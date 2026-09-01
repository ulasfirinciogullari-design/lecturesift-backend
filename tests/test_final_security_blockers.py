from __future__ import annotations

import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_successful_redis_migration_retains_exact_rollback_state_and_metadata() -> None:
    script = _read("deploy/migrate_redis_state.sh")
    assert 'TARGET_ROLLBACK_STATE="$RUN_DIR/target-before.json"' in script
    assert (
        'TARGET_ROLLBACK_METADATA="$RUN_DIR/target-rollback-metadata.json"'
        in script
    )
    assert '"schema": "lecturesift-redis-rollback-v1"' in script
    assert '"redis_key": "lecturesift:jobs:v2"' in script
    assert '"existed": existed' in script
    assert '"payload_bytes": len(payload)' in script
    assert '"payload_sha256": hashlib.sha256(payload).hexdigest()' in script
    assert "redis-cli --raw STRLEN lecturesift:jobs:v2" in script
    assert 'len(value) != expected + 1 or not value.endswith(b"\\n")' in script
    assert 'stream.truncate(expected)' in script
    assert "os.fsync(stream.fileno())" in script
    assert 'value != expected + b"\\n"' in script
    assert 'sort_keys=True, separators=(",", ":")' in script
    assert 'os.fchmod(fd, 0o600)' in script
    assert 'os.fchown(fd, 0, 0)' in script
    assert '"$metadata_rollback_sha256" == "$redis_rollback_sha256"' in script

    success_cleanup = script.split(
        'python3 "$CUTOVER_EVIDENCE_TOOL" write-redis', 1
    )[1]
    assert 'rm -f -- "$RUN_DIR/source-before.json"' in success_cleanup
    assert 'rm -f -- "$TARGET_ROLLBACK_STATE"' not in success_cleanup
    assert 'rm -f -- "$TARGET_ROLLBACK_METADATA"' not in success_cleanup
    assert (
        "Root-only rollback state and canonical metadata retained:"
        in success_cleanup
    )


def test_exact_rehearsal_admission_requires_one_time_trusted_handoff() -> None:
    controller = _read("deploy/trusted_exact_rehearsal_controller.sh")
    rehearsal = _read("deploy/run_exact_rehearsal.sh")
    validator = _read("deploy/validate_rehearsal_admission.py")

    assert 'handoff="$state/handoff.attestation"' in controller
    assert 'nonce="$(tr -d \'-\' </proc/sys/kernel/random/uuid)"' in controller
    assert 'chmod 0600 "$handoff_temporary"' in controller
    assert 'chown root:root "$handoff_temporary"' in controller
    assert 'LECTURESIFT_TRUSTED_REHEARSAL_HANDOFF="$handoff"' in controller
    assert 'LECTURESIFT_TRUSTED_REHEARSAL_NONCE="$nonce"' in controller
    assert 'LECTURESIFT_TRUSTED_REHEARSAL_HANDOFF_SHA256="$handoff_sha256"' in controller
    assert '[[ ! -e "$handoff" && ! -L "$handoff" ]]' in controller
    assert '"$state/handoff.completed"' in controller
    assert 'rehearsal_admission="$ADMISSION_ROOT/$revision.ok"' in controller
    assert "invalidate_rehearsal_admission()" in controller
    assert '"$child_started" == "true"' in controller
    assert "unsafe-existing-admission" in controller
    assert 'rm -f -- "$rehearsal_admission"' in controller
    assert 'sync -f "$ADMISSION_ROOT"' in controller
    assert controller.index("invalidate_rehearsal_admission || fail unsafe-existing-admission") < controller.index(
        "child_started=true"
    )

    consume = rehearsal.index("consume_trusted_controller_handoff\n")
    syntax_gate = rehearsal.index(
        'bash "$root/deploy/check_shell_syntax.sh" || fail shell-syntax-gate'
    )
    admission = rehearsal.index("write_admission() {")
    assert consume < syntax_gate < admission
    assert 'mv -T -- "$trusted_handoff" "$trusted_handoff_consumed"' in rehearsal
    assert "replayed-trusted-handoff" in rehearsal
    assert "verify_trusted_handoff_for_admission || return 1" in rehearsal
    assert "write_handoff_completion" in rehearsal
    assert 'version=5\\nstatus=verified' in rehearsal
    assert "trusted_controller_handoff_sha256" in rehearsal
    assert 'rm -f -- "$admission"' in rehearsal

    assert 'ADMISSION_VERSION = "5"' in validator
    assert 'admission["rehearsal_ai_provider"] != "dedicated"' in validator
    assert '"trusted_controller_sha256", "trusted_controller_handoff_sha256"' in validator
    assert "trusted controller changed after rehearsal" in validator


