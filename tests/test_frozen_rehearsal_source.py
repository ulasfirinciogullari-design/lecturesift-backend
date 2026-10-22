from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import re
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "deploy" / "frozen_source_dump.py"
OWNER = (os.getuid(), os.getgid())
PAYLOAD = b"PGDMP\x01\x10\x00\x04\x08\x01\x01\x01frozen render source"

# Every script that the post-cutover stage, rehearse, promote and start steps
# run. None of them may need Render or its credentials any more.
RELEASE_PATH = (
    "deploy/run_exact_rehearsal.sh",
    "deploy/rehearsal_restore.sh",
    "deploy/rehearsal_stack.sh",
    "deploy/provision_database_role.sh",
    "deploy/trusted_exact_rehearsal_controller.sh",
    "deploy/trusted_stage_release_controller.sh",
    "deploy/stage_release_candidate.sh",
    "deploy/release.sh",
    "deploy/promote_rehearsed_release.sh",
    "deploy/preflight.sh",
    "deploy/frozen_source_dump.py",
)


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _module():
    spec = importlib.util.spec_from_file_location("frozen_source_dump", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _install(
    tmp_path: Path,
    payload: bytes = PAYLOAD,
    *,
    pin: str | None = None,
) -> Path:
    parent = tmp_path.resolve() / "lecturesift"
    directory = parent / "frozen-source"
    directory.mkdir(parents=True)
    os.chmod(parent, 0o700)
    os.chmod(directory, 0o700)
    dump = directory / "render-postgres.dump"
    dump.write_bytes(payload)
    os.chmod(dump, 0o400)
    digest = directory / "render-postgres.dump.sha256"
    if pin is None:
        pin = f"{hashlib.sha256(payload).hexdigest()}  render-postgres.dump\n"
    digest.write_text(pin, encoding="ascii")
    os.chmod(digest, 0o400)
    return directory


def _function(script: str, name: str) -> str:
    return script.split(f"{name}() {{", 1)[1].split("\n}\n", 1)[0]


def test_pinned_frozen_dump_is_released_with_its_digest(tmp_path: Path) -> None:
    helper = _module()
    directory = _install(tmp_path)

    payload, digest = helper.read_verified_dump(directory, owner=OWNER)

    assert payload == PAYLOAD
    assert digest == hashlib.sha256(PAYLOAD).hexdigest()
    assert helper.FROZEN_SOURCE_DIRECTORY == Path("/var/backups/lecturesift/frozen-source")
    assert helper.FROZEN_SOURCE_OWNER == (0, 0)


def test_frozen_dump_that_differs_from_its_pin_is_refused(tmp_path: Path) -> None:
    helper = _module()
    directory = _install(tmp_path, pin=f"{'0' * 64}  render-postgres.dump\n")

    with pytest.raises(helper.FrozenSourceError, match="does not match its pinned SHA-256"):
        helper.read_verified_dump(directory, owner=OWNER)


def test_changed_frozen_dump_bytes_are_refused(tmp_path: Path) -> None:
    helper = _module()
    directory = _install(tmp_path)
    dump = directory / "render-postgres.dump"
    os.chmod(dump, 0o600)
    dump.write_bytes(PAYLOAD + b"tampered")
    os.chmod(dump, 0o400)

    with pytest.raises(helper.FrozenSourceError, match="does not match its pinned SHA-256"):
        helper.read_verified_dump(directory, owner=OWNER)


@pytest.mark.parametrize(
    "pin",
    [
        f"{hashlib.sha256(PAYLOAD).hexdigest()}\n",
        f"{hashlib.sha256(PAYLOAD).hexdigest().upper()}  render-postgres.dump\n",
        f"{hashlib.sha256(PAYLOAD).hexdigest()}  render-postgres.dump",
        f"{hashlib.sha256(PAYLOAD).hexdigest()}  other.dump\n",
        f"{hashlib.sha256(PAYLOAD).hexdigest()}  /var/backups/x/render-postgres.dump\n",
        f"{hashlib.sha256(PAYLOAD).hexdigest()}  render-postgres.dump\n" * 2,
    ],
)
def test_frozen_dump_pin_must_be_one_exact_sha256sum_line(tmp_path: Path, pin: str) -> None:
    helper = _module()
    directory = _install(tmp_path, pin=pin)

    with pytest.raises(helper.FrozenSourceError, match="exactly one line"):
        helper.read_verified_dump(directory, owner=OWNER)


def test_frozen_dump_must_be_a_custom_archive(tmp_path: Path) -> None:
    helper = _module()
    directory = _install(tmp_path, b"-- plain SQL is not the pinned archive\n")

    with pytest.raises(helper.FrozenSourceError, match="custom archive"):
        helper.read_verified_dump(directory, owner=OWNER)


@pytest.mark.parametrize("name", ["render-postgres.dump", "render-postgres.dump.sha256"])
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o700, 0o500])
def test_frozen_source_files_need_mode_0400_or_0600(
    tmp_path: Path, name: str, mode: int
) -> None:
    helper = _module()
    directory = _install(tmp_path)
    os.chmod(directory / name, mode)

    with pytest.raises(helper.FrozenSourceError, match="mode 0400 or 0600"):
        helper.read_verified_dump(directory, owner=OWNER)


