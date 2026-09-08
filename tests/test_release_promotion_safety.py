from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _usable_bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        if os.name == "nt":
            pytest.skip("Bash is unavailable on this Windows host")
        pytest.fail("Bash is required on Linux deployment/CI hosts")
    try:
        probe = subprocess.run(
            [bash, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if os.name == "nt":
            pytest.skip(f"Bash is not usable on this Windows host: {exc}")
        raise
    if probe.returncode != 0:
        if os.name == "nt":
            pytest.skip("the Windows Bash shim has no usable runtime")
        pytest.fail(probe.stderr or "Bash runtime probe failed")
    return bash


def _systemd_safety_functions(path: str) -> str:
    script = _read(path)
    return script[script.index("fail() {") : script.index("for command_name")]


def _run_systemd_safety_functions(
    path: str, body: str, *, listed: bool = False, state: str = "inactive"
) -> subprocess.CompletedProcess[str]:
    bash = _usable_bash()
    harness = f"""\
set -Eeuo pipefail
{_systemd_safety_functions(path)}
systemctl() {{
  case "${{1:-}}" in
    list-units)
      [[ "$#" == 6 && "$2" == --all && "$3" == --type=service && \
         "$4" == --no-legend && "$5" == --plain && \
         "$6" == 'lecturesift-backup-alert@*.service' ]] || return 91
      if [[ "${{FAKE_LISTED:-0}}" == 1 ]]; then
        printf '%s loaded active running test instance\\n' \
          'lecturesift-backup-alert@probe.service'
      fi
      ;;
    show)
      [[ "$#" == 4 && "$2" == --property=ActiveState && "$3" == --value && \
         "$4" == lecturesift-backup-alert@probe.service ]] || return 92
      printf '%s\\n' "$FAKE_ACTIVE_STATE"
      ;;
    *)
      return 93
      ;;
  esac
}}
{body}
"""
    environment = os.environ.copy()
    environment.update(
        FAKE_LISTED="1" if listed else "0",
        FAKE_ACTIVE_STATE=state,
    )
    return subprocess.run(
        [bash, "-c", harness],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
    )


def test_promotion_is_same_filesystem_fail_stop_and_idempotent():
    script = _read("deploy/promote_rehearsed_release.sh")

    assert "PREVIOUS_ROOT=/opt/.lecturesift-previous" in script
    assert 'stat -c \'%d\' -- /opt' in script
    assert "release-promotion.in-progress" in script
    assert "an interrupted release promotion requires operator recovery" in script
    assert "Release rollback is unproven" in script
    assert "REHEARSED_RELEASE_ALREADY_CURRENT" in script
    assert "old_revision=\"${target_revision:-none}\"" in script
    assert "/srv/lecturesift/previous" not in script
    assert 'exec 9<>"$LOCK_FILE"' in script
    assert 'exec 9>"$LOCK_FILE"' not in script
    assert "PRESERVE-UNVERIFIED-LEGACY-ROOT" in script
    assert '"ubuntu:ubuntu:775"' in script
    assert "the one-time legacy root contains a nested mount" in script
    assert "legacy_tree_identity" in script
    assert "legacy-unverified" in script
    assert "promoted_code_and_units_are_exact()" in script
    assert "reached the commit point; retaining it with the transaction marker" in script
    assert 'if [[ -d "$TARGET_ROOT/.git" && ! -L "$TARGET_ROOT/.git"' in script
    assert script.index('if [[ -d "$TARGET_ROOT/.git"') < script.index(
        'git -C "$TARGET_ROOT" rev-parse'
    )
    assert script.index('sync -f -- "$INCOMING_ROOT"') < script.index(
        'mv -T -- "$INCOMING_ROOT" "$TARGET_ROOT"'
    )


def test_systemd_install_uses_atomic_fragments_secure_lock_and_noop_retry():
    script = _read("deploy/install_systemd_units.sh")

    assert "atomic_install()" in script
    assert 'mv -fT -- "$temporary" "$destination"' in script
    assert "SYSTEMD_UNITS_ALREADY_CURRENT" in script
    assert "SYSTEMD_UNIT_ROLLBACK_UNPROVEN" in script
    assert "systemd-install.in-progress" in script
    assert "an interrupted systemd installation requires operator recovery" in script
    assert script.index("systemd-install.in-progress") < script.index(
        'atomic_install "$ROOT_DIR/deploy/$unit"'
    )
    assert "RUNTIME_ROOT=/run/lecturesift" in script
    assert 'exec 9<>"$LOCK_FILE"' in script
    assert 'exec 9>"$LOCK_FILE"' not in script
    assert 'for unit in "${units[@]}"; do' in script
    assert "lecturesift-instagram.timer" in script
    assert "lecturesift-ingress-selector.service" in script
    assert "systemd_query_unit()" in script
    assert "lecturesift-backup-alert@lecturesift-backup.service.service" in script
    assert "assert_unit_inactive()" in script
    assert "systemctl list-units --all --type=service --no-legend --plain" in script
    assert "systemctl show --property=ActiveState --value" in script
    assert "'lecturesift-backup-alert@*.service'" in script
    assert 'systemctl is-active --quiet "$unit"' in script
    assert 'systemctl is-active --quiet "$query_unit"' not in script
    assert 'systemctl show --property=FragmentPath --value "$query_unit"' in script
    assert 'systemctl show --property=NeedDaemonReload --value "$query_unit"' in script
    assert 'systemctl show --property=FragmentPath --value "$unit"' not in script
    assert 'systemctl show --property=NeedDaemonReload --value "$unit"' not in script
    disabled_gate = script.split("for unit in lecturesift.service", 1)[1].split("do", 1)[0]
    assert "lecturesift-ingress-selector.service" not in disabled_gate

    preflight = _read("deploy/preflight.sh")
    assert "release-promotion.in-progress" in preflight
    assert "systemd-install.in-progress" in preflight

    promotion = _read("deploy/promote_rehearsed_release.sh")
    assert "systemd_query_unit()" in promotion
    assert "lecturesift-backup-alert@lecturesift-backup.service.service" in promotion
    assert "assert_unit_inactive()" in promotion
    assert "systemctl list-units --all --type=service --no-legend --plain" in promotion
    assert "systemctl show --property=ActiveState --value" in promotion
    assert "'lecturesift-backup-alert@*.service'" in promotion
    assert 'systemctl is-active --quiet "$unit"' in promotion
    assert 'systemctl is-active --quiet "$query_unit"' not in promotion
    assert 'systemctl show --property=FragmentPath --value "$query_unit"' in promotion
    assert 'systemctl show --property=NeedDaemonReload --value "$query_unit"' in promotion
    assert 'systemctl show --property=FragmentPath --value "$unit"' not in promotion
    assert 'systemctl show --property=NeedDaemonReload --value "$unit"' not in promotion


@pytest.mark.parametrize(
    "path",
    ("deploy/install_systemd_units.sh", "deploy/promote_rehearsed_release.sh"),
)
@pytest.mark.parametrize("state", ("active", "activating", "reloading", "deactivating"))
def test_template_inactivity_gate_rejects_live_and_transitioning_instances(
    path: str, state: str
):
    result = _run_systemd_safety_functions(
        path,
        "assert_unit_inactive lecturesift-backup-alert@.service",
        listed=True,
        state=state,
    )

    assert result.returncode != 0
    assert "lecturesift-backup-alert@probe.service must be inactive" in result.stderr


@pytest.mark.parametrize(
    "path",
    ("deploy/install_systemd_units.sh", "deploy/promote_rehearsed_release.sh"),
)
@pytest.mark.parametrize(
    ("listed", "state"),
    ((False, "inactive"), (True, "inactive"), (True, "failed")),
)
def test_template_inactivity_gate_accepts_only_stopped_instances(
    path: str, listed: bool, state: str
):
    result = _run_systemd_safety_functions(
        path,
        "assert_unit_inactive lecturesift-backup-alert@.service\nprintf 'ACCEPTED\\n'",
        listed=listed,
        state=state,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout == "ACCEPTED\n"


@pytest.mark.parametrize(
    "path",
    ("deploy/install_systemd_units.sh", "deploy/promote_rehearsed_release.sh"),
)
def test_template_property_probe_matches_the_tracked_on_failure_instance(path: str):
    trigger_unit = "lecturesift-backup.service"
    on_failure = next(
        line.removeprefix("OnFailure=")
        for line in _read(f"deploy/{trigger_unit}").splitlines()
        if line.startswith("OnFailure=")
    )
    expected_instance = on_failure.replace("%n", trigger_unit)

    result = _run_systemd_safety_functions(
        path,
        "systemd_query_unit lecturesift-backup-alert@.service",
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout.strip() == expected_instance


def test_exact_rehearsal_promotion_step_is_explicit_and_matches_tool_contract():
    docs = _read("VPS_DEPLOYMENT.md")

    command = "LECTURESIFT_PROMOTE_RELEASE_CONFIRM=PROMOTE-EXACT-REHEARSED-RELEASE"
    assert command in docs
    assert "deploy/promote_rehearsed_release.sh" in docs
    assert "/opt/.lecturesift-previous" in docs
    assert "two atomic\nsame-filesystem renames under a durable fail-stop transaction marker" in docs
    assert "/var/lib/lecturesift/release-promotion/release-promotion.in-progress" in docs
    rehearsal = docs.index("After the exact rehearsal succeeds")
    promotion = docs.index(command, rehearsal)
    staging = docs.index("systemctl start lecturesift-caddy-staging.service", promotion)
    assert rehearsal < promotion < staging
    assert "Do not copy systemd fragments manually" in docs
    selector_stop = docs.index("sudo systemctl stop lecturesift-ingress-selector.service")
    core_stop = docs.index("sudo systemctl stop lecturesift-ingress.service lecturesift.service")
    assert selector_stop < core_stop
