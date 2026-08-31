from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from fastapi.responses import JSONResponse

import lecturesift.rollout_routes as rollout_routes


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _service_block(compose: str, name: str, next_name: str) -> str:
    return compose.split(f"  {name}:\n", 1)[1].split(f"\n  {next_name}:\n", 1)[0]


def test_production_services_have_hard_and_soft_resource_boundaries():
    compose = _read("compose.yaml")
    blocks = {
        "caddy": _service_block(compose, "caddy", "api"),
        "api": _service_block(compose, "api", "worker"),
        "worker": _service_block(compose, "worker", "egress-proxy"),
        "postgres": _service_block(compose, "postgres", "redis"),
        "redis": compose.split("  redis:\n", 1)[1].split("\nnetworks:\n", 1)[0],
    }
    for name, block in blocks.items():
        assert "cpus:" in block, name
        assert "cpu_shares:" in block, name
        assert "mem_limit:" in block, name
        assert "mem_reservation:" in block, name
        assert "memswap_limit:" in block, name
        assert "pids_limit:" in block, name
        assert "tmpfs:" in block, name

    assert "--concurrency=1" in blocks["worker"]
    assert "--max-tasks-per-child=10" in blocks["worker"]
    assert "--max-memory-per-child=2500000" in blocks["worker"]
    assert "temp_file_limit=256MB" in blocks["postgres"]
    assert "max_connections=50" in blocks["postgres"]
    assert "maxmemory 512mb" in _read("deploy/redis.conf")


def test_worker_can_reach_internet_only_through_private_range_denying_proxy():
    compose = _read("compose.yaml")
    worker = _service_block(compose, "worker", "egress-proxy")
    proxy = _service_block(compose, "egress-proxy", "instagram")
    squid = _read("deploy/egress-proxy/squid.conf")

    assert "HTTP_PROXY: http://egress-proxy:3128" in worker
    assert "HTTPS_PROXY: http://egress-proxy:3128" in worker
    assert "http_proxy: http://egress-proxy:3128" in worker
    assert "https_proxy: http://egress-proxy:3128" in worker
    assert "- backend" in worker
    assert "- egress" not in worker
    assert "condition: service_healthy" in worker
    assert "- backend" in proxy and "- egress" in proxy
    assert "read_only: true" in proxy
    assert "http_access deny forbidden_destination" in squid
    for network in (
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "fc00::/7",
        "fe80::/10",
    ):
        assert network in squid
    assert squid.index("http_access deny forbidden_destination") < squid.index(
        "http_access allow all"
    )


def test_isolated_rehearsal_uses_and_cleans_the_same_egress_proxy():
    stack = _read("deploy/rehearsal_stack.sh")
    orchestrator = _read("deploy/rehearsal_restore.sh")

    assert 'rehearsal_proxy_container="lecturesift-egress-proxy-rehearsal"' in stack
    assert 'bash "$ROOT_DIR/deploy/release.sh" build' in stack
    assert '--use-aliases egress-proxy' in stack
    assert stack.index('--use-aliases egress-proxy') < stack.index(
        'lecturesift-worker-rehearsal --no-deps'
    )
    assert "lecturesift-egress-proxy-rehearsal" in orchestrator


def test_resource_guard_reserves_host_capacity_and_bounds_work_volumes():
    guard = _read("deploy/resource_guard.sh")
    env_example = _read("deploy/env.example")
    preflight = _read("deploy/preflight.sh")

    assert "MIN_HOST_CPUS=4" in guard
    assert "MIN_HOST_MEMORY_BYTES=7516192768" in guard
    assert r'printf "%.0f\n", $2 * 1024' in guard
    assert r'printf "%.0f\\n", $2 * 1024' not in guard
    assert "DEFAULT_HOST_DISK_RESERVE_BYTES=10737418240" in guard
    assert "DEFAULT_MAX_JOB_WORK_BYTES=8589934592" in guard
    assert "disk_reserve + job_budget + max_video_bytes" in guard
    assert "max_video_bytes <= 1073741824" in guard
    assert "max_document_bytes <= 104857600" in guard
    assert "max_video_bytes * 4" in guard
    assert "lecturesift-api-work" in guard
    assert "lecturesift-worker-work" in guard
    assert "volume mountpoint escaped the Docker volume root" in guard
    assert "LECTURESIFT_HOST_DISK_RESERVE_BYTES=10737418240" in env_example
    assert "LECTURESIFT_MAX_JOB_WORK_BYTES=8589934592" in env_example
    assert 'bash "$ROOT_DIR/deploy/resource_guard.sh"' in preflight


def test_resource_guard_memory_probe_emits_only_decimal_bytes():
    guard = _read("deploy/resource_guard.sh")
    match = re.search(
        r'host_memory_bytes="\$\(awk \'([^\']+)\' /proc/meminfo\)"',
        guard,
    )
    assert match is not None
    program = match.group(1)
    assert r"\\n" not in program

    awk = shutil.which("awk")
    if awk is None:
        return
    completed = subprocess.run(
        [awk, program],
        input="MemTotal:        8060928 kB\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert completed.stdout == f"{8060928 * 1024}\n"


def test_api_health_requires_rollout_and_r2_before_caddy_can_start():
    compose = _read("compose.yaml")
    api = _service_block(compose, "api", "worker")
    caddy = _service_block(compose, "caddy", "api")
    rollout = _read("lecturesift/rollout_routes.py")

    assert "/health" in api
    assert "/billing/health" in api
    assert "/rollout/health" in api
    assert "durable_processing_ready" in api
    assert "storage" in api and "connected" in api
    assert "api:\n        condition: service_healthy" in caddy
    assert '"ready": deployment_ready' in rollout
    assert "health_uri /rollout/health?readiness=true" in _read("Caddyfile")
    assert "JSONResponse(status_code=503, content=payload)" in rollout


def test_rollout_readiness_returns_503_when_r2_is_disconnected(monkeypatch):
    monkeypatch.setattr(
        rollout_routes.JOBS,
        "redis_health",
        lambda: {"configured": True, "connected": True},
    )
    monkeypatch.setattr(
        rollout_routes.STORAGE,
        "health",
        lambda: {"configured": True, "connected": False},
    )
    monkeypatch.setattr(
        rollout_routes,
        "worker_health",
        lambda: {"configured": True, "reachable": True, "workers": 1},
    )

    diagnostic = rollout_routes.rollout_health()
    readiness = rollout_routes.rollout_health(readiness=True)

    assert isinstance(diagnostic, dict)
    assert diagnostic["ready"] is False
    assert isinstance(readiness, JSONResponse)
    assert readiness.status_code == 503


def test_docker_restart_policy_cannot_bypass_systemd_preflight():
    compose = _read("compose.yaml")
    service = _read("deploy/lecturesift.service")
    docs = _read("VPS_DEPLOYMENT.md")

    assert 'restart: "always"' not in compose
    assert 'restart: "unless-stopped"' not in compose
    assert compose.count('restart: "on-failure:5"') >= 5
    assert "BindsTo=docker.service" in service
    assert "PartOf=docker.service" in service
    assert "ExecStartPre=/bin/bash /opt/lecturesift/deploy/preflight.sh" in service
    assert "systemd owns" in docs
    assert "on-failure" in docs