def test_candidate_stage_requires_one_time_trusted_stage_handoff_before_transport_parsing() -> None:
    controller = _read("deploy/trusted_stage_release_controller.sh")
    candidate = _read("deploy/stage_release_candidate.sh")
    exact = _read("deploy/run_exact_rehearsal.sh")
    validator = _read("deploy/validate_rehearsal_admission.py")

    tree_gate = controller.index("fail unreviewed-source-tree")
    handoff = controller.index('handoff="$state/handoff.attestation"')
    candidate_exec = controller.index('bash -p "$candidate"')
    assert tree_gate < handoff < candidate_exec
    assert 'nonce="$(tr -d \'-\' </proc/sys/kernel/random/uuid)"' in controller
    assert 'chmod 0600 "$handoff_temporary"' in controller
    assert 'chown root:root "$handoff_temporary"' in controller
    assert 'LECTURESIFT_TRUSTED_STAGE_HANDOFF="$handoff"' in controller
    assert 'LECTURESIFT_TRUSTED_STAGE_NONCE="$nonce"' in controller
    assert 'LECTURESIFT_TRUSTED_STAGE_HANDOFF_SHA256="$handoff_sha256"' in controller
    assert '"$state/handoff.completed"' in controller
    assert "stage completion does not bind candidate evidence" in controller
    assert "candidate evidence does not bind trusted stage handoff" in controller
    assert "candidate-evidence-already-exists" in controller
    stage_lock = controller.index("flock -n 8")
    first_transport_parse = controller.index('git bundle list-heads "$bundle"')
    assert stage_lock < first_transport_parse
    assert 'exec 8>"$STATE_ROOT/.controller.lock"' in controller
    assert 'chown root:root "$STATE_ROOT/.controller.lock"' in controller

    consume = candidate.index("consume_trusted_stage_handoff\n")
    archive_parse = candidate.index('python3 - "$archive"')
    bundle_parse = candidate.index('git bundle list-heads "$bundle"')
    first_build = candidate.index("docker build --pull")
    assert consume < archive_parse < first_build
    assert consume < bundle_parse < first_build
    assert "missing-trusted-stage-handoff" in candidate  # direct invocation fails closed
    assert 'mv -T -- "$trusted_stage_handoff" "$trusted_stage_handoff_consumed"' in candidate
    assert "replayed-trusted-stage-handoff" in candidate
    assert '"$(sha256sum "$trusted_stage_handoff" | awk \'{print $1}\')" ==' in candidate
    assert "trusted-stage-handoff-digest" in candidate
    assert "trusted-stage-handoff-consumption" in candidate
    assert "trusted_stage_controller_sha256" in candidate
    assert "trusted_stage_handoff_sha256" in candidate
    assert "trusted_stage_handoff_nonce" in candidate
    assert "trusted_stage_controller_sha256" in exact
    assert "trusted_stage_handoff_sha256" in exact
    assert "trusted_stage_handoff_nonce" in exact
    assert "TRUSTED_STAGE_CONTROLLER" in validator
    assert "trusted stage handoff/controller evidence is not bound" in validator


def test_trusted_controller_and_stage_gate_ignore_inherited_path() -> None:
    controller = _read("deploy/trusted_exact_rehearsal_controller.sh")
    stage = _read("deploy/stage_release_candidate.sh")
    assert controller.startswith("#!/bin/bash -p\n")
    assert stage.startswith("#!/bin/bash -p\n")
    assert controller.index("export PATH=/usr/sbin:/usr/bin:/sbin:/bin") < controller.index(
        '[[ "$(id -u)" == "0" ]]'
    )
    assert stage.index("export PATH=/usr/sbin:/usr/bin:/sbin:/bin") < stage.index(
        '[[ "$(id -u)" == "0" ]]'
    )
    assert "unset CDPATH ENV BASH_ENV" in controller
    assert "hash -r" in controller


