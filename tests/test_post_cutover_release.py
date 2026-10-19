"""Later releases of a settled provider cutover.

A release after the cutover may pass the read-only production gates only when
the cutover is fully settled: the final proof is valid against its own
release, the one-time first start is consumed and the durable ingress state is
activated. Every mutating cutover step stays pinned to the cutover release.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "post_cutover_provider_cutover_evidence",
    ROOT / "deploy" / "provider_cutover_evidence.py",
)
assert SPEC and SPEC.loader
evidence = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = evidence
SPEC.loader.exec_module(evidence)
BASELINE_SPEC = importlib.util.spec_from_file_location(
    "post_cutover_payment_provider_baseline",
    ROOT / "deploy" / "record_payment_provider_baseline.py",
)
assert BASELINE_SPEC and BASELINE_SPEC.loader
baseline_tool = importlib.util.module_from_spec(BASELINE_SPEC)
BASELINE_SPEC.loader.exec_module(baseline_tool)

CUTOVER_ID = "1" * 32
CUTOVER_REVISION = "2" * 40
NEWER_REVISION = "f" * 40
SOURCE_REVISION = "e" * 40
SOURCE = "3" * 64
SOURCE_MANIFEST = "4" * 64
SOURCE_DUMP = "5" * 64
ROLLBACK_DUMP = "6" * 64
MIGRATED_MANIFEST = "7" * 64
REDIS_STATE = "8" * 64
REDIS_ROLLBACK = "9" * 64
SOURCE_WORKER_STOP = "a" * 64
POSTGRES_SECURITY = "b" * 64
TARGET_REDIS_MANIFEST = "c" * 64
POSTGRES_ROLE_LOGIN_PROBE = "d" * 64
REPOSITORY = "a" * 64
SNAPSHOT = "b" * 64
BACKUP_SET = "c" * 64
TARGET_DIGESTS = {
    "migrated_target_manifest_sha256": MIGRATED_MANIFEST,
    "postgres_role_login_probe_sha256": POSTGRES_ROLE_LOGIN_PROBE,
    "postgres_security_manifest_sha256": POSTGRES_SECURITY,
    "target_redis_manifest_sha256": TARGET_REDIS_MANIFEST,
}
READ_ONLY_COMMANDS = (
    "validate-final",
    "first-start-status",
    "ensure-ingress-freeze",
    "ingress-status",
)
SETTLED_OUTPUTS = {
    "validate-final": "",
    "first-start-status": "consumed",
    "ensure-ingress-freeze": "activated",
    "ingress-status": "activated",
}


@pytest.fixture(autouse=True)
def _use_ci_process_as_evidence_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise ownership checks without requiring the Linux CI user to be root."""
    if os.name == "posix":
        monkeypatch.setattr(evidence, "EVIDENCE_OWNER_UID", os.geteuid())
        monkeypatch.setattr(evidence, "EVIDENCE_OWNER_GID", os.getegid())


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _write_external(path: Path, fields: dict[str, str]) -> None:
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(fields.items())),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _finalized_cutover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "provider-cutover"
    recovery_root = tmp_path / "recovery-drills"
    recovery_root.mkdir(mode=0o700)
    monkeypatch.setattr(evidence, "RECOVERY_EVIDENCE_ROOT", recovery_root)
    common = {"cutover_id": CUTOVER_ID, "revision": CUTOVER_REVISION, "source": SOURCE}
    state = {
        "source_worker_stop_evidence_sha256": SOURCE_WORKER_STOP,
        "target_redis_manifest_sha256": TARGET_REDIS_MANIFEST,
    }
    evidence.begin_postgres(root, **common)
    evidence.write_postgres(
        root,
        **common,
        run_id="postgres-cutover-20261019T090000Z",
        manifest_sha256=SOURCE_MANIFEST,
        migrated_manifest_sha256=MIGRATED_MANIFEST,
        postgres_role_login_probe_sha256=POSTGRES_ROLE_LOGIN_PROBE,
        postgres_security_manifest_sha256=POSTGRES_SECURITY,
        source_dump_sha256=SOURCE_DUMP,
        source_worker_stop_evidence_sha256=SOURCE_WORKER_STOP,
        rollback_dump_sha256=ROLLBACK_DUMP,
        schema_contract_version="5",
    )
    evidence.begin_redis(root, **common)
    evidence.write_redis(
        root,
        **common,
        run_id="redis-migration-20261019T090500Z",
        state_sha256=REDIS_STATE,
        rollback_sha256=REDIS_ROLLBACK,
        **state,
    )
    evidence.write_seed(
        root,
        **common,
        **state,
        run_id="first-cutover-seed-20261019T091000Z-1",
        snapshot_id=SNAPSHOT,
        repository_id_sha256=REPOSITORY,
        backup_set_sha256=BACKUP_SET,
        database_dump_sha256="d" * 64,
        migrated_manifest_sha256=MIGRATED_MANIFEST,
        postgres_role_login_probe_sha256=POSTGRES_ROLE_LOGIN_PROBE,
        postgres_security_manifest_sha256=POSTGRES_SECURITY,
        redis_dump_sha256="e" * 64,
        configuration_checksums_sha256="f" * 64,
        schema_contract_version="5",
    )
    recovery = recovery_root / "restic-restore-20261019T092000Z-1.ok"
    retention = recovery_root / "r2-retention-lock.ok"
    _write_external(
        recovery,
        {
            "backup_set_sha256": BACKUP_SET,
            "drill_scope": "current-latest",
            "live_services_touched": "false",
            "postgres_restore": "verified",
            "repository_id_sha256": REPOSITORY,
            "redis_restore": "verified",
            "restored_payload_removed": "true",
            "snapshot_id": SNAPSHOT,
            "status": "success",
        },
    )
    _write_external(
        retention,
        {
            "repository_id_sha256": REPOSITORY,
            "status": "immutable-retention-verified",
        },
    )
    evidence.finalize(
        root,
        **common,
        **state,
        source_revision=SOURCE_REVISION,
        recovery_marker=Path(recovery.name),
        recovery_sha256=evidence.sha256_file(recovery),
        retention_marker=Path(retention.name),
        retention_sha256=evidence.sha256_file(retention),
        repository_id_sha256=REPOSITORY,
        migrated_target_manifest_sha256=MIGRATED_MANIFEST,
        postgres_role_login_probe_sha256=POSTGRES_ROLE_LOGIN_PROBE,
        postgres_security_manifest_sha256=POSTGRES_SECURITY,
        schema_contract_version="5",
    )
    assert evidence.validate_final(root, expected_revision=CUTOVER_REVISION)
    return root