def test_frozen_source_files_may_use_mode_0600(tmp_path: Path) -> None:
    helper = _module()
    directory = _install(tmp_path)
    os.chmod(directory / "render-postgres.dump", 0o600)
    os.chmod(directory / "render-postgres.dump.sha256", 0o600)

    assert helper.read_verified_dump(directory, owner=OWNER)[0] == PAYLOAD


@pytest.mark.parametrize("name", ["render-postgres.dump", "render-postgres.dump.sha256"])
def test_symlinked_frozen_source_files_are_refused(tmp_path: Path, name: str) -> None:
    helper = _module()
    directory = _install(tmp_path)
    elsewhere = tmp_path.resolve() / name
    os.replace(directory / name, elsewhere)
    (directory / name).symlink_to(elsewhere)

    with pytest.raises(helper.FrozenSourceError, match="canonical and not a symlink"):
        helper.read_verified_dump(directory, owner=OWNER)


def test_hard_linked_frozen_dump_is_refused(tmp_path: Path) -> None:
    helper = _module()
    directory = _install(tmp_path)
    os.link(directory / "render-postgres.dump", tmp_path / "second-name.dump")

    with pytest.raises(helper.FrozenSourceError, match="single-link"):
        helper.read_verified_dump(directory, owner=OWNER)


def test_frozen_source_owned_by_anyone_but_the_expected_owner_is_refused(
    tmp_path: Path,
) -> None:
    helper = _module()
    directory = _install(tmp_path)

    with pytest.raises(helper.FrozenSourceError, match="root-owned"):
        helper.read_verified_dump(directory, owner=(OWNER[0] + 1, OWNER[1]))


@pytest.mark.parametrize("mode", [0o750, 0o705, 0o770, 0o777])
def test_frozen_source_directory_must_be_private(tmp_path: Path, mode: int) -> None:
    helper = _module()
    directory = _install(tmp_path)
    os.chmod(directory, mode)

    with pytest.raises(helper.FrozenSourceError, match="mode 0700"):
        helper.read_verified_dump(directory, owner=OWNER)


@pytest.mark.parametrize("mode", [0o770, 0o702])
def test_frozen_source_parent_must_be_writable_only_by_root(
    tmp_path: Path, mode: int
) -> None:
    helper = _module()
    directory = _install(tmp_path)
    os.chmod(directory.parent, mode)

    with pytest.raises(helper.FrozenSourceError, match="only root can write"):
        helper.read_verified_dump(directory, owner=OWNER)


def test_symlinked_frozen_source_directory_is_refused(tmp_path: Path) -> None:
    helper = _module()
    directory = _install(tmp_path)
    alias = directory.parent / "alias"
    alias.symlink_to(directory)

    with pytest.raises(helper.FrozenSourceError, match="canonical root-owned directory"):
        helper.read_verified_dump(alias, owner=OWNER)


def test_missing_frozen_dump_is_refused(tmp_path: Path) -> None:
    helper = _module()
    directory = _install(tmp_path)
    (directory / "render-postgres.dump").unlink()

    with pytest.raises(helper.FrozenSourceError, match="missing"):
        helper.read_verified_dump(directory, owner=OWNER)