@pytest.mark.skipif(os.name == "nt", reason="requires a native POSIX shell")
def test_trusted_controller_rejects_before_hostile_path_commands_run(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    assert bash
    marker = tmp_path / "hostile-command-ran"
    for command in ("id", "git"):
        fake = tmp_path / command
        fake.write_text(
            f"#!/bin/sh\nprintf '%s' {command} >>'{marker}'\nexit 0\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
    environment = {
        "PATH": str(tmp_path),
        "LECTURESIFT_EXPECTED_REHEARSAL_REVISION": "a" * 40,
    }
    completed = subprocess.run(
        [bash, "-p", str(ROOT / "deploy/trusted_exact_rehearsal_controller.sh")],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode != 0
    assert not marker.exists()


def test_candidate_docker_build_is_after_independent_review_gate() -> None:
    stage = _read("deploy/stage_release_candidate.sh")
    controller = _read("deploy/trusted_exact_rehearsal_controller.sh")
    authorization = stage.index("LECTURESIFT_TRUSTED_CONTROLLER_MODE=authorize-build")
    first_build = stage.index("docker build --pull")
    assert authorization < first_build
    assert "candidate-build-not-authorized" in stage
    assert "invalid-candidate-build-authorization" in stage
    assert (
        "TRUSTED_CANDIDATE_BUILD_AUTHORIZED|revision=$revision|tree=$tree_sha256"
        in stage
    )
    mode_branch = controller.index('if [[ "$requested_mode" == "authorize-build" ]]')
    evidence_gate = controller.index('[[ -f "$evidence" && ! -L "$evidence"')
    assert controller.index("unreviewed-source-tree") < mode_branch < evidence_gate
    assert controller.index("unreviewed-orchestrator") < mode_branch


def test_fixed_stage_controller_authorizes_tree_before_candidate_code() -> None:
    trusted = _read("deploy/trusted_stage_release_controller.sh")
    generator = _read("deploy/generate_stage_release_wrapper.py")
    assert trusted.startswith("#!/bin/bash -p\n")
    size_gate = trusted.index("fail oversized-transport")
    first_import = trusted.index(" bundle verify ")
    tree_gate = trusted.index("fail unreviewed-source-tree")
    candidate_exec = trusted.index('bash -p "$candidate"')
    assert size_gate < first_import < tree_gate < candidate_exec
    assert '"${reviewed[version]:-}" == "2"' in trusted
    assert "trusted_stage_controller_sha256" in trusted
    assert "archive does not equal reviewed bundle revision" in trusted
    assert "stage_release_candidate.sh" in trusted
    assert "controller=/usr/local/sbin/lecturesift-release-stage-controller" in generator
    assert "git clone" not in generator
    assert 'tarfile.open(path, "r|")' in trusted
    assert "insufficient-stage-disk-reserve" in trusted
    assert "git-object-bound" in trusted
    assert "timeout --signal=KILL" in trusted
    assert "ulimit -v" in trusted and "ulimit -t" in trusted


def test_rehearsal_backend_is_dedicated_and_production_redis_is_unreachable() -> None:
    stack = _read("deploy/rehearsal_stack.sh")
    exact = _read("deploy/run_exact_rehearsal.sh")
    restore = _read("deploy/rehearsal_restore.sh")
    assert 'rehearsal_backend_network="lecturesift_rehearsal_backend"' in stack
    assert 'rehearsal_backend_network="lecturesift_backend"' not in stack
    assert 'docker network connect --alias postgres "$rehearsal_backend_network"' in stack
    assert "production Redis became resolvable" in stack
    assert 'for host in ("redis", "lecturesift-redis-1")' in stack
    assert "lecturesift_rehearsal_backend" in exact
    assert "lecturesift_rehearsal_backend" in restore
    assert 'docker network disconnect "$network" lecturesift-postgres-1' in restore
    assert 'docker network disconnect "$identifier" lecturesift-postgres-1' in exact
    stale = exact.split("reconcile_stale_labeled_resources() {", 1)[1].split(
        "run_inner_rehearsal()", 1
    )[0]
    first_delete = stale.index('docker rm -f "$identifier"')
    assert stale.index('" ${allowed_containers[*]} " == *" $name "*') < first_delete
    assert stale.index('" ${allowed_volumes[*]} " == *" $name "*') < first_delete
    assert stale.index('" ${allowed_networks[*]} " == *" $name "*') < first_delete
    assert stale.index('"$endpoint" == "lecturesift-postgres-1"') < first_delete
    assert "run_inner_rehearsal --reconcile-stale" in exact
    assert exact.index("run_inner_rehearsal --reconcile-stale") < exact.index(
        "run_inner_rehearsal --reconcile-only"
    )
    restore = _read("deploy/rehearsal_restore.sh")
    assert '--reconcile-stale) rehearsal_mode="reconcile-stale"' in restore
    assert "REHEARSAL_STALE_RECONCILE_OK" in restore
    stale_db = restore.split("reconcile_stale_rehearsal_state() {", 1)[1].split(
        'if [[ "$rehearsal_mode" == "reconcile-stale" ]]', 1
    )[0]
    first_drop = stale_db.index('exec -T postgres dropdb')
    assert stale_db.index('validated_databases+=("$database")') < first_drop
    assert stale_db.index('validated_role_only_databases+=("$database")') < first_drop
    assert "--opt type=tmpfs --opt device=tmpfs" in stack
    assert '"size=" + os.environ["EXPECTED_BYTES"]' in stack
    assert "candidate work-volume quota probe failed" in stack


def test_candidate_migration_has_a_one_use_internal_postgres_only_network() -> None:
    provision = _read("deploy/provision_database_role.sh")
    restore = _read("deploy/rehearsal_restore.sh")
    exact = _read("deploy/run_exact_rehearsal.sh")
    preflight = _read("deploy/preflight.sh")

    assert "--network lecturesift_backend" not in provision
    assert 'migration_network="lecturesift_rehearsal_migration"' in provision
    assert 'migration_container="lecturesift-migration-rehearsal"' in provision
    assert 'migration_purpose="candidate-database-migration"' in provision
    assert "docker network create --driver bridge --internal" in provision
    assert 'docker network connect --alias postgres "$migration_network_id"' in provision
    assert '--network "$migration_network_id"' in provision
    assert '"$redis_networks" == "lecturesift_backend"' in provision
    assert 'for host in ("redis", "lecturesift-redis-1", "lecturesift-redis-rehearsal")' in provision
    assert 'os.environ["LECTURESIFT_FORBIDDEN_REDIS_IP"]' in provision
    assert "production Redis became reachable during candidate migration" in provision
    assert "MIGRATION_REDIS_ISOLATION_OK" in provision
    assert "validate_migration_network_topology false" in provision
    assert "validate_migration_network_topology true" in provision
    assert "cleanup_candidate_migration_on_exit" in provision
    assert provision.index("docker network create --driver bridge --internal") < provision.index(
        'docker start "$migration_container_id"'
    ) < provision.index("cleanup_candidate_migration ||")

    for script in (restore, exact):
        assert "lecturesift-migration-rehearsal" in script
        assert "lecturesift_rehearsal_migration" in script
        assert "candidate-database-migration" in script
        assert "lecturesift-source-postgres-rehearsal" in script
        assert "source-postgres-client" in script
        assert '"$driver" == "bridge"' in script
        assert '"$scope" == "local"' in script
    assert 'lecturesift.rehearsal.run=$stamp' not in restore
    assert restore.count('lecturesift.rehearsal.run=$rehearsal_suffix') >= 4
    assert "fixed-name candidate migration container remains" in preflight
    assert "fixed-name candidate migration network remains" in preflight
    assert 'index .NetworkSettings.Networks "lecturesift_rehearsal_migration"' in preflight
    assert "production PostgreSQL remains attached to the candidate migration network" in preflight


def test_git_export_attributes_cannot_hide_root_executed_candidate_files() -> None:
    for relative in (
        "deploy/trusted_stage_release_controller.sh",
        "deploy/trusted_exact_rehearsal_controller.sh",
        "deploy/stage_release_candidate.sh",
        "deploy/run_exact_rehearsal.sh",
        "deploy/release.sh",
    ):
        script = _read(relative)
        assert "GIT_ATTR_NOSYSTEM=1" in script
        if relative != "deploy/release.sh":
            assert "export-attributes-forbidden" in script or ".gitattributes" in script
            assert "core.attributesFile=/dev/null" in script
    validator = _read("deploy/validate_rehearsal_admission.py")
    assert "Git export attributes are forbidden in admitted trees" in validator
    assert 'git_environment["GIT_ATTR_NOSYSTEM"] = "1"' in validator
    assert 'mode="r|"' in validator
    assert "stream.read(1024 * 1024)" in validator
    assert "stream.read()).hexdigest()" not in validator
    stage = _read("deploy/stage_release_candidate.sh")
    assert "path.read_bytes()" not in stage


def test_export_ignore_attack_is_visible_to_the_fail_closed_gate(tmp_path: Path) -> None:
    git = shutil.which("git")
    assert git
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run([git, "init", "--quiet"], cwd=repository, check=True)
    subprocess.run([git, "config", "user.email", "security@example.invalid"], cwd=repository, check=True)
    subprocess.run([git, "config", "user.name", "Security Test"], cwd=repository, check=True)
    (repository / ".gitattributes").write_text(
        "deploy/hidden-root-helper.sh export-ignore\n", encoding="utf-8"
    )
    helper = repository / "deploy" / "hidden-root-helper.sh"
    helper.parent.mkdir()
    helper.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    subprocess.run([git, "add", "."], cwd=repository, check=True)
    subprocess.run([git, "commit", "--quiet", "-m", "attack"], cwd=repository, check=True)

    archive = subprocess.run(
        [git, "-c", "core.attributesFile=/dev/null", "archive", "--format=tar", "HEAD"],
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        env={**os.environ, "GIT_ATTR_NOSYSTEM": "1"},
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
        assert "deploy/hidden-root-helper.sh" not in stream.getnames()
    detected = subprocess.run(
        [
            git, "ls-tree", "-rz", "--name-only", "HEAD",
        ],
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        env={**os.environ, "GIT_ATTR_NOSYSTEM": "1"},
    ).stdout.split(b"\0")
    assert b".gitattributes" in detected


def test_production_preflight_fail_stops_on_rehearsal_crash_residue() -> None:
    preflight = _read("deploy/preflight.sh")
    assert preflight.count("--filter label=lecturesift.rehearsal=true") == 3
    assert "rehearsal containers remain after an interrupted run" in preflight
    assert "rehearsal volumes remain after an interrupted run" in preflight
    assert "rehearsal networks remain after an interrupted run" in preflight
    assert 'index .NetworkSettings.Networks "lecturesift_rehearsal_backend"' in preflight
    assert "production PostgreSQL remains attached to the rehearsal backend" in preflight
    exact = _read("deploy/run_exact_rehearsal.sh")
    lock = exact.index("flock -n 7")
    reconcile = exact.index("reconcile_stale_labeled_resources ||")
    inner = exact.index("run_inner_rehearsal --reconcile-only")
    assert lock < reconcile < inner
    assert "unsafe-or-recent-rehearsal-runtime-residue" in exact
    assert "time.time() - created > 3600" in exact
    assert "Refusing" not in exact.split("reconcile_stale_labeled_resources()", 1)[1].split(
        "run_inner_rehearsal()", 1
    )[0]
    assert 'docker network disconnect "$identifier" lecturesift-postgres-1' in exact


def test_stage_bounds_untrusted_inputs_and_only_removes_owned_resources() -> None:
    stage = _read("deploy/stage_release_candidate.sh")
    first_build = stage.index("docker build --pull")
    for guard in (
        "MAX_TRANSPORT_BYTES",
        "MAX_EXPANDED_BYTES",
        "MAX_TREE_ENTRIES",
        "oversized-transport",
        "archive exceeds reviewed expansion bounds",
        "expanded-tree-bound",
        "target-image-already-exists",
    ):
        assert guard in stage
        assert stage.index(guard) < first_build
    assert "remove_created_tree()" in stage
    assert '"$(stat -c \'%d:%i\' -- "$path")" == "$expected_identity"' in stage
    assert 'mounts="$(findmnt -rn -o TARGET)"' in stage
    assert 'rm -rf --one-file-system -- "$resolved"' in stage
    assert 'rm -rf -- "$release"' not in stage
    assert 'rm -rf -- "$worktree"' not in stage
    assert '[[ "$created_release" == "false" ]]' in stage
    assert '[[ "$created_worktree" == "false" ]]' in stage
    assert '[[ "$created_app_image" == "true" ]]' in stage
    assert '[[ "$created_proxy_image" == "true" ]]' in stage


def test_rehearsal_stop_gate_includes_production_egress_proxy_and_caddy() -> None:
    gate = _read("deploy/assert_rehearsal_production_stopped.sh")
    exact = _read("deploy/run_exact_rehearsal.sh")
    stack = _read("deploy/rehearsal_stack.sh")
    for container in (
        "lecturesift-api-1",
        "lecturesift-worker-1",
        "lecturesift-caddy-1",
        "lecturesift-egress-proxy-1",
    ):
        assert container in gate
    assert "active-production-container:$container" in gate
    assert 'bash "$root/deploy/assert_rehearsal_production_stopped.sh"' in exact
    assert stack.count(
        'bash "$ROOT_DIR/deploy/assert_rehearsal_production_stopped.sh"'
    ) >= 3
    assert stack.index(
        'bash "$ROOT_DIR/deploy/assert_rehearsal_production_stopped.sh"'
    ) < stack.index('bash "$ROOT_DIR/deploy/release.sh" build')


def test_exact_cleanup_is_run_bound_and_old_artifact_cleanup_is_mount_safe() -> None:
    exact = _read("deploy/run_exact_rehearsal.sh")
    restore = _read("deploy/rehearsal_restore.sh")
    assert "expected_rehearsal_run_id" in exact
    assert "expected_rehearsal_suffix" in exact
    assert 'LECTURESIFT_EXPECTED_REHEARSAL_RUN_ID="$expected_rehearsal_run_id"' in exact
    assert exact.count(
        '--filter "label=lecturesift.rehearsal.run=$expected_rehearsal_suffix"'
    ) == 3
    assert "allowed_containers" in exact
    assert "allowed_volumes" in exact
    assert "allowed_networks" in exact
    assert 'docker container inspect --format' in exact
    assert 'docker volume inspect --format' in exact
    assert 'docker network inspect --format' in exact
    assert '"${new_rehearsal_runs[0]}" == "$expected_rehearsal_run_id"' in exact

    cleanup = restore.split("cleanup_expired_rehearsal_runs() {", 1)[1].split(
        "cleanup_rehearsal() {", 1
    )[0]
    assert '"$candidate" == "$BACKUP_ROOT/$name"' in cleanup
    assert '"$mode" == "0:0:700"' in cleanup
    assert 'resolved="$(realpath -e -- "$candidate")"' in cleanup
    assert 'mounts="$(findmnt -rn -o TARGET)"' in cleanup
    assert 'rm -rf --one-file-system -- "$candidate"' in cleanup
    assert "-exec rm -rf" not in restore


@pytest.mark.skipif(os.name == "nt", reason="requires a native POSIX shell")
def test_rehearsal_stop_gate_fails_closed_when_production_proxy_runs(
    tmp_path: Path,
) -> None:
    bash = shutil.which("bash")
    assert bash
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        """#!/bin/sh
if [ "$1" = container ] && [ "$2" = inspect ]; then
  last=""
  for argument in "$@"; do last="$argument"; done
  if [ "$3" = --format ]; then
    if [ "$last" = "${ACTIVE_CONTAINER:-}" ]; then printf 'true\\n'; else printf 'false\\n'; fi
  fi
  exit 0
fi
exit 2
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    system_path = os.environ.get("PATH", "")
    environment = {
        "PATH": f"{tmp_path}{os.pathsep}{system_path}",
        "ACTIVE_CONTAINER": "lecturesift-egress-proxy-1",
    }
    failed = subprocess.run(
        [bash, str(ROOT / "deploy/assert_rehearsal_production_stopped.sh")],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
    )
    assert failed.returncode != 0
    assert "active-production-container:lecturesift-egress-proxy-1" in failed.stderr

    environment["ACTIVE_CONTAINER"] = ""
    passed = subprocess.run(
        [bash, str(ROOT / "deploy/assert_rehearsal_production_stopped.sh")],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
    )
    assert passed.returncode == 0
    assert passed.stdout.strip() == "REHEARSAL_PRODUCTION_STOP_GATE_OK"
