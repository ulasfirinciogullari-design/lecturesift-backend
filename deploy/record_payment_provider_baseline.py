#!/usr/bin/env python3
"""Record the immutable payment-provider census before first OVH traffic.

The census is derived from the fixed root-only runtime configuration; provider
names are not supplied by an operator.  No credential value is written or
hashed into the output.  The output is bound to the finalized cutover proof and
created atomically before the first production start.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Final


EVIDENCE_ROOT: Final = Path("/var/lib/lecturesift/provider-cutover")
RUNTIME_ENV: Final = Path("/etc/lecturesift/runtime.env")
FINAL_PROOF: Final = EVIDENCE_ROOT / "provider-cutover.ok"
MAX_INPUT_BYTES: Final = 512 * 1024
ASSIGNMENT: Final = re.compile(r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
HEX32: Final = re.compile(r"[0-9a-f]{32}")
HEX40: Final = re.compile(r"[0-9a-f]{40}")
HEX64: Final = re.compile(r"[0-9a-f]{64}")
PROVIDER_KEYS: Final = {
    "iyzico": ("IYZICO_API_KEY", "IYZICO_SECRET_KEY"),
    "paytr": ("PAYTR_MERCHANT_ID", "PAYTR_MERCHANT_KEY", "PAYTR_MERCHANT_SALT"),
}
BASELINE_FIELDS: Final = frozenset(
    {
        "schema",
        "cutover_id",
        "release_revision",
        "provider_cutover_proof_sha256",
        "configuration_presence_sha256",
        "configured_providers",
        "recorded_at_utc",
    }
)


class ProviderBaselineError(RuntimeError):
    pass


def _private_file(path: Path, *, enforce_filesystem: bool) -> bytes:
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise ProviderBaselineError(f"required input is missing: {path.name}") from exc
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise ProviderBaselineError(f"input is not a regular file: {path.name}")
    if before.st_size <= 0 or before.st_size > MAX_INPUT_BYTES:
        raise ProviderBaselineError(f"input size is unsafe: {path.name}")
    if enforce_filesystem and (
        before.st_uid != 0
        or before.st_gid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) not in {0o400, 0o600}
    ):
        raise ProviderBaselineError(f"input is not root:root 0400/0600: {path.name}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if enforce_filesystem and (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_nlink,
            opened.st_uid,
            opened.st_gid,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        ) != (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_uid,
            before.st_gid,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ):
            raise ProviderBaselineError(f"input changed while opening: {path.name}")
        payload_parts: list[bytes] = []
        payload_size = 0
        while payload_size <= MAX_INPUT_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_INPUT_BYTES + 1 - payload_size))
            if not chunk:
                break
            payload_parts.append(chunk)
            payload_size += len(chunk)
        payload = b"".join(payload_parts)
        if enforce_filesystem and len(payload) != before.st_size:
            raise ProviderBaselineError(f"input changed while reading: {path.name}")
    finally:
        os.close(descriptor)
    return payload


def _literal(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def configured_providers_from_runtime(payload: bytes) -> tuple[tuple[str, ...], str]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ProviderBaselineError("runtime environment is not UTF-8") from exc
    if "\x00" in text:
        raise ProviderBaselineError("runtime environment contains a NUL byte")
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = ASSIGNMENT.fullmatch(line)
        if not match:
            raise ProviderBaselineError(f"unsupported runtime syntax at line {number}")
        key, raw = match.groups()
        if key in values:
            raise ProviderBaselineError(f"duplicate runtime key: {key}")
        values[key] = _literal(raw)

    providers: list[str] = []
    presence: dict[str, bool] = {}
    for provider, keys in sorted(PROVIDER_KEYS.items()):
        flags = [bool(values.get(key, "")) for key in keys]
        presence.update({key: flag for key, flag in zip(keys, flags)})
        if any(flags) and not all(flags):
            raise ProviderBaselineError(f"{provider} credential set is partial")
        if all(flags):
            providers.append(provider)
    if not providers:
        raise ProviderBaselineError("no configured payment provider was found")
    canonical = json.dumps(presence, sort_keys=True, separators=(",", ":")).encode("ascii")
    return tuple(providers), hashlib.sha256(canonical).hexdigest()


def _proof_fields(payload: bytes) -> dict[str, str]:
    try:
        text = payload.decode("ascii", errors="strict")
    except UnicodeError as exc:
        raise ProviderBaselineError("cutover proof is not ASCII") from exc
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line or "=" not in line:
            raise ProviderBaselineError("cutover proof contains a malformed line")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or key in result or not value:
            raise ProviderBaselineError("cutover proof contains an unsafe field")
        result[key] = value
    if (
        result.get("version") != "3"
        or result.get("status") != "provider-cutover-verified"
        or not HEX32.fullmatch(result.get("cutover_id", ""))
        or not HEX40.fullmatch(result.get("release_revision", ""))
    ):
        raise ProviderBaselineError("cutover proof identity is invalid")
    return result


def _baseline_payload(
    runtime_payload: bytes,
    proof_payload: bytes,
    *,
    expected_cutover_id: str,
    expected_revision: str,
    now: datetime,
) -> dict[str, object]:
    if not HEX32.fullmatch(expected_cutover_id) or not HEX40.fullmatch(expected_revision):
        raise ProviderBaselineError("expected cutover identity is invalid")
    proof = _proof_fields(proof_payload)
    if (
        proof["cutover_id"] != expected_cutover_id
        or proof["release_revision"] != expected_revision
    ):
        raise ProviderBaselineError("cutover proof does not match the requested baseline")
    providers, presence_digest = configured_providers_from_runtime(runtime_payload)
    return {
        "schema": "lecturesift-payment-provider-baseline-v1",
        "cutover_id": expected_cutover_id,
        "release_revision": expected_revision,
        "provider_cutover_proof_sha256": hashlib.sha256(proof_payload).hexdigest(),
        "configuration_presence_sha256": presence_digest,
        "configured_providers": list(providers),
        "recorded_at_utc": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _validate_existing_baseline(
    existing: object,
    proof_payload: bytes,
    *,
    expected_cutover_id: str,
    expected_revision: str,
) -> dict[str, object]:
    if not isinstance(existing, dict) or set(existing) != BASELINE_FIELDS:
        raise ProviderBaselineError("existing provider baseline contract is invalid")
    providers = existing.get("configured_providers")
    if (
        existing.get("schema") != "lecturesift-payment-provider-baseline-v1"
        or existing.get("cutover_id") != expected_cutover_id
        or existing.get("release_revision") != expected_revision
        or existing.get("provider_cutover_proof_sha256")
        != hashlib.sha256(proof_payload).hexdigest()
        or not HEX64.fullmatch(str(existing.get("configuration_presence_sha256", "")))
        or not isinstance(providers, list)
        or not providers
        or providers != sorted(set(providers))
        or any(provider not in PROVIDER_KEYS for provider in providers)
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
            str(existing.get("recorded_at_utc", "")),
        )
        is None
    ):
        raise ProviderBaselineError("existing provider baseline identity is invalid")
    return existing


def record_baseline(
    runtime_env: Path,
    cutover_proof: Path,
    output: Path,
    *,
    expected_cutover_id: str,
    expected_revision: str,
    now: datetime | None = None,
    enforce_filesystem: bool = True,
    require_existing: bool = False,
) -> dict[str, object]:
    if enforce_filesystem:
        expected_output = EVIDENCE_ROOT / f"payment-provider-baseline-{expected_cutover_id}.json"
        if runtime_env != RUNTIME_ENV or cutover_proof != FINAL_PROOF or output != expected_output:
            raise ProviderBaselineError("production baseline paths are fixed")
        if os.geteuid() != 0:
            raise ProviderBaselineError("provider baseline recording must run as root")
        root = os.lstat(EVIDENCE_ROOT)
        if (
            not stat.S_ISDIR(root.st_mode)
            or stat.S_ISLNK(root.st_mode)
            or root.st_uid != 0
            or root.st_gid != 0
            or stat.S_IMODE(root.st_mode) != 0o700
            or EVIDENCE_ROOT.resolve(strict=True) != EVIDENCE_ROOT
        ):
            raise ProviderBaselineError("cutover evidence root is unsafe")

    runtime_payload = _private_file(runtime_env, enforce_filesystem=enforce_filesystem)
    proof_payload = _private_file(cutover_proof, enforce_filesystem=enforce_filesystem)
    candidate = _baseline_payload(
        runtime_payload,
        proof_payload,
        expected_cutover_id=expected_cutover_id,
        expected_revision=expected_revision,
        now=now or datetime.now(timezone.utc),
    )
    if output.exists() or output.is_symlink():
        existing_payload = _private_file(output, enforce_filesystem=enforce_filesystem)
        try:
            existing = json.loads(existing_payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProviderBaselineError("existing provider baseline is invalid") from exc
        existing = _validate_existing_baseline(
            existing,
            proof_payload,
            expected_cutover_id=expected_cutover_id,
            expected_revision=expected_revision,
        )
        if require_existing:
            # The immutable file is the pre-traffic census. Current production
            # configuration may legitimately add a provider later; validate
            # that configuration above, but never rewrite history to match it.
            return existing
        comparable = {key: value for key, value in candidate.items() if key != "recorded_at_utc"}
        if {key: value for key, value in existing.items() if key != "recorded_at_utc"} != comparable:
            raise ProviderBaselineError("immutable provider baseline disagrees with current evidence")
        return existing

    if require_existing:
        raise ProviderBaselineError("immutable provider baseline is missing")

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".payment-provider-baseline-", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:  # pragma: no cover - Windows-only test portability
            os.chmod(temporary, 0o600)
        if enforce_filesystem:
            os.fchown(descriptor, 0, 0)
        payload = (json.dumps(candidate, sort_keys=True, separators=(",", ":")) + "\n").encode()
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if output.exists() or output.is_symlink():
            raise ProviderBaselineError("provider baseline appeared concurrently")
        os.link(temporary, output, follow_symlinks=False)
        temporary.unlink()
        if enforce_filesystem:
            directory_fd = os.open(
                output.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutover-id")
    parser.add_argument("--revision", required=True)
    parser.add_argument(
        "--check",
        action="store_true",
        help="require and validate the immutable baseline without creating it",
    )
    args = parser.parse_args()
    try:
        proof_payload = _private_file(FINAL_PROOF, enforce_filesystem=True)
        proof = _proof_fields(proof_payload)
        cutover_id = args.cutover_id or proof["cutover_id"]
        if args.cutover_id is None and not args.check:
            raise ProviderBaselineError("--cutover-id is required when recording")
        output = EVIDENCE_ROOT / f"payment-provider-baseline-{cutover_id}.json"
        result = record_baseline(
            RUNTIME_ENV,
            FINAL_PROOF,
            output,
            expected_cutover_id=cutover_id,
            expected_revision=args.revision,
            require_existing=args.check,
        )
    except (OSError, ProviderBaselineError) as exc:
        parser.exit(1, f"Payment provider baseline rejected: {exc}\n")
    print(
        "PAYMENT_PROVIDER_BASELINE_VERIFIED" if args.check else "PAYMENT_PROVIDER_BASELINE_RECORDED",
        end="",
    )
    print(
        f"|cutover_id={result['cutover_id']}"
        f"|revision={result['release_revision']}"
        f"|providers={','.join(result['configured_providers'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
