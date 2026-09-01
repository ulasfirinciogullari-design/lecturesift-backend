from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SECRET_BEARING_SCRIPTS = (
    "finalize_provider_cutover.sh",
    "migrate_postgres.sh",
    "migrate_redis_state.sh",
    "postgres-app-role.sh",
    "postgres_role_login_probe.sh",
    "preflight.sh",
    "provision_database_role.sh",
    "rehearsal_restore.sh",
    "rehearsal_stack.sh",
    "rollback_postgres_to_render.sh",
    "run_exact_rehearsal.sh",
    "seed_first_cutover_backup.sh",
    "target_redis_manifest.sh",
    "verify_provider_first_start.sh",
)


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

    # WSL's legacy bash.exe can report a valid version while being unable to
    # translate the Windows working directory.  Treat that host-only adapter
    # limitation as a skip; Linux CI still fails closed.
    path_probe = subprocess.run(
        [bash, "-c", "test -f deploy/check_shell_syntax.sh"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if path_probe.returncode != 0:
        if os.name == "nt":
            pytest.skip("the Windows Bash shim cannot access the repository")
        pytest.fail(path_probe.stderr or "Bash cannot access the repository")
    return bash


def test_deployment_shell_scripts_parse_with_bash():
    bash = _usable_bash()
    result = subprocess.run(
        [bash, "deploy/check_shell_syntax.sh"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout.startswith("SHELL_SYNTAX_OK|scripts=")


def test_ci_runs_the_mandatory_shell_syntax_gate():
    workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(
        encoding="utf-8"
    )
    assert "bash deploy/check_shell_syntax.sh" in workflow
    wrapper = (ROOT / "deploy" / "run_exact_rehearsal.sh").read_text(
        encoding="utf-8"
    )
    assert 'bash "$root/deploy/check_shell_syntax.sh" || fail shell-syntax-gate' in wrapper


def test_secret_bearing_scripts_disable_inherited_xtrace_before_secret_access(
    tmp_path: Path,
):
    prologues: dict[str, list[str]] = {}
    for script_name in SECRET_BEARING_SCRIPTS:
        script = (ROOT / "deploy" / script_name).read_text(encoding="utf-8")
        lines = script.splitlines()
        assert "set +x" in lines[:5], f"{script_name} disables xtrace too late"
        prologues[script_name] = lines

    bash = _usable_bash()

    secret = "LECTURESIFT_FAKE_SECRET_MUST_NEVER_APPEAR_9f8e7d6c"
    for script_name, lines in prologues.items():
        # Execute only the real script's inert prologue through `set +x`, then
        # expand a canary secret. This proves an inherited `bash -x` setting is
        # disabled without ever running a deployment command.
        set_x_line = lines.index("set +x")
        probe = tmp_path / f"{script_name}.xtrace-probe.sh"
        with probe.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                "\n".join(lines[: set_x_line + 1])
                + f'\nprobe_value="{secret}"\n'
            )
        try:
            result = subprocess.run(
                [bash, "-x", probe.name],
                cwd=tmp_path,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            if os.name == "nt":
                pytest.skip(f"Bash is not usable on this Windows host: {exc}")
            raise
        assert result.returncode == 0, result.stderr or result.stdout
        assert secret not in result.stdout
        assert secret not in result.stderr