_INGRESS_PATHS = {
    "required": (),
    "awaiting-activation": ("await-activation",),
    "activated": ("await-activation", "activate"),
    "render-restored": ("await-activation", "begin-restore", "complete-restore"),
}


def _cutover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    first_start: str = "consumed",
    ingress: str | None = "activated",
) -> tuple[Path, Path]:
    root = _finalized_cutover(tmp_path, monkeypatch)
    ingress_root = tmp_path / "ingress-state"
    if first_start in {"armed", "consumed"}:
        assert (
            evidence.arm_first_start(
                root, expected_revision=CUTOVER_REVISION, **TARGET_DIGESTS
            )
            == "armed"
        )
    if first_start == "consumed":
        assert (
            evidence.complete_first_start(root, expected_revision=CUTOVER_REVISION)
            == "consumed"
        )
    if ingress is not None:
        assert (
            evidence.ensure_ingress_freeze(
                root, ingress_root, expected_revision=CUTOVER_REVISION
            )
            == "required"
        )
        if ingress != "required":
            assert (
                evidence.begin_ingress_handoff(
                    root, ingress_root, expected_revision=CUTOVER_REVISION
                )
                == "handoff-in-progress"
            )
        for action in _INGRESS_PATHS[ingress]:
            evidence.transition_ingress_state(
                root,
                ingress_root,
                expected_revision=CUTOVER_REVISION,
                action=action,
            )
        assert (
            evidence.ingress_status(
                root, ingress_root, expected_revision=CUTOVER_REVISION
            )
            == ingress
        )
    return root, ingress_root


def _snapshot(*roots: Path) -> dict[str, bytes]:
    return {
        str(path): path.read_bytes()
        for base in roots
        if base.exists()
        for path in sorted(base.rglob("*"))
        if path.is_file()
    }


def test_settled_cutover_admits_a_later_release_to_every_read_only_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root, ingress_root = _cutover(tmp_path, monkeypatch)
    before = _snapshot(root, ingress_root)

    assert evidence.POST_CUTOVER_READ_ONLY_COMMANDS == frozenset(READ_ONLY_COMMANDS)
    assert (
        evidence.cutover_revision_for_release(
            root, ingress_root, release_revision=NEWER_REVISION
        )
        == CUTOVER_REVISION
    )
    for command in READ_ONLY_COMMANDS:
        for revision in (CUTOVER_REVISION, NEWER_REVISION):
            assert (
                evidence.read_only_release_gate(
                    command, root, ingress_root, release_revision=revision
                )
                == SETTLED_OUTPUTS[command]
            )
    # Reading the settled state for a later release never rewrites evidence.
    assert _snapshot(root, ingress_root) == before


@pytest.mark.parametrize(
    ("first_start", "ingress", "message"),
    [
        ("consumed", None, "ingress state"),
        ("consumed", "required", "activated provider ingress"),
        ("consumed", "awaiting-activation", "activated provider ingress"),
        ("consumed", "render-restored", "activated provider ingress"),
        ("required", "activated", "consumed provider first start"),
        ("armed", "activated", "manual review"),
        ("required", None, "consumed provider first start"),
    ],
)
def test_unsettled_cutover_refuses_a_later_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_start: str,
    ingress: str | None,
    message: str,
):
    root, ingress_root = _cutover(
        tmp_path, monkeypatch, first_start=first_start, ingress=ingress
    )
    before = _snapshot(root, ingress_root)

    with pytest.raises(evidence.EvidenceError, match=message):
        evidence.cutover_revision_for_release(
            root, ingress_root, release_revision=NEWER_REVISION
        )
    for command in READ_ONLY_COMMANDS:
        with pytest.raises(evidence.EvidenceError):
            evidence.read_only_release_gate(
                command, root, ingress_root, release_revision=NEWER_REVISION
            )
    # A refused later release cannot create or advance the ingress fence.
    assert _snapshot(root, ingress_root) == before
    assert ingress is not None or not (ingress_root / evidence.INGRESS_STATE_NAME).exists()

    # The cutover release keeps its unchanged exact-revision behavior.
    assert (
        evidence.read_only_release_gate(
            "validate-final", root, ingress_root, release_revision=CUTOVER_REVISION
        )
        == ""
    )
    if first_start != "armed":
        assert (
            evidence.read_only_release_gate(
                "first-start-status",
                root,
                ingress_root,
                release_revision=CUTOVER_REVISION,
            )
            == first_start
        )


