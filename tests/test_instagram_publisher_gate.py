from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_instagram_publishers_stopped",
    ROOT / "deploy" / "verify_instagram_publishers_stopped.py",
)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


def _envs(tmp_path: Path, *, runtime_value: str = "false") -> tuple[Path, Path]:
    runtime = tmp_path / "runtime.env"
    generated = tmp_path / "instagram.env"
    runtime.write_text(
        f"OTHER=value\nINSTAGRAM_DAILY_AUTOMATION_ENABLED={runtime_value}\n",
        encoding="utf-8",
    )
    generated.write_text(
        "# generated\nINSTAGRAM_DAILY_AUTOMATION_ENABLED=false\n",
        encoding="utf-8",
    )
    return runtime, generated


def _runner(
    *,
    timer_enablement: str = "disabled",
    service_enablement: str = "static",
    active_unit: str = "",
    container: str = "",
):
    def run(command, **_kwargs):
        if command[:4] == ["systemctl", "show", "--property=ActiveState", "--value"]:
            state = "active" if command[-1] == active_unit else "inactive"
            return subprocess.CompletedProcess(command, 0, state + "\n", "")
        if command[:2] == ["systemctl", "is-enabled"]:
            value = timer_enablement if command[-1].endswith(".timer") else service_enablement
            return subprocess.CompletedProcess(command, 0 if value != "disabled" else 1, value + "\n", "")
        if command[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(command, 0, container, "")
        raise AssertionError(command)

    return run


def test_instagram_publisher_gate_accepts_only_disabled_stopped_state(tmp_path: Path):
    runtime, generated = _envs(tmp_path)
    gate.verify_publishers_stopped(runtime, generated, runner=_runner())


def test_instagram_publisher_gate_rejects_true_runtime_config(tmp_path: Path):
    runtime, generated = _envs(tmp_path, runtime_value="true")
    with pytest.raises(gate.PublisherStateError, match="exactly false"):
        gate.verify_publishers_stopped(runtime, generated, runner=_runner())


def test_instagram_publisher_gate_rejects_enabled_persistent_timer(tmp_path: Path):
    runtime, generated = _envs(tmp_path)
    with pytest.raises(gate.PublisherStateError, match="boot enablement"):
        gate.verify_publishers_stopped(
            runtime, generated, runner=_runner(timer_enablement="enabled")
        )


@pytest.mark.parametrize(
    "active_unit",
    ("lecturesift-instagram.timer", "lecturesift-instagram.service"),
)
def test_instagram_publisher_gate_rejects_active_unit(
    tmp_path: Path, active_unit: str
):
    runtime, generated = _envs(tmp_path)
    with pytest.raises(gate.PublisherStateError, match="active or unprovable"):
        gate.verify_publishers_stopped(
            runtime, generated, runner=_runner(active_unit=active_unit)
        )


def test_instagram_publisher_gate_rejects_enabled_service(tmp_path: Path):
    runtime, generated = _envs(tmp_path)
    with pytest.raises(gate.PublisherStateError, match="boot enablement"):
        gate.verify_publishers_stopped(
            runtime, generated, runner=_runner(service_enablement="enabled")
        )


def test_instagram_publisher_gate_rejects_running_jobs_container(tmp_path: Path):
    runtime, generated = _envs(tmp_path)
    with pytest.raises(gate.PublisherStateError, match="container"):
        gate.verify_publishers_stopped(
            runtime, generated, runner=_runner(container="container-id\n")
        )
