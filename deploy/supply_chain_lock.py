#!/usr/bin/env python3
"""Validate and fingerprint the production image supply-chain lock."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from pathlib import Path
import re
import sys


MANIFEST_NAME = "deploy/supply_chain.lock"
REQUIREMENTS_INPUT = "requirements.txt"
REQUIREMENTS_LOCK = "requirements.lock"
REQUIREMENTS_DEV = "requirements-dev.txt"
APPLICATION_DOCKERFILE = "Dockerfile"
PROXY_DOCKERFILE = "deploy/egress-proxy/Dockerfile"
FIELDS = (
    "version",
    "lock_generator",
    "python_target",
    "application_base",
    "proxy_base",
    "caddy_image",
    "postgres_image",
    "redis_image",
    "debian_snapshot",
    "requirements_input_sha256",
    "requirements_lock_sha256",
    "requirements_dev_sha256",
)
SHA256 = re.compile(r"[0-9a-f]{64}")
IMAGE = re.compile(r"[a-z0-9][a-z0-9._/-]*:[a-zA-Z0-9._-]+@sha256:[0-9a-f]{64}")
PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*==[^\s;\\]+ \\")
WINDOWS_DEV_PACKAGE = re.compile(
    r'colorama==[^\s;\\]+; sys_platform == "win32" \\'
)
HASH = re.compile(r"    --hash=sha256:[0-9a-f]{64}(?: \\)?")
MAX_MANIFEST_BYTES = 4096
MAX_REQUIREMENTS_BYTES = 2 * 1024 * 1024
APPLICATION_APT_PACKAGES = (
    "ffmpeg",
    "libgl1",
    "libglib2.0-0",
    "fonts-dejavu-core",
    "tesseract-ocr",
    "tesseract-ocr-osd",
    "tesseract-ocr-ara",
    "tesseract-ocr-chi-sim",
    "tesseract-ocr-deu",
    "tesseract-ocr-eng",
    "tesseract-ocr-fra",
    "tesseract-ocr-hin",
    "tesseract-ocr-ita",
    "tesseract-ocr-jpn",
    "tesseract-ocr-kor",
    "tesseract-ocr-por",
    "tesseract-ocr-rus",
    "tesseract-ocr-spa",
    "tesseract-ocr-tur",
)
PROXY_APT_PACKAGES = ("ca-certificates", "squid", "squidclient")


class SupplyChainError(RuntimeError):
    """The checked source cannot produce an admitted release image."""


def _read_regular(root: Path, relative: str, maximum: int) -> bytes:
    path = root / relative
    try:
        resolved = path.resolve(strict=True)
        details = path.lstat()
    except OSError as exc:
        raise SupplyChainError(f"missing supply-chain input: {relative}") from exc
    if not path.is_file() or path.is_symlink() or resolved != path:
        raise SupplyChainError(f"unsafe supply-chain input: {relative}")
    if details.st_size <= 0 or details.st_size > maximum:
        raise SupplyChainError(f"invalid supply-chain input size: {relative}")
    return path.read_bytes()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _requirements_sha256(payload: bytes) -> str:
    # Git stores text with LF while Windows worktrees may expose CRLF. Both
    # represent the same reviewed dependency input, so bind its canonical bytes.
    canonical = payload.replace(b"\r\n", b"\n")
    if b"\r" in canonical:
        raise SupplyChainError("requirements input contains a lone carriage return")
    return _sha256(canonical)


def _parse_manifest(payload: bytes) -> dict[str, str]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise SupplyChainError("supply-chain manifest is not UTF-8") from exc
    values: dict[str, str] = {}
    order: list[str] = []
    for line in lines:
        if not line or "=" not in line or "\x00" in line:
            raise SupplyChainError("invalid supply-chain manifest syntax")
        key, value = line.split("=", 1)
        if key not in FIELDS or key in values or not value:
            raise SupplyChainError("invalid supply-chain manifest field")
        values[key] = value
        order.append(key)
    if tuple(order) != FIELDS:
        raise SupplyChainError("supply-chain manifest fields are incomplete or unordered")
    if values["version"] != "1":
        raise SupplyChainError("unsupported supply-chain manifest version")
    if not re.fullmatch(r"uv-[0-9]+(?:\.[0-9]+){2}", values["lock_generator"]):
        raise SupplyChainError("invalid lock generator identity")
    if values["python_target"] != "cp312-manylinux_2_17_x86_64":
        raise SupplyChainError("unexpected Python lock target")
    for field in (
        "application_base", "proxy_base", "caddy_image", "postgres_image", "redis_image"
    ):
        if not IMAGE.fullmatch(values[field]):
            raise SupplyChainError(f"base image is not immutable: {field}")
    for field in (
        "requirements_input_sha256",
        "requirements_lock_sha256",
        "requirements_dev_sha256",
    ):
        if not SHA256.fullmatch(values[field]):
            raise SupplyChainError(f"invalid digest: {field}")
    if not re.fullmatch(r"20[0-9]{6}T[0-9]{6}Z", values["debian_snapshot"]):
        raise SupplyChainError("invalid Debian snapshot timestamp")
    return values


def _docker_base(payload: bytes, relative: str) -> str:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise SupplyChainError(f"Dockerfile is not UTF-8: {relative}") from exc
    bases = []
    for line in lines:
        instruction = re.match(r"^[ \t]*([A-Za-z]+)(?:[ \t]+(.*))?$", line)
        if instruction and instruction.group(1).lower() == "from":
            argument = (instruction.group(2) or "").strip()
            if not argument:
                raise SupplyChainError(f"empty base image instruction: {relative}")
            bases.append(argument)
    if len(bases) != 1:
        raise SupplyChainError(f"expected exactly one base image: {relative}")
    return bases[0]


def _validate_hashed_requirements(
    payload: bytes, *, required_include: str | None = None
) -> None:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise SupplyChainError("requirements lock is not UTF-8") from exc
    if any(
        token in line.lower()
        for line in lines
        for token in ("http://", "https://", " @ ", "--index-url", "--extra-index-url")
    ):
        raise SupplyChainError("requirements lock contains a remote or direct reference")
    package_count = 0
    hashes_for_package = 0
    include_count = 0
    for line in lines:
        if not line or line.startswith("#"):
            continue
        if required_include is not None and line == f"-r {required_include}":
            if include_count or package_count or hashes_for_package:
                raise SupplyChainError("requirements include is duplicated or misplaced")
            include_count += 1
            continue
        if PACKAGE.fullmatch(line) or (
            required_include is not None and WINDOWS_DEV_PACKAGE.fullmatch(line)
        ):
            if package_count and not hashes_for_package:
                raise SupplyChainError("requirements entry has no artifact hash")
            package_count += 1
            hashes_for_package = 0
            continue
        if HASH.fullmatch(line):
            if not package_count:
                raise SupplyChainError("orphan requirements artifact hash")
            hashes_for_package += 1
            continue
        raise SupplyChainError("requirements lock is not fully pinned and hashed")
    if not package_count or not hashes_for_package:
        raise SupplyChainError("requirements lock is empty or unhashed")
    if required_include is not None and include_count != 1:
        raise SupplyChainError("requirements include is missing")


def _validate_apt_snapshot(
    payload: bytes,
    suite: str,
    timestamp: str,
    expected_packages: tuple[str, ...],
    *,
    external_ca_image: str | None = None,
    runtime_base_image: str | None = None,
) -> None:
    try:
        source = payload.decode("utf-8")
    except UnicodeError as exc:
        raise SupplyChainError("Dockerfile is not UTF-8") from exc
    if re.search(
        r"(?im)^[ \t]*#[ \t]*(?:syntax|escape|check)[ \t]*=", source
    ):
        raise SupplyChainError("Dockerfile parser directives are not locked")
    if re.search(r"(?im)^[ \t]*SHELL(?:[ \t]|$)", source):
        raise SupplyChainError("Dockerfile custom shells are not locked")
    repository_lines = (
        "Types: deb",
        f"URIs: https://snapshot.debian.org/archive/debian/{timestamp}",
        f"Suites: {suite} {suite}-updates",
        "Components: main",
        "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg",
        "Check-Valid-Until: no",
        "Types: deb",
        f"URIs: https://snapshot.debian.org/archive/debian-security/{timestamp}",
        f"Suites: {suite}-security",
        "Components: main",
        "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg",
        "Check-Valid-Until: no",
    )
    declared_repository_lines = tuple(
        match.group(1)
        for match in re.finditer(
            r"'((?:Types|URIs|Suites|Components|Signed-By|Check-Valid-Until):[^'\r\n]*)'",
            source,
        )
    )
    if declared_repository_lines != repository_lines:
        raise SupplyChainError("Dockerfile does not use the locked Debian snapshot")

    allowed_urls = (
        f"https://snapshot.debian.org/archive/debian/{timestamp}",
        f"https://snapshot.debian.org/archive/debian-security/{timestamp}",
    )
    observed_urls = tuple(re.findall(r"https?://[^\s'\"\\;]+", source))
    if observed_urls != allowed_urls:
        raise SupplyChainError("Dockerfile contains a mutable or additional repository URL")

    source_file_lines = [
        line.strip().removesuffix("\\").rstrip()
        for line in source.splitlines()
        if "/etc/apt/sources" in line
    ]
    if source_file_lines != [
        "rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*;",
        "> /etc/apt/sources.list.d/debian.sources;",
    ]:
        raise SupplyChainError("Dockerfile rewrites or adds an unreviewed APT source")

    expected_ca_copy = None
    if external_ca_image is not None:
        expected_ca_copy = (
            f"COPY --from={external_ca_image} "
            "/etc/ssl/certs/ca-certificates.crt "
            "/etc/ssl/certs/ca-certificates.crt"
        )
    copy_or_add_lines = [
        line.strip()
        for line in source.splitlines()
        if re.match(r"(?i)^[ \t]*(?:COPY|ADD)(?:[ \t]|$)", line)
    ]
    if (
        expected_ca_copy is not None
        and expected_ca_copy in source
        and source.index(expected_ca_copy) > source.index("RUN set -eux;")
    ):
        raise SupplyChainError("Dockerfile CA bootstrap image is copied too late")
    if expected_ca_copy is not None:
        expected_copy_or_add_lines = [
            expected_ca_copy,
            "COPY squid.conf /etc/squid/squid.conf",
        ]
        if copy_or_add_lines != expected_copy_or_add_lines:
            raise SupplyChainError("Dockerfile proxy copy instructions do not match the lock")
    elif any(
        re.match(r"(?i)^COPY[ \t]+--from=", line) for line in copy_or_add_lines
    ):
        raise SupplyChainError("Dockerfile CA bootstrap image does not match the lock")
    if external_ca_image is not None and source.count(
        "/etc/ssl/certs/ca-certificates.crt"
    ) != 6:
        raise SupplyChainError("Dockerfile CA bootstrap path is not exact")

    logical_source = re.sub(r"\\\r?\n", " ", source)
    logical_source = re.sub(r"[ \t]+", " ", logical_source)
    logical_lines = [
        re.sub(r"\s*;\s*", "; ", line.strip()).rstrip()
        for line in logical_source.splitlines()
        if line.strip()
    ]
    repository_arguments = [
        *(f"'{line}'" for line in repository_lines[:6]),
        "''",
        *(f"'{line}'" for line in repository_lines[6:]),
    ]
    expected_update = "apt-get update --error-on=any; "
    expected_install = (
        "apt-get install -y --no-install-recommends "
        + " ".join(expected_packages)
        + "; "
    )
    expected_prelude = ""
    if external_ca_image is not None:
        expected_prelude = (
            "test -s /etc/ssl/certs/ca-certificates.crt; "
            "test ! -L /etc/ssl/certs/ca-certificates.crt; "
        )
        tls_options = (
            "-o Acquire::https::CAInfo=/etc/ssl/certs/ca-certificates.crt "
            "-o Acquire::https::Verify-Peer=true "
            "-o Acquire::https::Verify-Host=true "
        )
        expected_update = (
            "apt-get "
            + tls_options
            + "update --error-on=any; "
        )
        expected_install = (
            "apt-get "
            + tls_options
            + "install -y --no-install-recommends "
            + " ".join(expected_packages)
            + "; "
        )
    expected_apt_run = (
        "RUN set -eux; "
        + expected_prelude
        + "rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*; "
        + "printf '%s\\n' "
        + " ".join(repository_arguments)
        + " > /etc/apt/sources.list.d/debian.sources; "
        + expected_update
        + expected_install
        + "rm -rf /var/lib/apt/lists/*"
    )
    run_lines = [
        line for line in logical_lines if re.match(r"(?i)^RUN(?:\s|$)", line)
    ]
    if not run_lines or run_lines[0] != expected_apt_run:
        raise SupplyChainError("Dockerfile APT provisioning command is not the locked command")
    if external_ca_image is not None:
        if runtime_base_image is None or expected_ca_copy is None:
            raise SupplyChainError("Dockerfile proxy instruction lock is incomplete")
        revision_value = chr(36) + "{LECTURESIFT_BUILD_REVISION}"
        lock_value = chr(36) + "{LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256}"
        expected_proxy_instructions = [
            f"FROM {runtime_base_image}",
            "ARG LECTURESIFT_BUILD_REVISION=unknown",
            "ARG LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256=unknown",
            (
                f'LABEL org.opencontainers.image.revision="{revision_value}" '
                f'io.lecturesift.supply-chain-lock-sha256="{lock_value}"'
            ),
            (
                f'ENV LECTURESIFT_BUILD_REVISION="{revision_value}" '
                f'LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256="{lock_value}"'
            ),
            expected_ca_copy,
            expected_apt_run,
            "COPY squid.conf /etc/squid/squid.conf",
            "USER proxy",
            "EXPOSE 3128",
            'CMD ["squid", "-N", "-f", "/etc/squid/squid.conf"]',
        ]
        if logical_lines != expected_proxy_instructions:
            raise SupplyChainError(
                "Dockerfile proxy instruction sequence does not match the lock"
            )
    forbidden_package_commands = re.findall(
        r"(?:^|\bRUN\s+|[;&|]\s*)(apt|apt-key|add-apt-repository|dpkg)(?:\s|$)",
        logical_source,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    forbidden_transport_downgrades = (
        "http://",
        "allow-insecure",
        "allowweak",
        "allow-downgrade-to-insecure",
        "--allow-unauthenticated",
        "check-valid-until=false",
        "check-date: no",
        "check-date=no",
        "trusted: yes",
        "trusted=yes",
        "verify-peer=false",
        "verify-host=false",
    )
    if (
        logical_source.lower().count("apt-get") != 2
        or forbidden_package_commands
        or any(token in source.lower() for token in forbidden_transport_downgrades)
    ):
        raise SupplyChainError("Dockerfile APT install block does not match the lock contract")
    if logical_source.count("rm -rf /var/lib/apt/lists/*") != 1:
        raise SupplyChainError("Dockerfile APT metadata cleanup does not match the lock contract")
    if logical_source.index("snapshot.debian.org") > logical_source.index(
        expected_update.strip()
    ):
        raise SupplyChainError("Debian snapshot must be configured before apt metadata is read")


def _strip_yaml_comment(line: str) -> str:
    """Remove a YAML comment without treating hashes inside quotes as comments."""
    single_quoted = False
    double_quoted = False
    escaped = False
    for index, character in enumerate(line):
        if double_quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                double_quoted = False
            continue
        if single_quoted:
            if character == "'":
                if index + 1 < len(line) and line[index + 1] == "'":
                    continue
                single_quoted = False
            continue
        if character == "'":
            single_quoted = True
        elif character == '"':
            double_quoted = True
        elif character == "#":
            return line[:index]
    if single_quoted or double_quoted:
        raise SupplyChainError("compose file contains an unterminated quoted scalar")
    return line


def _validate_compose_images(payload: bytes, values: dict[str, str]) -> None:
    try:
        source = payload.decode("utf-8")
    except UnicodeError as exc:
        raise SupplyChainError("compose file is not UTF-8") from exc
    if "\t" in source:
        raise SupplyChainError("compose file contains ambiguous tab indentation")

    parsed_lines: list[tuple[int, str]] = []
    literal_images: list[str] = []
    mapping = re.compile(r"(?P<key><<|[A-Za-z_][A-Za-z0-9_.-]*)[ ]*:[ ]*(?P<value>.*)\Z")
    for raw_line in source.splitlines():
        uncommented = _strip_yaml_comment(raw_line).rstrip()
        if not uncommented.strip():
            continue
        indent = len(uncommented) - len(uncommented.lstrip(" "))
        content = uncommented[indent:]
        parsed_lines.append((indent, content))
        item = mapping.fullmatch(content)
        if item and item.group("key") == "image":
            value = item.group("value").strip()
            if not value:
                raise SupplyChainError("compose image mapping is empty")
            literal_images.append(value)

    service_headers = [
        index
        for index, (indent, content) in enumerate(parsed_lines)
        if indent == 0 and mapping.fullmatch(content)
        and mapping.fullmatch(content).group("key") == "services"
    ]
    if len(service_headers) != 1:
        raise SupplyChainError("compose file must contain one services mapping")
    services_index = service_headers[0]
    services_header = mapping.fullmatch(parsed_lines[services_index][1])
    assert services_header is not None
    if services_header.group("value").strip():
        raise SupplyChainError("compose services mapping cannot use an alias or flow value")

    end_index = len(parsed_lines)
    for index in range(services_index + 1, len(parsed_lines)):
        if parsed_lines[index][0] == 0:
            end_index = index
            break

    service_keys: dict[str, dict[str, str]] = {}
    current_service: str | None = None
    for indent, content in parsed_lines[services_index + 1:end_index]:
        if indent == 2:
            header = mapping.fullmatch(content)
            if header is None or header.group("key") == "<<" or header.group("value").strip():
                raise SupplyChainError("compose service declaration is not a plain mapping")
            current_service = header.group("key")
            if current_service in service_keys:
                raise SupplyChainError("compose file contains a duplicate service key")
            service_keys[current_service] = {}
        elif indent == 4:
            if current_service is None:
                raise SupplyChainError("compose service field has no service mapping")
            item = mapping.fullmatch(content)
            if item is None:
                raise SupplyChainError("compose service mapping contains unsupported syntax")
            key = item.group("key")
            if key in service_keys[current_service]:
                raise SupplyChainError("compose service mapping contains a duplicate key")
            service_keys[current_service][key] = item.group("value").strip()
        elif indent < 6:
            raise SupplyChainError("compose services mapping has ambiguous indentation")

    protected_images = {
        "caddy": values["caddy_image"],
        "postgres": values["postgres_image"],
        "redis": values["redis_image"],
    }
    for service, expected_image in protected_images.items():
        fields = service_keys.get(service)
        if fields is None:
            raise SupplyChainError(f"compose service is missing: {service}")
        if "<<" in fields:
            raise SupplyChainError(f"compose image service cannot use a merge alias: {service}")
        if fields.get("image") != expected_image:
            raise SupplyChainError(f"runtime image does not match the lock: {service}_image")

    expected_literals = Counter(
        {
            "lecturesift-backend:local": 1,
            "lecturesift-egress-proxy:local": 1,
            values["caddy_image"]: 1,
            values["postgres_image"]: 1,
            values["redis_image"]: 1,
        }
    )
    if Counter(literal_images) != expected_literals:
        raise SupplyChainError("compose file contains an unexpected or duplicate image mapping")


def validate(root: Path) -> str:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise SupplyChainError("supply-chain root is not a directory")
    manifest_payload = _read_regular(root, MANIFEST_NAME, MAX_MANIFEST_BYTES)
    values = _parse_manifest(manifest_payload)
    requirements_input = _read_regular(root, REQUIREMENTS_INPUT, MAX_REQUIREMENTS_BYTES)
    requirements_lock = _read_regular(root, REQUIREMENTS_LOCK, MAX_REQUIREMENTS_BYTES)
    requirements_dev = _read_regular(root, REQUIREMENTS_DEV, MAX_REQUIREMENTS_BYTES)
    _validate_hashed_requirements(requirements_lock)
    _validate_hashed_requirements(
        requirements_dev, required_include=REQUIREMENTS_LOCK
    )
    if _requirements_sha256(requirements_input) != values["requirements_input_sha256"]:
        raise SupplyChainError("requirements input changed without regenerating the lock")
    if _requirements_sha256(requirements_lock) != values["requirements_lock_sha256"]:
        raise SupplyChainError("requirements lock digest mismatch")
    if _requirements_sha256(requirements_dev) != values["requirements_dev_sha256"]:
        raise SupplyChainError("development requirements digest mismatch")
    application_dockerfile = _read_regular(
        root, APPLICATION_DOCKERFILE, MAX_MANIFEST_BYTES * 8
    )
    proxy_dockerfile = _read_regular(root, PROXY_DOCKERFILE, MAX_MANIFEST_BYTES * 8)
    application_base = _docker_base(application_dockerfile, APPLICATION_DOCKERFILE)
    proxy_base = _docker_base(proxy_dockerfile, PROXY_DOCKERFILE)
    if application_base != values["application_base"]:
        raise SupplyChainError("application base image does not match the lock")
    if proxy_base != values["proxy_base"]:
        raise SupplyChainError("proxy base image does not match the lock")
    _validate_apt_snapshot(
        application_dockerfile,
        "trixie",
        values["debian_snapshot"],
        APPLICATION_APT_PACKAGES,
    )
    _validate_apt_snapshot(
        proxy_dockerfile,
        "bookworm",
        values["debian_snapshot"],
        PROXY_APT_PACKAGES,
        external_ca_image=values["application_base"],
        runtime_base_image=values["proxy_base"],
    )
    compose = _read_regular(root, "compose.yaml", MAX_REQUIREMENTS_BYTES)
    _validate_compose_images(compose, values)
    return _sha256(manifest_payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--print-digest", action="store_true")
    args = parser.parse_args()
    try:
        digest = validate(args.root)
    except (SupplyChainError, OSError, ValueError) as exc:
        print(f"Supply-chain lock validation failed: {exc}", file=sys.stderr)
        return 1
    if args.print_digest:
        print(digest)
    else:
        print("SUPPLY_CHAIN_LOCK_VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