def test_tampered_or_reopened_cutover_refuses_a_later_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root, ingress_root = _cutover(tmp_path, monkeypatch)

    first_start_path = root / evidence.FIRST_START_PROOF_NAME
    first_start_fields = evidence._load(first_start_path)
    evidence._atomic_write(
        first_start_path, {**first_start_fields, "final_proof_sha256": "0" * 64}
    )
    with pytest.raises(evidence.EvidenceError, match="does not match"):
        evidence.cutover_revision_for_release(
            root, ingress_root, release_revision=NEWER_REVISION
        )
    evidence._atomic_write(first_start_path, first_start_fields)
    assert (
        evidence.cutover_revision_for_release(
            root, ingress_root, release_revision=NEWER_REVISION
        )
        == CUTOVER_REVISION
    )

    marker = root / evidence.IN_PROGRESS_NAME
    _write_external(marker, {"phase": "postgres-in-progress"})
    with pytest.raises(evidence.EvidenceError, match="still in progress"):
        evidence.cutover_revision_for_release(
            root, ingress_root, release_revision=NEWER_REVISION
        )
    marker.unlink()

    with (root / evidence.REDIS_PROOF_NAME).open("a", encoding="utf-8") as stream:
        stream.write("tampered=true\n")
    for revision in (NEWER_REVISION, CUTOVER_REVISION):
        for command in READ_ONLY_COMMANDS:
            with pytest.raises(evidence.EvidenceError, match="changed after finalization"):
                evidence.read_only_release_gate(
                    command, root, ingress_root, release_revision=revision
                )


@pytest.mark.parametrize(
    "revision", ["unknown", "F" * 40, "2" * 39, "", CUTOVER_REVISION + "\n"]
)
def test_an_invalid_release_revision_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revision: str
):
    root, ingress_root = _cutover(tmp_path, monkeypatch)
    with pytest.raises(evidence.EvidenceError, match="revision is invalid"):
        evidence.cutover_revision_for_release(
            root, ingress_root, release_revision=revision
        )


def test_every_mutating_cutover_step_stays_pinned_to_the_cutover_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root, ingress_root = _cutover(tmp_path, monkeypatch)
    before = _snapshot(root, ingress_root)
    pinned = {
        "final-schema-contract-version": lambda: evidence.final_schema_contract_version(
            root, expected_revision=NEWER_REVISION
        ),
        "arm-first-start": lambda: evidence.arm_first_start(
            root, expected_revision=NEWER_REVISION, **TARGET_DIGESTS
        ),
        "complete-first-start": lambda: evidence.complete_first_start(
            root, expected_revision=NEWER_REVISION
        ),
        "recover-first-start": lambda: evidence.recover_first_start(
            root, expected_revision=NEWER_REVISION, **TARGET_DIGESTS
        ),
        "ingress-source-revision": lambda: evidence.ingress_source_revision(
            root, ingress_root, expected_revision=NEWER_REVISION
        ),
        "begin-ingress-handoff": lambda: evidence.begin_ingress_handoff(
            root, ingress_root, expected_revision=NEWER_REVISION
        ),
        "validate-final (direct)": lambda: evidence.validate_final(
            root, expected_revision=NEWER_REVISION
        ),
    }
    for field in ("source-fingerprint", "source-executor-stop-digest"):
        pinned[f"ingress-{field}"] = (
            lambda field=field: evidence.ingress_source_contract_field(
                root, ingress_root, expected_revision=NEWER_REVISION, field=field
            )
        )
    for action in ("await-activation", "activate", "begin-restore", "complete-restore"):
        pinned[f"transition-ingress-state {action}"] = (
            lambda action=action: evidence.transition_ingress_state(
                root, ingress_root, expected_revision=NEWER_REVISION, action=action
            )
        )

    for name, call in pinned.items():
        with pytest.raises(evidence.EvidenceError, match="exact release"):
            call()
        assert _snapshot(root, ingress_root) == before, name

    for command in (
        "arm-first-start",
        "complete-first-start",
        "recover-first-start",
        "begin-ingress-handoff",
        "transition-ingress-state",
        "final-schema-contract-version",
        "ingress-source-revision",
    ):
        with pytest.raises(evidence.EvidenceError, match="not a read-only release gate"):
            evidence.read_only_release_gate(
                command, root, ingress_root, release_revision=NEWER_REVISION
            )


