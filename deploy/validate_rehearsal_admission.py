"""Validate the root-only exact-rehearsal admission for production startup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import threading
import time


ADMISSION_ROOT = Path("/var/lib/lecturesift/rehearsal-admissions")
CANDIDATE_ROOT = Path("/var/lib/lecturesift/release-candidates")
TRUSTED_CONTROLLER = Path("/usr/local/sbin/lecturesift-exact-rehearsal-controller")
TRUSTED_STAGE_CONTROLLER = Path("/usr/local/sbin/lecturesift-release-stage-controller")
REVISION = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z")
ADMISSION_VERSION = "5"
REHEARSAL_RESULT_FORMAT = "lecturesift-exact-rehearsal-result-v3"
GIT_COMMAND_TIMEOUT_SECONDS = 60
GIT_ARCHIVE_TIMEOUT_SECONDS = 360
GIT_ATTRIBUTE_ADDRESS_SPACE_BYTES = 768 * 1024 * 1024
GIT_ARCHIVE_ADDRESS_SPACE_BYTES = 3 * 1024 * 1024 * 1024
PRLIMIT_PATH = "/usr/bin/prlimit"
GIT_PATH = "/usr/bin/git"
PROCESS_REAP_TIMEOUT_SECONDS = 5
ARTIFACT_DIGEST_FIELDS = (
    "application_e2e_sha256",
    "environment_proof_sha256",
    "formats_e2e_sha256",
    "purge_e2e_sha256",
    "r2_negative_capability_sha256",
    "schema_after_e2e_sha256",
    "schema_transition_sha256",
    "target_after_e2e_manifest_sha256",
    "target_migrated_manifest_sha256",
)


class AdmissionError(RuntimeError):
    pass


def _bounded_git_command(address_space_bytes: int, *arguments: str) -> list[str]:
    if address_space_bytes <= 0:
        raise AdmissionError("invalid Git address-space bound")
    return [
        PRLIMIT_PATH,
        f"--as={address_space_bytes}",
        "--",
        GIT_PATH,
        *arguments,
    ]


def _kill_processes(processes: tuple[subprocess.Popen[bytes], ...]) -> None:
    for process in processes:
        if process.poll() is not None:
            continue
        try:
            process.kill()
        except OSError:
            pass


def _reap_processes(processes: tuple[subprocess.Popen[bytes], ...]) -> None:
    for process in processes:
        try:
            process.wait(timeout=PROCESS_REAP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            _kill_processes((process,))
            try:
                process.wait(timeout=PROCESS_REAP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                pass


def _start_process_watchdog(
    processes: tuple[subprocess.Popen[bytes], ...], timeout_seconds: int
) -> tuple[threading.Timer, threading.Event, float]:
    expired = threading.Event()

    def expire() -> None:
        expired.set()
        _kill_processes(processes)

    deadline = time.monotonic() + timeout_seconds
    timer = threading.Timer(timeout_seconds, expire)
    timer.daemon = True
    timer.start()
    return timer, expired, deadline


def _stop_process_watchdog(timer: threading.Timer) -> None:
    timer.cancel()
    timer.join(timeout=PROCESS_REAP_TIMEOUT_SECONDS)


def _wait_before_deadline(
    process: subprocess.Popen[bytes], deadline: float
) -> int:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(process.args, 0)
    return process.wait(timeout=remaining)


def _private_file(path: Path, parent: Path) -> None:
    parent_details = parent.lstat()
    if (
        not stat.S_ISDIR(parent_details.st_mode)
        or stat.S_ISLNK(parent_details.st_mode)
        or parent_details.st_uid != 0
        or stat.S_IMODE(parent_details.st_mode) != 0o700
        or parent.resolve(strict=True) != parent
    ):
        raise AdmissionError(f"unsafe evidence parent: {parent}")
    details = path.lstat()
    if (
        not stat.S_ISREG(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or details.st_uid != 0
        or stat.S_IMODE(details.st_mode) not in {0o400, 0o600}
        or path.resolve(strict=True) != path
        or path.parent != parent
    ):
        raise AdmissionError(f"unsafe evidence file: {path}")


def _root_immutable_file(path: Path) -> None:
    details = path.lstat()
    if (
        not stat.S_ISREG(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or details.st_uid != 0
        or stat.S_IMODE(details.st_mode) & 0o022
        or path.resolve(strict=True) != path
    ):
        raise AdmissionError(f"unsafe trusted controller: {path}")


def _fields(path: Path, allowed: set[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise AdmissionError(f"cannot read {path}") from exc
    for line in lines:
        if not line or "=" not in line:
            raise AdmissionError(f"invalid evidence syntax: {path}")
        key, value = line.split("=", 1)
        if key not in allowed or key in values or "\x00" in value:
            raise AdmissionError(f"invalid evidence field: {key}")
        values[key] = value
    return values


def _git_info_attributes_path(
    root: Path, git_environment: dict[str, str]
) -> Path:
    common_raw = subprocess.run(
        _bounded_git_command(
            GIT_ATTRIBUTE_ADDRESS_SPACE_BYTES,
            "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir",
        ),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=git_environment,
        timeout=GIT_COMMAND_TIMEOUT_SECONDS,
    ).stdout.strip()
    info_raw = subprocess.run(
        _bounded_git_command(
            GIT_ATTRIBUTE_ADDRESS_SPACE_BYTES,
            "-C", str(root), "rev-parse", "--path-format=absolute",
            "--git-path", "info/attributes",
        ),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=git_environment,
        timeout=GIT_COMMAND_TIMEOUT_SECONDS,
    ).stdout.strip()
    if not common_raw or not info_raw:
        raise AdmissionError("cannot resolve Git attribute metadata")
    common_dir = Path(common_raw).resolve(strict=True)
    info_attributes = Path(info_raw)
    try:
        normalized_info = info_attributes.parent.resolve(strict=True) / info_attributes.name
    except OSError as exc:
        raise AdmissionError("cannot resolve Git attribute metadata") from exc
    if normalized_info != common_dir / "info" / "attributes":
        raise AdmissionError("Git attribute metadata escaped the repository")
    if info_attributes.exists() or info_attributes.is_symlink():
        raise AdmissionError("Git info export attributes are forbidden")
    return normalized_info


def _assert_no_effective_git_export_attributes(
    root: Path, revision: str, git_environment: dict[str, str]
) -> None:
    info_attributes_before = _git_info_attributes_path(root, git_environment)
    tree_process = subprocess.Popen(
        _bounded_git_command(
            GIT_ATTRIBUTE_ADDRESS_SPACE_BYTES,
            "-C", str(root), "ls-tree", "-rzt", "--name-only", revision,
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=git_environment,
    )
    attributes_process: subprocess.Popen[bytes] | None = None
    processes: tuple[subprocess.Popen[bytes], ...] = (tree_process,)
    watchdog: threading.Timer | None = None
    watchdog_expired: threading.Event | None = None
    deadline = 0.0
    try:
        if tree_process.stdout is None:
            raise AdmissionError("cannot enumerate Git attribute paths")
        attributes_process = subprocess.Popen(
            _bounded_git_command(
                GIT_ATTRIBUTE_ADDRESS_SPACE_BYTES,
                "-c", "core.attributesFile=/dev/null", "-C", str(root),
                "check-attr", f"--source={revision}", "-z", "--stdin", "--all",
            ),
            stdin=tree_process.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=git_environment,
        )
        processes = (attributes_process, tree_process)
        watchdog, watchdog_expired, deadline = _start_process_watchdog(
            processes, GIT_COMMAND_TIMEOUT_SECONDS
        )
        tree_process.stdout.close()
        if attributes_process.stdout is None:
            raise AdmissionError("cannot inspect effective Git attributes")
        pending = b""
        fields: list[bytes] = []
        records = 0
        while chunk := attributes_process.stdout.read(65536):
            pending += chunk
            if len(pending) > 1_114_112:
                raise AdmissionError("Git attribute record exceeds the admission bound")
            while b"\0" in pending:
                field, pending = pending.split(b"\0", 1)
                fields.append(field)
                if len(fields) != 3:
                    continue
                raw_path, attribute, _value = fields
                if (
                    not raw_path
                    or not attribute
                    or len(raw_path) > 1024 * 1024
                    or len(attribute) > 1024 * 1024
                    or len(_value) > 1024 * 1024
                ):
                    raise AdmissionError("Git attribute record is malformed")
                records += 1
                if records > 200_000:
                    raise AdmissionError("Git attribute inventory exceeds the admission bound")
                if attribute in {b"export-ignore", b"export-subst"}:
                    raise AdmissionError("effective Git export attributes are forbidden")
                fields.clear()
        if watchdog_expired.is_set():
            raise AdmissionError("Git attribute inspection timed out")
        if pending or fields:
            raise AdmissionError("Git attribute output is truncated")
        if (
            _wait_before_deadline(attributes_process, deadline) != 0
            or _wait_before_deadline(tree_process, deadline) != 0
        ):
            raise AdmissionError("Git attribute inspection failed")
        if watchdog_expired.is_set():
            raise AdmissionError("Git attribute inspection timed out")
        if _git_info_attributes_path(root, git_environment) != info_attributes_before:
            raise AdmissionError("Git attribute metadata changed during inspection")
    except subprocess.TimeoutExpired as exc:
        raise AdmissionError("Git attribute inspection timed out") from exc
    finally:
        if watchdog is not None:
            _stop_process_watchdog(watchdog)
        _kill_processes(processes)
        _reap_processes(processes)


def _git_tree_digest(root: Path, revision: str) -> str:
    git_environment = os.environ.copy()
    for key in tuple(git_environment):
        if key.startswith("GIT_"):
            git_environment.pop(key)
    git_environment["GIT_ATTR_NOSYSTEM"] = "1"
    head = subprocess.run(
        _bounded_git_command(
            GIT_ATTRIBUTE_ADDRESS_SPACE_BYTES,
            "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}",
        ),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=git_environment,
        timeout=GIT_COMMAND_TIMEOUT_SECONDS,
    ).stdout.strip()
    dirty = subprocess.run(
        _bounded_git_command(
            GIT_ATTRIBUTE_ADDRESS_SPACE_BYTES,
            "-C", str(root), "status", "--porcelain=v1", "--untracked-files=all",
        ),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=git_environment,
        timeout=GIT_COMMAND_TIMEOUT_SECONDS,
    ).stdout
    if head != revision or dirty:
        raise AdmissionError("active checkout is not the admitted clean revision")
    _assert_no_effective_git_export_attributes(root, revision, git_environment)
    process = subprocess.Popen(
        _bounded_git_command(
            GIT_ARCHIVE_ADDRESS_SPACE_BYTES,
            "-c", "core.attributesFile=/dev/null", "-C", str(root),
            "archive", "--format=tar", revision,
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=git_environment,
    )
    if process.stdout is None:
        process.kill()
        raise AdmissionError("cannot stream the admitted Git tree")
    watchdog, watchdog_expired, deadline = _start_process_watchdog(
        (process,), GIT_ARCHIVE_TIMEOUT_SECONDS
    )
    inventory: list[tuple[str, str, int, str]] = []
    seen: set[str] = set()
    entries = 0
    expanded = 0
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as source:
            for member in source:
                entries += 1
                expanded += member.size
                name = member.name.rstrip("/")
                if (
                    entries > 100_000
                    or expanded > 2 * 1024 * 1024 * 1024
                    or member.size < 0
                    or member.size > 1024 * 1024 * 1024
                    or not name
                    or name in seen
                ):
                    raise AdmissionError("Git archive exceeds the admission bounds")
                seen.add(name)
                if member.isdir():
                    inventory.append((name, "d", 1, ""))
                elif member.isfile():
                    stream = source.extractfile(member)
                    if stream is None:
                        raise AdmissionError("Git archive file cannot be read")
                    digest = hashlib.sha256()
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                    inventory.append(
                        (name, "f", int(bool(member.mode & 0o111)), digest.hexdigest())
                    )
                else:
                    raise AdmissionError("Git archive contains an unsupported entry")
        if watchdog_expired.is_set():
            raise AdmissionError("Git archive generation timed out")
        if _wait_before_deadline(process, deadline) != 0:
            raise AdmissionError("Git archive generation failed")
        if watchdog_expired.is_set():
            raise AdmissionError("Git archive generation timed out")
    except subprocess.TimeoutExpired as exc:
        raise AdmissionError("Git archive generation timed out") from exc
    except BaseException as exc:
        if watchdog_expired.is_set():
            raise AdmissionError("Git archive generation timed out") from exc
        raise
    finally:
        _stop_process_watchdog(watchdog)
        _kill_processes((process,))
        _reap_processes((process,))
    if entries == 0:
        raise AdmissionError("Git archive is empty")
    inventory.sort()
    payload = json.dumps(inventory, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(payload).hexdigest()


def _image(image: str) -> tuple[str, str, str, set[str]]:
    raw = subprocess.run(
        ["docker", "image", "inspect", image],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    ).stdout
    document = json.loads(raw)
    if not isinstance(document, list) or len(document) != 1:
        raise AdmissionError(f"unexpected Docker image document: {image}")
    item = document[0]
    return (
        item.get("Id", ""),
        (item.get("Config", {}).get("Labels") or {}).get("org.opencontainers.image.revision", ""),
        (item.get("Config", {}).get("Labels") or {}).get(
            "io.lecturesift.supply-chain-lock-sha256", ""
        ),
        set(item.get("Config", {}).get("Env") or []),
    )


def validate(root: Path, revision: str) -> None:
    if os.geteuid() != 0:
        raise AdmissionError("root is required")
    if not REVISION.fullmatch(revision):
        raise AdmissionError("invalid expected revision")
    admission_path = ADMISSION_ROOT / f"{revision}.ok"
    candidate_path = CANDIDATE_ROOT / f"{revision}.ok"
    _private_file(admission_path, ADMISSION_ROOT)
    _private_file(candidate_path, CANDIDATE_ROOT)
    admission_allowed = {
        "version", "status", "revision", "candidate_evidence_sha256", "staged_app_image_id",
        "staged_proxy_image_id", "rehearsed_local_app_image_id",
        "rehearsed_local_proxy_image_id", "candidate_tree_sha256",
        "rehearsed_local_source_tree_sha256", "source_tree_equivalent",
        "base_postgres_unchanged", "base_redis_unchanged", "roles_unchanged",
        "database_inventory_unchanged", "grants_unchanged",
        "containers_unchanged", "volumes_unchanged", "networks_unchanged",
        "listeners_unchanged",
        "trusted_controller_sha256", "trusted_controller_handoff_sha256",
        "trusted_stage_controller_sha256", "trusted_stage_handoff_sha256",
        "trusted_stage_handoff_nonce",
        "rehearsal_result_format", "rehearsal_run_id", "rehearsal_ai_provider",
        "rehearsal_artifact_set_sha256", *ARTIFACT_DIGEST_FIELDS,
    }
    candidate_allowed = {
        "status", "revision", "archive_sha256", "bundle_sha256", "tree_sha256",
        "app_image_id", "proxy_image_id", "archive_equals_bundle_commit",
        "containers_unchanged", "listeners_unchanged",
        "trusted_stage_controller_sha256", "trusted_stage_handoff_sha256",
        "trusted_stage_handoff_nonce",
    }
    admission = _fields(admission_path, admission_allowed)
    candidate = _fields(candidate_path, candidate_allowed)
    if set(admission) != admission_allowed or set(candidate) != candidate_allowed:
        raise AdmissionError("evidence fields are incomplete")
    if (
        admission["version"] != ADMISSION_VERSION
        or admission["status"] != "verified"
        or admission["revision"] != revision
    ):
        raise AdmissionError("admission identity mismatch")
    if any(
        not SHA256.fullmatch(admission.get(field, ""))
        for field in (
            "trusted_controller_sha256", "trusted_controller_handoff_sha256",
            "trusted_stage_controller_sha256", "trusted_stage_handoff_sha256",
        )
    ):
        raise AdmissionError("trusted-controller admission evidence is malformed")
    _root_immutable_file(TRUSTED_CONTROLLER)
    _root_immutable_file(TRUSTED_STAGE_CONTROLLER)
    if (
        hashlib.sha256(TRUSTED_CONTROLLER.read_bytes()).hexdigest()
        != admission["trusted_controller_sha256"]
    ):
        raise AdmissionError("trusted controller changed after rehearsal")
    stage_controller_sha256 = hashlib.sha256(
        TRUSTED_STAGE_CONTROLLER.read_bytes()
    ).hexdigest()
    if (
        admission["trusted_stage_controller_sha256"] != stage_controller_sha256
        or candidate["trusted_stage_controller_sha256"] != stage_controller_sha256
        or admission["trusted_stage_handoff_sha256"]
        != candidate["trusted_stage_handoff_sha256"]
        or admission["trusted_stage_handoff_nonce"]
        != candidate["trusted_stage_handoff_nonce"]
        or not re.fullmatch(r"[0-9a-f]{32}", admission["trusted_stage_handoff_nonce"])
    ):
        raise AdmissionError("trusted stage handoff/controller evidence is not bound")
    if candidate["status"] != "verified" or candidate["revision"] != revision:
        raise AdmissionError("candidate identity mismatch")
    true_fields = {
        "source_tree_equivalent", "base_postgres_unchanged", "base_redis_unchanged",
        "roles_unchanged", "containers_unchanged", "volumes_unchanged",
        "database_inventory_unchanged", "grants_unchanged",
        "networks_unchanged", "listeners_unchanged",
    }
    if any(admission.get(field) != "true" for field in true_fields):
        raise AdmissionError("admission safety flag is not true")
    if candidate["archive_equals_bundle_commit"] != "true" or any(
        candidate[field] != "true" for field in ("containers_unchanged", "listeners_unchanged")
    ):
        raise AdmissionError("candidate safety flag is not true")
    candidate_hash = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    if admission["candidate_evidence_sha256"] != candidate_hash:
        raise AdmissionError("candidate evidence changed after rehearsal")
    if (
        admission["rehearsal_result_format"] != REHEARSAL_RESULT_FORMAT
        or not RUN_ID.fullmatch(admission["rehearsal_run_id"])
        or admission["rehearsal_ai_provider"] != "dedicated"
        or not SHA256.fullmatch(admission["rehearsal_artifact_set_sha256"])
        or any(
            not SHA256.fullmatch(admission.get(field, ""))
            for field in ARTIFACT_DIGEST_FIELDS
        )
    ):
        raise AdmissionError("rehearsal artifact evidence is malformed")
    artifact_payload = (
        f"rehearsal_result_format={admission['rehearsal_result_format']}\n"
        f"rehearsal_run_id={admission['rehearsal_run_id']}\n"
        f"rehearsal_ai_provider={admission['rehearsal_ai_provider']}\n"
        + "".join(
            f"{field}={admission[field]}\n"
            for field in sorted(ARTIFACT_DIGEST_FIELDS)
        )
    ).encode()
    if (
        hashlib.sha256(artifact_payload).hexdigest()
        != admission["rehearsal_artifact_set_sha256"]
    ):
        raise AdmissionError("rehearsal artifact-set digest does not match")
    tree = _git_tree_digest(root.resolve(strict=True), revision)
    if not SHA256.fullmatch(tree) or any(
        value != tree
        for value in (
            candidate["tree_sha256"], admission["candidate_tree_sha256"],
            admission["rehearsed_local_source_tree_sha256"],
        )
    ):
        raise AdmissionError("staged/local source-tree equivalence is not valid")
    supply_result = subprocess.run(
        [
            os.sys.executable,
            str(root / "deploy" / "supply_chain_lock.py"),
            "--root",
            str(root),
            "--print-digest",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
    )
    supply_chain_digest = supply_result.stdout.strip()
    if not SHA256.fullmatch(supply_chain_digest):
        raise AdmissionError("invalid admitted supply-chain lock")
    images = {
        f"lecturesift-backend:staged-{revision}": admission["staged_app_image_id"],
        f"lecturesift-egress-proxy:staged-{revision}": admission["staged_proxy_image_id"],
        "lecturesift-backend:local": admission["rehearsed_local_app_image_id"],
        "lecturesift-egress-proxy:local": admission["rehearsed_local_proxy_image_id"],
    }
    if candidate["app_image_id"] != admission["staged_app_image_id"] or candidate[
        "proxy_image_id"
    ] != admission["staged_proxy_image_id"]:
        raise AdmissionError("candidate/admission staged-image mismatch")
    for image, expected_id in images.items():
        if not IMAGE_ID.fullmatch(expected_id):
            raise AdmissionError("invalid admitted image ID")
        image_id, label, supply_label, environment = _image(image)
        if (
            image_id != expected_id
            or label != revision
            or supply_label != supply_chain_digest
            or f"LECTURESIFT_BUILD_REVISION={revision}" not in environment
            or f"LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256={supply_chain_digest}"
            not in environment
        ):
            raise AdmissionError(f"admitted image changed: {image}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expected-revision", required=True)
    args = parser.parse_args()
    try:
        validate(args.root, args.expected_revision)
    except (AdmissionError, OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
        print(f"Exact rehearsal admission failed: {exc}", file=os.sys.stderr)
        return 1
    print(f"EXACT_REHEARSAL_ADMISSION_VALID|revision={args.expected_revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
