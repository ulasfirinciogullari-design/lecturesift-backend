from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


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
    disabled_gate = script.split("for unit in lecturesift.service", 1)[1].split("do", 1)[0]
    assert "lecturesift-ingress-selector.service" not in disabled_gate

    preflight = _read("deploy/preflight.sh")
    assert "release-promotion.in-progress" in preflight
    assert "systemd-install.in-progress" in preflight


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