def test_stream_releases_exact_bytes_and_verify_prints_the_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    helper = _module()
    directory = _install(tmp_path)
    monkeypatch.setattr(helper, "FROZEN_SOURCE_DIRECTORY", directory)
    monkeypatch.setattr(helper, "FROZEN_SOURCE_OWNER", OWNER)
    monkeypatch.setattr(
        helper, "finalized_source_dump_sha256", lambda: hashlib.sha256(PAYLOAD).hexdigest()
    )

    assert helper.main(["stream"]) == 0
    assert capsysbinary.readouterr().out == PAYLOAD

    assert helper.main(["verify"]) == 0
    digest = hashlib.sha256(PAYLOAD).hexdigest()
    assert capsysbinary.readouterr().out == (
        f"FROZEN_SOURCE_DUMP_OK|sha256={digest}|bytes={len(PAYLOAD)}\n".encode()
    )


def test_stream_writes_nothing_when_the_dump_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    helper = _module()
    directory = _install(tmp_path, pin=f"{'f' * 64}  render-postgres.dump\n")
    monkeypatch.setattr(helper, "FROZEN_SOURCE_DIRECTORY", directory)
    monkeypatch.setattr(helper, "FROZEN_SOURCE_OWNER", OWNER)
    monkeypatch.setattr(helper, "finalized_source_dump_sha256", lambda: "f" * 64)

    assert helper.main(["stream"]) == 1
    captured = capsysbinary.readouterr()
    assert captured.out == b""
    assert b"Frozen source dump rejected" in captured.err


@pytest.mark.parametrize("mode", ["stream", "verify"])
def test_pinned_dump_that_is_not_the_finalized_cutover_dump_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
    mode: str,
) -> None:
    # A stale or failed cutover run's dump can match its own colocated pin;
    # only the digest the finalized cutover recorded is accepted.
    helper = _module()
    directory = _install(tmp_path)
    monkeypatch.setattr(helper, "FROZEN_SOURCE_DIRECTORY", directory)
    monkeypatch.setattr(helper, "FROZEN_SOURCE_OWNER", OWNER)
    monkeypatch.setattr(helper, "finalized_source_dump_sha256", lambda: "0" * 64)

    assert helper.main([mode]) == 1
    captured = capsysbinary.readouterr()
    assert captured.out == b""
    assert b"not the Render dump the finalized provider cutover restored" in captured.err


