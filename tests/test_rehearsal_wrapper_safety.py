from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _wrapper_revision(path: Path) -> str:
    assert path.is_file() and not path.is_symlink(), f"unsafe operator wrapper: {path}"
    matches = [
        line.removeprefix("revision=")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("revision=")
    ]
    assert len(matches) == 1, f"operator wrapper must pin exactly one revision: {path}"
    revision = matches[0]
    assert len(revision) == 40 and set(revision) <= set("0123456789abcdef")
    return revision


def _stage_wrapper_for_revision(secret_root: Path, revision: str) -> Path:
    assert secret_root.is_dir() and not secret_root.is_symlink()
    candidates = sorted(secret_root.glob("stage_exact_release_*.sh"), key=lambda item: item.name)
    matches: list[Path] = []
    for candidate in candidates:
        assert candidate.parent == secret_root
        if _wrapper_revision(candidate) == revision:
            matches.append(candidate)
    assert len(matches) == 1, (
        "expected exactly one safe stage wrapper for pinned revision "
        f"{revision}, found {len(matches)}"
    )
    return matches[0]


def test_stage_wrapper_selection_fails_on_zero_or_multiple_revision_matches(tmp_path: Path) -> None:
    revision = "a" * 40
    with pytest.raises(AssertionError, match="found 0"):
        _stage_wrapper_for_revision(tmp_path, revision)

    first = tmp_path / "stage_exact_release_first.sh"
    first.write_text(f"revision={revision}\n", encoding="utf-8")
    assert _stage_wrapper_for_revision(tmp_path, revision) == first

    second = tmp_path / "stage_exact_release_second.sh"
    second.write_text(f"revision={revision}\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="found 2"):
        _stage_wrapper_for_revision(tmp_path, revision)


def test_exact_wrapper_is_fail_closed_on_every_exit() -> None:
    script = _read("deploy/run_exact_rehearsal.sh")
    assert "set -Eeuo pipefail" in script
    assert "set +x" in script
    assert "trap outer_exit EXIT" in script
    assert script.index("trap outer_exit EXIT") < script.rindex("run_inner_rehearsal\n")
    assert "cleanup_labeled_residue || post_failed=true" in script
    assert "EXACT_REHEARSAL_POSTCONDITION_FAILED" in script
    assert 'docker ps -aq --filter label=lecturesift.rehearsal=true' in script
    assert 'docker volume ls -q --filter label=lecturesift.rehearsal=true' in script
    assert 'docker network ls -q --filter label=lecturesift.rehearsal=true' in script
    assert "provenance_root=/var/lib/lecturesift/rehearsal-provenance" in script
    assert script.count("check_provenance_residue") >= 3
    assert 'find "$provenance_root" -mindepth 1 -maxdepth 1 -print -quit' in script


def test_exact_wrapper_reconciles_durable_provenance_before_baseline() -> None:
    script = _read("deploy/run_exact_rehearsal.sh")
    lock = script.index("flock -n 7")
    reconcile = script.index("run_inner_rehearsal --reconcile-only")
    empty_gate = script.index(
        "check_provenance_residue || fail stale-rehearsal-provenance"
    )
    baseline = script.index('snapshot_inventory "$state/before"')
    full_rehearsal = script.rindex("run_inner_rehearsal\n")
    assert lock < reconcile < empty_gate < baseline < full_rehearsal
    assert 'bash "$root/deploy/rehearsal_restore.sh" "$@"' in script
    assert script.count("env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin") == 1
    assert (
        "REHEARSAL_RECONCILE_OK|database_or_role_modified=false|"
        "provenance_empty=true"
    ) in script
    assert "invalid-rehearsal-provenance-reconcile-evidence" in script


def test_inner_reconcile_is_marker_only_locked_and_durable() -> None:
    script = _read("deploy/rehearsal_restore.sh")
    assert 'case "${1:-}" in' in script
    assert '--reconcile-only) rehearsal_mode="reconcile-only"' in script
    assert script.index('flock -n 8') < script.index(
        'if [[ "$rehearsal_mode" == "reconcile-only" ]]'
    )
    assert script.index('flock -n 9') < script.index(
        'if [[ "$rehearsal_mode" == "reconcile-only" ]]'
    )
    reconcile = script.split("reconcile_rehearsal_provenance_only() {", 1)[1].split(
        "reconcile_stale_rehearsal_state() {", 1
    )[0]
    assert "will not modify it" in reconcile
    assert "reconcile_orphaned_provenance_markers" in reconcile
    assert "dropdb" not in reconcile
    assert "DROP ROLE" not in reconcile
    assert "cleanup_rehearsal_containers" not in reconcile
    assert '[[ -z "$residue" ]]' in reconcile
    assert 'sync -f -- "$rehearsal_provenance_marker"' in script
    assert script.count('sync -f -- "$PROVENANCE_ROOT"') >= 2
    assert script.index('sync -f -- "$rehearsal_provenance_marker"') < script.index(
        'exec -T postgres createdb'
    )
    remove = script.split("remove_rehearsal_provenance_if_clear() {", 1)[1].split(
        "cleanup_rehearsal_containers() {", 1
    )[0]
    assert remove.index('rm -f -- "$PROVENANCE_ROOT/$database.provenance"') < (
        remove.index('sync -f -- "$PROVENANCE_ROOT"')
    )


def test_exact_wrapper_never_executes_master_dotenv() -> None:
    script = _read("deploy/run_exact_rehearsal.sh")
    assert 'source "$postgres_env"' not in script
    assert 'required = {"POSTGRES_USER", "POSTGRES_DB"' in script
    assert "POSTGRES_DOTENV_SYNTAX" in script
    assert 'source "$state/postgres-identifiers.env"' in script
    assert "env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin" in script


def test_exact_wrapper_attests_all_base_state_without_exposing_values() -> None:
    script = _read("deploy/run_exact_rehearsal.sh")
    for suffix in ("containers", "volumes", "networks", "listeners"):
        assert f'"$state/before.{suffix}"' in script
        assert f'"$state/after.{suffix}"' in script
    assert "FROM pg_authid" in script
    assert "md5(coalesce(rolpassword, ''))" in script
    assert "FROM pg_db_role_setting" in script
    assert "FROM pg_auth_members" in script
    assert "FROM pg_parameter_acl" in script
    assert "FROM pg_tablespace" in script
    assert "DATABASE_INVENTORY|" in script
    assert "shobj_description(oid, 'pg_database')" in script
    assert "pg_get_userbyid(datdba)" in script
    assert "datlocprovider" in script and "datcollversion" in script
    assert 'snapshot_databases "$state/before.databases"' in script
    assert 'snapshot_databases "$state/after.databases"' in script
    assert 'cmp --silent "$state/before.databases" "$state/after.databases"' in script
    assert "FROM pg_default_acl" in script
    assert "FROM pg_largeobject_metadata" in script
    assert 'cmp --silent "$state/before.grants" "$state/after.grants"' in script
    assert "redis.sha1hex(salt..key)" in script
    assert "redis.sha1hex(salt..value)" in script
    assert "Redis value/type changed during rehearsal" in script
    assert "base_postgres_unchanged=true" in script
    assert "base_redis_unchanged=true" in script
    assert "roles_unchanged=true" in script
    assert "database_inventory_unchanged=true" in script
    assert "grants_unchanged=true" in script
    assert "SCHEMA_OBJECT|SCHEMA_COMPAT" in script
    assert "TABLE_DIFF" in script
    assert 'verify_schema_transition.py" current' in script
    assert "schema_contract_payment_provider_sessions_v1.txt" in script
    assert "schema_contract_billing_email_verifications_v1.txt" in script
    assert 'before.manifest.canonical" "$state/after.manifest.canonical' in script


def test_candidate_admission_binds_staged_and_rehearsed_images() -> None:
    script = _read("deploy/run_exact_rehearsal.sh")
    assert "validate_candidate" in script
    assert "archive_equals_bundle_commit" in script
    assert "candidate_evidence_sha256" in script
    assert "staged_app_image_id" in script
    assert "rehearsed_local_app_image_id" in script
    assert 'LECTURESIFT_BUILD_REVISION=$revision' in script
    assert '-C "$root" archive --format=tar "$revision"' in script
    assert "candidate_tree_sha256" in script
    assert "rehearsed_local_source_tree_sha256" in script
    assert "source_tree_equivalent=true" in script


def test_exact_wrapper_never_gives_production_role_envs_to_candidate_image() -> None:
    script = _read("deploy/run_exact_rehearsal.sh")
    final_gate = script.split(
        'python3 "$root/deploy/validate_rehearsal_artifacts.py"', 1
    )[1]
    assert '--env-file "$role_env"' not in final_gate
    assert 'lecturesift-backend:local -c' not in final_gate
    assert '/etc/lecturesift/api.env|' not in final_gate
    assert '/etc/lecturesift/worker.env|' not in final_gate
    assert 'bash "$root/deploy/postgres_role_login_probe.sh"' in final_gate
    assert '[[ "$role_login_digest" =~ ^[0-9a-f]{64}$ ]]' in final_gate


def test_production_preflight_revalidates_admission_chain() -> None:
    preflight = _read("deploy/preflight.sh")
    validator = _read("deploy/validate_rehearsal_admission.py")
    assert 'REHEARSAL_ADMISSION_TOOL="$ROOT_DIR/deploy/validate_rehearsal_admission.py"' in preflight
    assert 'python3 "$REHEARSAL_ADMISSION_TOOL"' in preflight
    assert '--root "$ROOT_DIR" --expected-revision "$prepared_revision"' in preflight
    assert preflight.index('python3 "$CUTOVER_EVIDENCE_TOOL" validate-final') < preflight.index(
        'python3 "$REHEARSAL_ADMISSION_TOOL"'
    )
    assert "candidate evidence changed after rehearsal" in validator
    assert "staged/local source-tree equivalence is not valid" in validator
    assert "admitted image changed" in validator
    assert '"lecturesift-backend:local"' in validator
    assert '"archive", "--format=tar", revision' in validator


def test_stager_compares_transport_tree_with_bundle_commit() -> None:
    script = _read("deploy/stage_release_candidate.sh")
    assert '-C "$incoming_worktree"' in script
    assert 'archive --format=tar "$revision"' in script
    assert "archive_tree = inventory(sys.argv[1])" in script
    assert "git_tree = inventory(sys.argv[2])" in script
    assert "if archive_tree != git_tree" in script
    assert "archive_equals_bundle_commit=true" in script
    assert "tree_sha256=" in script
    assert "member.name in seen" in script
    assert "git -C \"$incoming_worktree\" fsck --strict" in script


def test_exact_rehearsal_documents_the_root_equivalent_trust_boundary() -> None:
    docs = _read("deploy/EXACT_REHEARSAL_SAFETY.md")
    deployment = _read("VPS_DEPLOYMENT.md")
    assert "not** a sandbox" in docs
    assert "Docker daemon is host-root-equivalent" in docs
    assert "Do not derive or approve that allowlist solely" in docs
    assert "reviewed_docker_root_equivalent=true" in docs
    assert "/usr/local/sbin/lecturesift-exact-rehearsal-controller" in deployment
    assert "host-root-equivalent" in deployment
    assert "Never invoke candidate" in deployment


def test_isolated_preflight_uses_canonical_ephemeral_secret_staging() -> None:
    script = _read("deploy/run_isolated_preflight.sh")
    assert "LECTURESIFT_EXPECTED_PREFLIGHT_REVISION" in script
    assert "unsafe-staging-base" in script
    assert 'state="$(mktemp -d -- "$staging_base/$revision.XXXXXXXX")"' in script
    assert "trap cleanup EXIT" in script
    assert 'rm -rf -- "$state"' in script
    assert "UNSAFE_DOTENV_SYNTAX" in script
    assert "env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin" in script
    assert '\nsource "$runtime_source"' not in script
    assert '\nsource "$database_source"' not in script


def test_rehearsal_runtime_resources_have_cleanup_labels() -> None:
    stack = _read("deploy/rehearsal_stack.sh")
    assert stack.count("lecturesift.rehearsal=true") >= 4
    assert stack.count("lecturesift.rehearsal.run") >= 7
    assert 'rehearsal_run="${rehearsal_db#lecturesift_rehearsal_}"' in stack


def test_exact_rehearsal_has_no_host_listener_dependency() -> None:
    stack = _read("deploy/rehearsal_stack.sh")
    exact = _read("deploy/run_exact_rehearsal.sh")
    safety = _read("deploy/EXACT_REHEARSAL_SAFETY.md")
    deployment = _read("VPS_DEPLOYMENT.md")

    assert "18000" not in stack
    assert "sport = :18000" not in exact
    assert "rehearsal-port-in-use" not in exact
    assert "no host-published" in safety
    assert "publishes no host port" in deployment
    assert "LS-IG-01" in safety
    assert "LS-IG-01" in deployment


@pytest.mark.skipif(
    not (ROOT / ".local-secrets/run_exact_rehearsal.sh").exists(),
    reason="operator-only ignored wrappers are not present in repository clones",
)
def test_current_operator_wrappers_use_fixed_fail_closed_trust_anchors() -> None:
    secret_root = ROOT / ".local-secrets"
    exact_path = secret_root / "run_exact_rehearsal.sh"
    exact = exact_path.read_text(encoding="utf-8")
    revision = _wrapper_revision(exact_path)
    stage = _stage_wrapper_for_revision(secret_root, revision).read_text(encoding="utf-8")
    isolated = _read(".local-secrets/run_isolated_preflight.sh")
    master = _read(".local-secrets/update_master_role_envs.sh")
    controller = _read("deploy/trusted_exact_rehearsal_controller.sh")

    assert f"revision={revision}" in stage
    assert "controller=/usr/local/sbin/lecturesift-exact-rehearsal-controller" in exact
    assert '[[ "$(id -u)" == "0" ]]' in exact
    assert '[[ -f "$controller" && ! -L "$controller"' in exact
    assert '"$(realpath -e -- "$controller")" == "$controller"' in exact
    assert '"$(stat -c \'%u:%g\' -- "$controller")" == "0:0"' in exact
    assert "env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8" in exact
    assert f'LECTURESIFT_EXPECTED_REHEARSAL_REVISION="{revision}"' not in exact
    assert 'LECTURESIFT_EXPECTED_REHEARSAL_REVISION="$revision"' in exact
    assert exact.rstrip().endswith('"$controller"')
    assert "deploy/run_exact_rehearsal.sh" not in exact
    assert 'bash "$helper"' not in exact
    assert '"$@"' not in exact

    assert (
        "CONTROLLER_PATH=/usr/local/sbin/lecturesift-exact-rehearsal-controller"
        in controller
    )
    assert '"$(realpath -e -- "$0")" == "$CONTROLLER_PATH"' in controller
    assert '"$(stat -c \'%u:%g\' -- "$CONTROLLER_PATH")" == "0:0"' in controller
    assert "(( (8#$controller_mode & 8#022) == 0 ))" in controller
    assert "source_tree_sha256" in controller
    assert "orchestrator_sha256" in controller
    assert "trusted_controller_sha256" in controller
    assert controller.index("unreviewed-source-tree") < controller.index(
        'bash "$helper"'
    )
    assert controller.index("orchestrator-changed-before-exec") < controller.index(
        'bash "$helper"'
    )

    assert "controller=/usr/local/sbin/lecturesift-release-stage-controller" in stage
    assert '"$controller"' in stage
    assert "git clone" not in stage
    assert "stage_release_candidate.sh" not in stage
    assert 'helper="$root/deploy/run_isolated_preflight.sh"' in isolated
    assert 'preflight = worktree / "deploy/run_isolated_preflight.sh"' in master
    assert 'Path("/tmp/lecturesift-run-isolated-preflight.sh")' not in master


@pytest.mark.skipif(
    not (ROOT / ".local-secrets/install_cutover_inputs.py").exists(),
    reason="operator-only ignored cutover installer is not present in repository clones",
)
def test_cutover_input_installer_matches_redis_migration_source_names() -> None:
    installer = _read(".local-secrets/install_cutover_inputs.py")
    migration = _read("deploy/migrate_redis_state.sh")

    assert '("SOURCE_REDIS_URL", redis_url)' in installer
    assert '("SOURCE_CELERY_BROKER_URL", redis_url)' in installer
    assert 'SOURCE_REDIS_URL="${SOURCE_REDIS_URL:-${REDIS_URL:-}}"' in migration
    assert (
        'SOURCE_CELERY_BROKER_URL="${SOURCE_CELERY_BROKER_URL:-${CELERY_BROKER_URL:-}}"'
        in migration
    )
    assert 'SOURCE_REDIS_URL="${REDIS_URL:-}"' not in migration
    assert 'SOURCE_CELERY_BROKER_URL="${CELERY_BROKER_URL:-}"' not in migration