def test_only_the_four_read_only_commands_route_through_the_resolver():
    source = _read("deploy/provider_cutover_evidence.py")
    main_source = source[source.index("\ndef main(") :]

    assert "elif args.command in POST_CUTOVER_READ_ONLY_COMMANDS:" in main_source
    assert main_source.count("read_only_release_gate(") == 1
    assert "release_revision=args.expected_revision" in main_source
    assert "cutover_revision_for_release(" not in main_source
    for command in READ_ONLY_COMMANDS:
        assert f'"{command}"' not in main_source
    # Final schema, first-start arm/complete/recover, the source contract
    # readers, the handoff and every transition receive the exact revision.
    assert main_source.count("expected_revision=args.expected_revision") == 8
    for pinned in (
        "final_schema_contract_version(",
        "arm_first_start(",
        "complete_first_start(",
        "recover_first_start(",
        "ingress_source_revision(",
        "ingress_source_contract_field(",
        "begin_ingress_handoff(",
        "transition_ingress_state(",
    ):
        assert main_source.count(pinned) == 1


def _record_cutover_baseline(tmp_path: Path, root: Path) -> tuple[Path, Path, dict]:
    runtime = tmp_path / "runtime.env"
    runtime.write_text("IYZICO_API_KEY=key\nIYZICO_SECRET_KEY=secret\n", encoding="utf-8")
    runtime.chmod(0o600)
    output = tmp_path / "baseline" / f"payment-provider-baseline-{CUTOVER_ID}.json"
    recorded = baseline_tool.record_baseline(
        runtime,
        root / evidence.FINAL_PROOF_NAME,
        output,
        expected_cutover_id=CUTOVER_ID,
        expected_revision=CUTOVER_REVISION,
        enforce_filesystem=False,
    )
    return runtime, output, recorded


def test_baseline_check_reuses_the_settled_resolver_and_recording_stays_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root, ingress_root = _cutover(tmp_path, monkeypatch)
    runtime, output, recorded = _record_cutover_baseline(tmp_path, root)
    final = root / evidence.FINAL_PROOF_NAME

    resolved = baseline_tool.cutover_revision_for_check(
        NEWER_REVISION,
        evidence=evidence,
        evidence_root=root,
        ingress_state_root=ingress_root,
    )
    assert resolved == CUTOVER_REVISION
    checked = baseline_tool.record_baseline(
        runtime,
        final,
        output,
        expected_cutover_id=CUTOVER_ID,
        expected_revision=resolved,
        enforce_filesystem=False,
        require_existing=True,
    )
    assert checked == recorded
    assert checked["release_revision"] == CUTOVER_REVISION

    # The census is never rebound to a later release.
    for require_existing in (True, False):
        with pytest.raises(baseline_tool.ProviderBaselineError, match="does not match"):
            baseline_tool.record_baseline(
                runtime,
                final,
                output,
                expected_cutover_id=CUTOVER_ID,
                expected_revision=NEWER_REVISION,
                enforce_filesystem=False,
                require_existing=require_existing,
            )

    # Injected roots are test-only; production resolution uses fixed paths.
    with pytest.raises(baseline_tool.ProviderBaselineError, match="paths are fixed"):
        baseline_tool.cutover_revision_for_check(
            NEWER_REVISION, evidence=evidence, evidence_root=root
        )

    source = _read("deploy/record_payment_provider_baseline.py")
    assert (
        "cutover_revision_for_check(args.revision) if args.check else args.revision"
        in source
    )
    assert "evidence.cutover_revision_for_release(" in source
    loaded = baseline_tool._load_cutover_evidence()
    assert loaded.EVIDENCE_ROOT == baseline_tool.EVIDENCE_ROOT
    assert loaded.POST_CUTOVER_READ_ONLY_COMMANDS == evidence.POST_CUTOVER_READ_ONLY_COMMANDS


@pytest.mark.parametrize(
    ("first_start", "ingress"),
    [("consumed", "awaiting-activation"), ("required", "activated"), ("consumed", None)],
)
def test_baseline_check_refuses_a_later_release_of_an_unsettled_cutover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_start: str,
    ingress: str | None,
):
    root, ingress_root = _cutover(
        tmp_path, monkeypatch, first_start=first_start, ingress=ingress
    )
    with pytest.raises(baseline_tool.ProviderBaselineError, match="cutover is not settled"):
        baseline_tool.cutover_revision_for_check(
            NEWER_REVISION,
            evidence=evidence,
            evidence_root=root,
            ingress_state_root=ingress_root,
        )
    assert (
        baseline_tool.cutover_revision_for_check(
            CUTOVER_REVISION,
            evidence=evidence,
            evidence_root=root,
            ingress_state_root=ingress_root,
        )
        == CUTOVER_REVISION
    )