def test_cutover_evidence_failures_refuse_the_dump(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    helper = _module()
    directory = _install(tmp_path)
    monkeypatch.setattr(helper, "FROZEN_SOURCE_DIRECTORY", directory)
    monkeypatch.setattr(helper, "FROZEN_SOURCE_OWNER", OWNER)

    def refuse() -> str:
        raise helper.FrozenSourceError("the finalized provider cutover evidence is invalid")

    monkeypatch.setattr(helper, "finalized_source_dump_sha256", refuse)

    assert helper.main(["stream"]) == 1
    captured = capsysbinary.readouterr()
    assert captured.out == b""
    assert b"cutover evidence is invalid" in captured.err


class _StubEvidence:
    class EvidenceError(RuntimeError):
        pass

    EVIDENCE_ROOT = Path("/evidence")
    POSTGRES_PROOF_NAME = "postgres-cutover.ok"
    FINAL_PROOF_NAME = "provider-cutover.ok"

    def __init__(self, *, digest: str, proof_sha256: str = "a" * 64, invalid: bool = False):
        self.digest = digest
        self.proof_sha256 = proof_sha256
        self.invalid = invalid
        self.validated: list[tuple[Path, str]] = []

    def _load(self, path: Path) -> dict[str, str]:
        if path.name == self.FINAL_PROOF_NAME:
            return {"release_revision": "c" * 40}
        return {"run_id": "postgres-cutover-20261019T141500Z", "source_dump_sha256": self.digest}

    def validate_final(self, root: Path, *, expected_revision: str) -> dict[str, str]:
        self.validated.append((root, expected_revision))
        if self.invalid:
            raise self.EvidenceError("provider cutover is still in progress")
        return {"postgres_proof_sha256": "a" * 64}

    def sha256_file(self, path: Path) -> str:
        assert path == self.EVIDENCE_ROOT / self.POSTGRES_PROOF_NAME
        return self.proof_sha256


def test_finalized_digest_comes_from_the_validated_cutover_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = _module()
    stub = _StubEvidence(digest="b" * 64)
    monkeypatch.setattr(helper, "_cutover_evidence", lambda: stub)

    assert helper.finalized_source_dump_sha256() == "b" * 64
    assert stub.validated == [(Path("/evidence"), "c" * 40)]


@pytest.mark.parametrize(
    ("stub", "message"),
    [
        (_StubEvidence(digest="b" * 64, invalid=True), "cutover evidence is invalid"),
        (_StubEvidence(digest="b" * 64, proof_sha256="d" * 64), "changed after finalization"),
        (_StubEvidence(digest="not-a-digest"), "no valid source dump digest"),
    ],
)
def test_unfinalized_or_changed_cutover_evidence_is_refused(
    monkeypatch: pytest.MonkeyPatch, stub: _StubEvidence, message: str
) -> None:
    helper = _module()
    monkeypatch.setattr(helper, "_cutover_evidence", lambda: stub)

    with pytest.raises(helper.FrozenSourceError, match=message):
        helper.finalized_source_dump_sha256()


def test_real_cutover_evidence_tool_provides_what_the_helper_reads() -> None:
    helper = _module()
    evidence = helper._cutover_evidence()

    assert helper.CUTOVER_EVIDENCE_TOOL == ROOT / "deploy" / "provider_cutover_evidence.py"
    assert evidence.EVIDENCE_ROOT == Path("/var/lib/lecturesift/provider-cutover")
    assert evidence.POSTGRES_PROOF_NAME == "postgres-cutover.ok"
    assert evidence.FINAL_PROOF_NAME == "provider-cutover.ok"
    for name in ("_load", "validate_final", "sha256_file", "EvidenceError"):
        assert hasattr(evidence, name)
    assert "source_dump_sha256" in _read("deploy/provider_cutover_evidence.py")


def test_release_path_never_needs_render_or_its_credentials() -> None:
    for relative in RELEASE_PATH:
        text = _read(relative)
        for forbidden in (
            ".lecturesift-render-source.env",
            "LECTURESIFT_SOURCE_DB_ENV",
            "source_postgres_transport",
            "exec-libpq-docker",
            "SOURCE_DATABASE_URL",
            "render.com",
            "source-postgres-client",
        ):
            assert forbidden not in text, (relative, forbidden)


def test_frozen_source_container_is_disposable_network_less_and_labelled() -> None:
    restore = _read("deploy/rehearsal_restore.sh")
    compose = _read("compose.yaml")
    start = _function(restore, "start_frozen_source_postgres")
    run = start.split("docker run", 1)[1].split("' >/dev/null || return 1", 1)[0]

    image = re.search(r'^POSTGRES_IMAGE="(postgres:18-bookworm@sha256:[0-9a-f]{64})"$', restore, re.M)
    assert image and f"image: {image.group(1)}" in compose
    assert 'FROZEN_SOURCE_CONTAINER="lecturesift-source-postgres-rehearsal"' in restore
    for flag in (
        "--detach --pull=never --network none --read-only",
        '--name "$FROZEN_SOURCE_CONTAINER"',
        "--label lecturesift.rehearsal=true",
        '--label "lecturesift.rehearsal.run=$rehearsal_suffix"',
        "--label lecturesift.rehearsal.purpose=frozen-source-postgres",
        "--user 999:999 --cap-drop ALL --security-opt no-new-privileges",
        "--memory 1536m --memory-swap 1536m",
        # The image declares VOLUME /var/lib/postgresql; tmpfs on that exact
        # path keeps the data off disk and prevents an anonymous volume.
        "--tmpfs /var/lib/postgresql:rw,nosuid,nodev,size=1g,uid=999,gid=999,mode=0700",
        '--entrypoint bash "$POSTGRES_IMAGE"',
        "--auth-host=reject",
        "--locale=en_US.UTF8 --encoding=UTF8",
        "-c listen_addresses= -c unix_socket_directories=/tmp",
    ):
        assert flag in run, flag
    for forbidden in ("--volume", " -v ", "--mount", "--env-file", "--publish", " -p ", "--network host"):
        assert forbidden not in run, forbidden

    # The started container is re-inspected before anything is restored into it.
    inspect = start.index("{{.HostConfig.NetworkMode}}|{{.HostConfig.ReadonlyRootfs}}")
    assert '"$network_mode" == "none" && "$read_only" == "true"' in start
    assert '"$purpose_label" == "frozen-source-postgres"' in start
    assert '[[ "$mount_types" =~ ^(tmpfs )*$ ]]' in start
    restore_dump = start.index('python3 -I "$FROZEN_SOURCE_DUMP_TOOL" stream | \\')
    assert inspect < restore_dump
    assert "--template=template0 \\\n    --encoding=UTF8 --locale=en_US.UTF8" in start
    assert (
        "--exit-on-error --single-transaction --no-owner --no-acl \\\n"
        '      --dbname="$FROZEN_SOURCE_DATABASE"'
    ) in start


def test_frozen_source_reads_are_read_only_and_produce_the_same_artifacts() -> None:
    restore = _read("deploy/rehearsal_restore.sh")
    reader = _function(restore, "frozen_source_read")

    assert '--env "PGOPTIONS=-c default_transaction_read_only=on"' in reader
    assert '-v ON_ERROR_STOP=1 -f - <"$1"' in reader
    dump = restore.split('docker exec --env "PGOPTIONS=-c default_transaction_read_only=on"', 1)[1]
    dump = dump.split('> "$run_dir/render.dump"', 1)[0]
    assert "pg_dump" in dump
    assert "--format=custom" in dump
    assert "--no-owner --no-acl --serializable-deferrable" in dump
    for artifact in (
        '> "$run_dir/source-inventory.txt"',
        '> "$run_dir/source-before.txt"',
        '> "$run_dir/render.dump"',
        '> "$run_dir/source-after.txt"',
        '> "$run_dir/render.dump.sha256"',
        '> "$run_dir/render.dump.list"',
    ):
        assert restore.count(artifact) == 1, artifact
    assert '--inventory "$run_dir/source-inventory.txt" --accept 4,5' in restore


def test_frozen_source_container_is_removed_before_the_clone_and_by_every_cleanup() -> None:
    restore = _read("deploy/rehearsal_restore.sh")
    exact = _read("deploy/run_exact_rehearsal.sh")
    preflight = _read("deploy/preflight.sh")

    source_after = restore.index('> "$run_dir/source-after.txt"')
    listing = restore.index('> "$run_dir/render.dump.list"')
    removal = restore.index('cleanup_rehearsal_containers "$rehearsal_suffix"\n', listing)
    removed_check = restore.index(
        'if docker container inspect "$FROZEN_SOURCE_CONTAINER" >/dev/null 2>&1; then'
    )
    marker = restore.index("create_rehearsal_provenance_marker\n", removal)
    stack = restore.index('"$ROOT_DIR/deploy/rehearsal_stack.sh" "$rehearsal_db"')
    assert source_after < listing < removal < removed_check < marker < stack

    containers = _function(restore, "cleanup_rehearsal_containers")
    assert "lecturesift-source-postgres-rehearsal; do" in containers
    assert '"$purpose_label" == "frozen-source-postgres"' in containers
    cleanup = _function(restore, "cleanup_rehearsal")
    assert 'cleanup_rehearsal_containers "$rehearsal_suffix"' in cleanup
    stale = _function(restore, "reconcile_stale_rehearsal_state")
    assert stale.index("cleanup_rehearsal_containers\n") < stale.index("dropdb")

    for function in ("reconcile_stale_labeled_resources", "cleanup_labeled_residue"):
        body = exact.split(f"{function}() {{", 1)[1].split("\n}\n", 1)[0]
        assert "lecturesift-source-postgres-rehearsal" in body
        assert '"$purpose_label" == "frozen-source-postgres" ]] || return 1' in body
    assert "docker container inspect lecturesift-source-postgres-rehearsal" in preflight


def test_reconcile_modes_need_neither_render_nor_the_frozen_dump() -> None:
    restore = _read("deploy/rehearsal_restore.sh")
    exact = _read("deploy/run_exact_rehearsal.sh")

    stale_dispatch = restore.index('if [[ "$rehearsal_mode" == "reconcile-stale" ]]')
    only_dispatch = restore.index('if [[ "$rehearsal_mode" == "reconcile-only" ]]')
    verify = restore.index('python3 -I "$FROZEN_SOURCE_DUMP_TOOL" verify >/dev/null')
    run_dir = restore.index('mkdir -m 0700 -- "$run_dir"')
    start = restore.index("start_frozen_source_postgres || {")
    assert stale_dispatch < only_dispatch < verify < run_dir < start
    # Before the mode dispatch the tool is only named as a tracked input file.
    head = restore[:stale_dispatch]
    assert head.count("$FROZEN_SOURCE_DUMP_TOOL") == 1
    # The shell never touches the dump path itself; only the verifier reads it.
    assert "/var/backups/lecturesift/frozen-source" not in restore
    assert "/var/backups/lecturesift/frozen-source" not in exact

    inner = exact.split("run_inner_rehearsal() {", 1)[1].split("\n}\n", 1)[0]
    assert "SOURCE" not in inner


def test_outer_gate_proves_the_frozen_source_before_any_runtime_change() -> None:
    exact = _read("deploy/run_exact_rehearsal.sh")

    tree = exact.index("fail trusted-handoff-tree-mismatch")
    verify = exact.index(
        'python3 -I "$root/deploy/frozen_source_dump.py" verify >/dev/null || \\\n'
        "  fail frozen-source-dump-unavailable"
    )
    lock = exact.index('exec 7>"$staging_base/.exact-rehearsal.lock"')
    datastores = exact.index("ensure_production_datastores || fail production-datastores-unavailable")
    baseline = exact.index('snapshot_inventory "$state/before"')
    assert tree < verify < lock < datastores < baseline
    assert "fail missing-frozen-source-tool" in exact


def test_release_docs_install_the_frozen_source_and_order_render_deletion_last() -> None:
    runbook = _read("deploy/POST_CUTOVER_RELEASE.md")
    safety = _read("deploy/EXACT_REHEARSAL_SAFETY.md")

    assert "Keep that file and the frozen" not in runbook
    assert "/var/backups/lecturesift/frozen-source/render-postgres.dump" in runbook
    assert "frozen-source-dump-unavailable" in runbook
    install = runbook[
        runbook.index("### One-time install"):runbook.index("### Deleting the Render database")
    ]
    ordered = [
        "EVIDENCE=/var/lib/lecturesift/provider-cutover/postgres-cutover.ok",
        "s/^run_id=//p",
        "s/^source_dump_sha256=//p",
        'SRC="/var/backups/lecturesift/postgres-cutover/$RUN_ID/render-final.dump"',
        "SOURCE_DUMP_MATCHES",
        'sudo install -d -o root -g root -m 0700 "$DST"',
        'sudo install -o root -g root -m 0400 "$SRC" "$DST/render-postgres.dump"',
        "printf '%s  render-postgres.dump\\n' \"$EXPECTED\"",
        'sudo chmod 0400 "$DST/render-postgres.dump.sha256"',
        "sha256sum -c render-postgres.dump.sha256",
        "frozen_source_dump.py\" verify",
    ]
    position = 0
    for command in ordered:
        position = install.index(command, position) + len(command)
    deletion = runbook[
        runbook.index("### Deleting the Render database"):runbook.index("## 0. Before you start")
    ]
    assert "first,\nthen delete Render" in deletion
    assert "`/root/.lecturesift-render-source.env`, and both can be removed" in deletion
    assert runbook.index("## Frozen rehearsal source") < runbook.index("## 0. Before you start")

    assert "source_dump_sha256" in runbook
    assert "render-final-<UTC-stamp>" not in runbook
    assert "validate_final" in safety
    assert "frozen-source-postgres" in safety
    assert "`--network none`" in safety
    assert "The rehearsal never connects to Render." in safety