def _usable_bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        if os.name == "nt":
            pytest.skip("Bash is unavailable on this Windows host")
        pytest.fail("Bash is required on Linux deployment/CI hosts")
    try:
        probe = subprocess.run(
            [bash, "-c", "test -f deploy/verify_provider_first_start.sh"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if os.name == "nt":
            pytest.skip(f"Bash is not usable on this Windows host: {exc}")
        raise
    if probe.returncode != 0:
        if os.name == "nt":
            pytest.skip("Bash cannot read this Windows checkout")
        pytest.fail("Bash cannot read the deployment checkout")
    return bash


def _first_start_segment() -> str:
    verifier = _read("deploy/verify_provider_first_start.sh")
    start = verifier.index('if [[ "$MODE" == "arm" || "$MODE" == "complete" ]]; then')
    end = verifier.index('SCHEMA_CONTRACT_VERSION="$(', start)
    return verifier[start:end]


def _run_first_start(
    tmp_path: Path, *, mode: str, status: str, instagram: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bash = _usable_bash()
    log = tmp_path / f"calls-{mode}-{status}-{instagram}.log"
    log.write_text("", encoding="utf-8")
    gate = tmp_path / "instagram-gate.sh"
    gate.write_text(
        "#!/usr/bin/env bash\n"
        'printf "instagram-gate\\n" >>"$FAKE_CALL_LOG"\n'
        '[[ "$FAKE_INSTAGRAM" != enabled ]]\n',
        encoding="utf-8",
    )
    harness = (
        "set -euo pipefail\n"
        'MODE="$FAKE_MODE"\n'
        "EVIDENCE_TOOL=/opt/lecturesift/deploy/provider_cutover_evidence.py\n"
        f"EXPECTED_REVISION={NEWER_REVISION}\n"
        'INSTAGRAM_STOP_GATE="$FAKE_GATE"\n'
        'fail() { echo "FAILED:$*" >&2; exit 1; }\n'
        "python3() {\n"
        '  [[ "$1" == "$EVIDENCE_TOOL" && "$3" == --expected-revision &&\n'
        '     "$4" == "$EXPECTED_REVISION" ]] || return 97\n'
        '  printf "evidence:%s\\n" "$2" >>"$FAKE_CALL_LOG"\n'
        '  case "$2:$FAKE_STATUS" in\n'
        "    first-start-status:consumed) echo consumed ;;\n"
        "    first-start-status:required) echo required ;;\n"
        "    first-start-status:armed)\n"
        '      echo "provider first start is in progress" >&2; return 1 ;;\n'
        "    first-start-status:unsettled)\n"
        '      echo "a later release requires activated provider ingress" >&2; return 1 ;;\n'
        "    complete-first-start:armed) echo consumed ;;\n"
        '    *) echo "unexpected evidence call" >&2; return 1 ;;\n'
        "  esac\n"
        "}\n"
        + _first_start_segment()
        + "echo REACHED-TARGET-PROOF\n"
    )
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "FAKE_MODE": mode,
        "FAKE_STATUS": status,
        "FAKE_INSTAGRAM": instagram,
        "FAKE_GATE": gate.as_posix(),
        "FAKE_CALL_LOG": log.as_posix(),
    }
    result = subprocess.run(
        [bash, "-c", harness],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env=environment,
    )
    return result, log.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("mode", ["arm", "complete"])
def test_consumed_first_start_restarts_without_the_instagram_gate(
    tmp_path: Path, mode: str
):
    result, calls = _run_first_start(
        tmp_path, mode=mode, status="consumed", instagram="enabled"
    )
    assert result.returncode == 0, result.stderr
    assert "Provider first-start gate was already consumed." in result.stdout
    assert "REACHED-TARGET-PROOF" not in result.stdout
    assert calls == ["evidence:first-start-status"]


@pytest.mark.parametrize(
    ("mode", "status"),
    [
        ("arm", "required"),
        ("arm", "unsettled"),
        ("complete", "armed"),
        ("complete", "unsettled"),
    ],
)
def test_unconsumed_first_start_still_requires_stopped_instagram_publishers(
    tmp_path: Path, mode: str, status: str
):
    result, calls = _run_first_start(
        tmp_path, mode=mode, status=status, instagram="enabled"
    )
    assert result.returncode == 1
    assert "FAILED:an Instagram publisher remains enabled" in result.stderr
    assert calls == ["evidence:first-start-status", "instagram-gate"]


def test_recovery_always_requires_stopped_instagram_publishers(tmp_path: Path):
    result, calls = _run_first_start(
        tmp_path, mode="recover", status="consumed", instagram="enabled"
    )
    assert result.returncode == 1
    assert "FAILED:an Instagram publisher remains enabled" in result.stderr
    assert calls == ["instagram-gate"]

    result, calls = _run_first_start(
        tmp_path, mode="recover", status="consumed", instagram="disabled"
    )
    assert result.returncode == 0, result.stderr
    assert "REACHED-TARGET-PROOF" in result.stdout
    assert calls == ["instagram-gate"]


def test_first_start_paths_keep_their_strict_checks_after_the_gate(tmp_path: Path):
    result, calls = _run_first_start(
        tmp_path, mode="arm", status="required", instagram="disabled"
    )
    assert result.returncode == 0, result.stderr
    assert "REACHED-TARGET-PROOF" in result.stdout
    assert calls == [
        "evidence:first-start-status",
        "instagram-gate",
        "evidence:first-start-status",
    ]

    result, calls = _run_first_start(
        tmp_path, mode="complete", status="armed", instagram="disabled"
    )
    assert result.returncode == 0, result.stderr
    assert "Provider first-start gate is consumed." in result.stdout
    assert calls == [
        "evidence:first-start-status",
        "instagram-gate",
        "evidence:complete-first-start",
    ]

    result, calls = _run_first_start(
        tmp_path, mode="arm", status="unsettled", instagram="disabled"
    )
    assert result.returncode == 1
    assert "FAILED:the first-start status is ambiguous or crash-fenced" in result.stderr


DATASTORE_START = (
    "up -d --no-deps --wait --wait-timeout 300 postgres redis"
)
DATASTORE_STOP = "stop --timeout 600 redis postgres"


def _datastore_segment() -> str:
    script = _read("deploy/run_exact_rehearsal.sh")
    assert script.count("production_root=/opt/lecturesift\n") == 1
    start = script.index("production_root=/opt/lecturesift\n")
    marker = "ensure_production_datastores || fail production-datastores-unavailable\n"
    end = script.index(marker, start) + len(marker)
    return script[start:end].replace(
        "production_root=/opt/lecturesift\n",
        'production_root="$FAKE_PRODUCTION_ROOT"\n',
        1,
    )


def _run_datastores(
    tmp_path: Path,
    *,
    initial: str,
    compose: str = "ok",
    stop_gate: str = "ok",
    root_uid: str = "0",
    root_mode: str = "755",
    production_root: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    bash = _usable_bash()
    work = tmp_path / "datastores"
    work.mkdir()
    log = work / "calls.log"
    log.write_text("", encoding="utf-8")
    state = work / "state"
    state.write_text(initial + "\n", encoding="utf-8")
    compose_result = work / "compose-result"
    compose_result.write_text(compose + "\n", encoding="utf-8")
    stop_result = work / "stop-result"
    stop_result.write_text(stop_gate + "\n", encoding="utf-8")

    if production_root is None:
        production_root = Path(os.path.realpath(tmp_path)) / "opt-lecturesift"
        production_root.mkdir()
        (production_root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")

    bin_dir = work / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        f"log='{log.as_posix()}'\n"
        f"state='{state.as_posix()}'\n"
        f"compose_result='{compose_result.as_posix()}'\n"
        'printf "docker %s|db_env=%s\\n" "$*" "${LECTURESIFT_DB_ENV_FILE-unset}" >>"$log"\n'
        'if [[ "$1 $2" == "container inspect" ]]; then\n'
        '  name="${!#}"; service="${name#lecturesift-}"; service="${service%-1}"\n'
        '  case "$(cat "$state")" in\n'
        "    healthy) printf 'lecturesift|%s|true|healthy\\n' \"$service\" ;;\n"
        "    stopped) printf 'lecturesift|%s|false|unhealthy\\n' \"$service\" ;;\n"
        "    *) exit 1 ;;\n"
        "  esac\n"
        "  exit 0\n"
        "fi\n"
        'if [[ "$1" == compose ]]; then\n'
        '  case " $* " in\n'
        "    *\" stop \"*) printf 'stopped\\n' >\"$state\"; exit 0 ;;\n"
        "  esac\n"
        '  case "$(cat "$compose_result")" in\n'
        "    ok) printf 'healthy\\n' >\"$state\" ;;\n"
        "    unchanged) ;;\n"
        "    *) exit 1 ;;\n"
        "  esac\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    candidate = work / "candidate"
    (candidate / "deploy").mkdir(parents=True)
    (candidate / "deploy" / "assert_rehearsal_production_stopped.sh").write_text(
        "#!/usr/bin/env bash\n"
        f"printf 'stop-gate\\n' >>'{log.as_posix()}'\n"
        f"[[ \"$(cat '{stop_result.as_posix()}')\" == ok ]]\n",
        encoding="utf-8",
    )

    harness = (
        "set -Eeuo pipefail\n"
        'root="$FAKE_CANDIDATE_ROOT"\n'
        'fail() { echo "FAILED:$*" >&2; exit 1; }\n'
        "stat() {\n"
        '  case "$2" in\n'
        '    %u) printf "%s\\n" "$FAKE_ROOT_UID" ;;\n'
        '    %a) printf "%s\\n" "$FAKE_ROOT_MODE" ;;\n'
        "    *) return 1 ;;\n"
        "  esac\n"
        "}\n"
        + _datastore_segment()
        + "echo DATASTORES-READY\n"
        + 'echo "STARTED=$production_datastores_started"\n'
    )
    environment = {
        "PATH": f"{bin_dir.as_posix()}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
        "LANG": "C.UTF-8",
        "FAKE_PRODUCTION_ROOT": production_root.as_posix(),
        "FAKE_CANDIDATE_ROOT": candidate.as_posix(),
        "FAKE_ROOT_UID": root_uid,
        "FAKE_ROOT_MODE": root_mode,
        "LECTURESIFT_DB_ENV_FILE": "/tmp/operator-shell-override.env",
    }
    result = subprocess.run(
        [bash, "-c", harness],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env=environment,
    )
    return result, log.read_text(encoding="utf-8").splitlines(), production_root


def test_rehearsal_leaves_already_healthy_production_datastores_alone(tmp_path: Path):
    result, calls, _ = _run_datastores(tmp_path, initial="healthy")

    assert result.returncode == 0, result.stderr
    assert "DATASTORES-READY" in result.stdout
    assert not any(call.startswith("docker compose") for call in calls)
    assert "stop-gate" not in calls
    assert len(calls) == 2
    assert all(call.startswith("docker container inspect") for call in calls)
    # Data stores this run did not start are never stopped by it.
    assert "STARTED=false" in result.stdout


def _compose_call(production_root: Path, arguments: str) -> str:
    return (
        f"docker compose --project-directory {production_root.as_posix()} "
        f"--file {production_root.as_posix()}/compose.yaml {arguments}"
        "|db_env=unset"
    )


def test_rehearsal_starts_only_production_postgres_and_redis_after_core_stop(
    tmp_path: Path,
):
    result, calls, production_root = _run_datastores(tmp_path, initial="stopped")

    assert result.returncode == 0, result.stderr
    assert "DATASTORES-READY" in result.stdout
    assert "STARTED=true" in result.stdout
    expected_start = _compose_call(production_root, DATASTORE_START)
    expected_stop = _compose_call(production_root, DATASTORE_STOP)
    compose_calls = [call for call in calls if call.startswith("docker compose")]
    # Exactly one start, from the installed production project, with a scrubbed
    # environment so an operator shell cannot redirect any env file. An exit
    # before outer_exit takes over stops what this run started.
    assert compose_calls == [expected_start, expected_stop]
    start = calls.index(expected_start)
    assert calls.count("stop-gate") == 1
    assert start < calls.index("stop-gate") < calls.index(expected_stop)
    assert calls[-1] == expected_stop
    assert all(
        call.endswith("|db_env=/tmp/operator-shell-override.env")
        for call in calls
        if call.startswith("docker container inspect")
    )


@pytest.mark.parametrize(
    ("compose", "stop_gate"),
    [("fail", "ok"), ("unchanged", "ok"), ("ok", "fail")],
)
def test_rehearsal_fails_closed_when_datastores_cannot_be_proved(
    tmp_path: Path, compose: str, stop_gate: str
):
    result, calls, production_root = _run_datastores(
        tmp_path, initial="stopped", compose=compose, stop_gate=stop_gate
    )

    assert result.returncode == 1
    assert "FAILED:production-datastores-unavailable" in result.stderr
    assert "DATASTORES-READY" not in result.stdout
    if compose != "ok":
        assert "stop-gate" not in calls
    # A failed or unproven start is stopped again on exit.
    assert calls[-1] == _compose_call(production_root, DATASTORE_STOP)


@pytest.mark.parametrize("unsafe", ["non-root", "group-writable", "symlink", "no-compose", "compose-symlink"])
def test_rehearsal_never_starts_datastores_from_an_unsafe_production_root(
    tmp_path: Path, unsafe: str
):
    real_root = Path(os.path.realpath(tmp_path)) / "opt-lecturesift"
    real_root.mkdir()
    production_root = real_root
    root_uid = "0"
    root_mode = "755"
    if unsafe == "non-root":
        (real_root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        root_uid = "1000"
    elif unsafe == "group-writable":
        (real_root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        root_mode = "775"
    elif unsafe == "symlink":
        (real_root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        production_root = Path(os.path.realpath(tmp_path)) / "opt-link"
        production_root.symlink_to(real_root, target_is_directory=True)
    elif unsafe == "compose-symlink":
        target = Path(os.path.realpath(tmp_path)) / "other-compose.yaml"
        target.write_text("services: {}\n", encoding="utf-8")
        (real_root / "compose.yaml").symlink_to(target)

    result, calls, _ = _run_datastores(
        tmp_path,
        initial="stopped",
        root_uid=root_uid,
        root_mode=root_mode,
        production_root=production_root,
    )

    assert result.returncode == 1
    assert "FAILED:production-datastores-unavailable" in result.stderr
    assert not any(call.startswith("docker compose") for call in calls)
    assert "stop-gate" not in calls


def test_rehearsal_starts_datastores_only_after_every_production_stop_proof():
    script = _read("deploy/run_exact_rehearsal.sh")
    units = script.index("for unit in lecturesift.service lecturesift-api.service")
    stop_gate = script.index("fail active-production-runtime")
    lock = script.index("flock -n 7 || fail another-exact-rehearsal-active")
    definition = script.index("ensure_production_datastores() {")
    call = script.index("ensure_production_datastores || fail production-datastores-unavailable")
    reconcile = script.index("reconcile_stale_labeled_resources || fail")
    reconcile_only = script.index("run_inner_rehearsal --reconcile-only")

    assert units < stop_gate < lock < definition < call < reconcile < reconcile_only
    body = script[definition:call]
    assert body.count("docker compose") == 1
    assert DATASTORE_START in body
    assert '--project-directory "$production_root"' in body
    assert '--file "$production_root/compose.yaml"' in body
    assert '"$root/compose.yaml"' not in body
    assert "systemctl" not in body
    # Exactly the two data stores, never API, worker, proxy or Caddy.
    assert DATASTORE_START + " >/dev/null || return 1" in body
    started = body.index("production_datastores_started=true")
    early_trap = body.index(
        "trap 'stop_production_datastores >/dev/null 2>&1 || true' EXIT"
    )
    assert started < early_trap < body.index(DATASTORE_START)
    assert body.index(DATASTORE_START) < body.index(
        'bash "$root/deploy/assert_rehearsal_production_stopped.sh"'
    )

    # The core unit's stop still takes both data stores down, which is why the
    # rehearsal has to own their start.
    core = _read("deploy/lecturesift.service")
    assert "ExecStop=/usr/bin/docker compose stop --timeout 600 worker api egress-proxy redis postgres" in core


def test_rehearsal_stops_the_datastores_it_started_before_any_admission():
    script = _read("deploy/run_exact_rehearsal.sh")
    stop = script[
        script.index("stop_production_datastores() {") : script.index(
            "ensure_production_datastores() {"
        )
    ]
    assert stop.count("docker compose") == 1
    assert '--project-directory "$production_root"' in stop
    assert '--file "$production_root/compose.yaml"' in stop
    assert DATASTORE_STOP + " >/dev/null" in stop
    assert "env -i PATH=\"$PATH\" HOME=/root LANG=C.UTF-8" in stop

    outer = script[script.index("outer_exit() {") : script.index("trap outer_exit EXIT")]
    after_state = outer.index('verify_redis "$after_epoch_ms" || post_failed=true')
    guarded_stop = outer.index(
        'if [[ "$production_datastores_started" == "true" ]]; then\n'
        "    stop_production_datastores || post_failed=true"
    )
    admission = outer.index("if ! write_admission || ! write_handoff_completion; then")
    removal = outer.index('rm -f -- "$admission"')
    assert after_state < guarded_stop < admission < removal
    # outer_exit replaces the early trap only after the stop is in place.
    assert script.index("trap 'stop_production_datastores") < script.index(
        "trap outer_exit EXIT"
    )


def test_post_cutover_release_runbook_is_linked_and_ordered():
    runbook = _read("deploy/POST_CUTOVER_RELEASE.md")
    deployment = _read("VPS_DEPLOYMENT.md")

    assert "deploy/POST_CUTOVER_RELEASE.md" in deployment
    ordered = [
        "sudo systemctl disable --now lecturesift-instagram.timer",
        "sudo systemctl stop lecturesift-instagram.service",
        "sudo systemctl disable --now lecturesift-backup.timer",
        "generate_stage_release_wrapper.py",
        "sudo bash -p <path-to>/stage_exact_release_$REVISION.sh",
        "sudo systemctl stop lecturesift-ingress-selector.service",
        "sudo systemctl stop lecturesift-ingress.service lecturesift.service",
        "sudo systemctl disable lecturesift.service",
        "sudo bash /opt/lecturesift/deploy/check_shell_syntax.sh",
        "/usr/local/sbin/lecturesift-exact-rehearsal-controller",
        "promote_rehearsed_release.sh",
        "sudo systemctl enable lecturesift.service",
        "sudo systemctl start lecturesift.service",
        "sudo systemctl start lecturesift-ingress-selector.service",
        "sudo bash /opt/lecturesift/deploy/verify_production_ingress.sh activated",
        "sudo systemctl enable --now lecturesift-backup.timer",
        "sudo systemctl enable --now lecturesift-instagram.timer",
    ]
    position = 0
    for command in ordered:
        position = runbook.index(command, position) + len(command)
    # Promotion runs after the rehearsal and before any service start.
    assert runbook.index("promote_rehearsed_release.sh") > runbook.index(
        "/usr/local/sbin/lecturesift-exact-rehearsal-controller\n"
    )
    assert runbook.index("sudo systemctl start lecturesift.service") > runbook.index(
        "promote_rehearsed_release.sh"
    )
    assert "LECTURESIFT_PROMOTE_LEGACY_ROOT_CONFIRM" not in runbook.split("## Rollback")[0]
    assert "docker image prune" in runbook
    assert "docker system prune" in runbook
    for section in ("## Rollback", "## Prune old images"):
        assert section in runbook

    # The live cutover release still has the old verifier and probe.
    until = runbook.index("## Until the first release with these fixes is live")
    before_start = runbook.index("## 0. Before you start")
    assert until < before_start
    assert "INSTAGRAM_DAILY_AUTOMATION_ENABLED=false" in runbook[until:before_start]
    assert "Never re-run `lecturesift-r2-retention-probe.service`" in runbook[until:before_start]
    # Promotion must find the rehearsal-started data stores stopped.
    stop = runbook.index(
        "--file /opt/lecturesift/compose.yaml stop --timeout 600 redis postgres"
    )
    assert runbook.index("EXACT_REHEARSAL_ADMITTED") < stop
    assert stop < runbook.index("promote_rehearsed_release.sh")
