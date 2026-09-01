from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

from lecturesift import config
from lecturesift.app import app
import lecturesift.rollout_routes as rollout_routes


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_build_revision_accepts_only_one_full_commit_with_provider_fallback():
    explicit = "A" * 40
    render = "b" * 40

    assert config._build_revision({"LECTURESIFT_BUILD_REVISION": explicit}) == explicit.lower()
    assert config._build_revision({"RENDER_GIT_COMMIT": render}) == render
    assert config._build_revision(
        {"LECTURESIFT_BUILD_REVISION": "unknown", "RENDER_GIT_COMMIT": render}
    ) == render
    assert config._build_revision(
        {"LECTURESIFT_BUILD_REVISION": "abc123", "RENDER_GIT_COMMIT": render}
    ) == "unknown"
    assert config._build_revision({"RENDER_GIT_COMMIT": "b" * 39}) == "unknown"
    assert config._build_revision({}) == "unknown"


def test_health_exposes_normalized_build_revision():
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json()["revision"] == config.BUILD_REVISION
    assert config.BUILD_REVISION == "unknown" or len(config.BUILD_REVISION) == 40


def test_docker_image_bakes_and_labels_the_revision():
    dockerfile = _read("Dockerfile")
    compose = _read("compose.yaml")

    assert "ARG LECTURESIFT_BUILD_REVISION=unknown" in dockerfile
    assert 'org.opencontainers.image.revision="${LECTURESIFT_BUILD_REVISION}"' in dockerfile
    assert 'LECTURESIFT_BUILD_REVISION="${LECTURESIFT_BUILD_REVISION}"' in dockerfile
    assert "LECTURESIFT_BUILD_REVISION: ${LECTURESIFT_BUILD_REVISION:-unknown}" in compose
    smoke = _read("deploy/image_smoke.py")
    assert 're.fullmatch(r"[0-9a-f]{40}", build_revision)' in smoke
    proxy_dockerfile = _read("deploy/egress-proxy/Dockerfile")
    assert "ARG LECTURESIFT_BUILD_REVISION=unknown" in proxy_dockerfile
    assert "org.opencontainers.image.revision" in proxy_dockerfile
    assert 'LECTURESIFT_BUILD_REVISION="${LECTURESIFT_BUILD_REVISION}"' in proxy_dockerfile


def test_compose_release_marker_is_separate_and_blocks_stale_api_health():
    compose = _read("compose.yaml")
    api = compose.split("  api:\n", 1)[1].split("\n  worker:\n", 1)[0]

    assert "${LECTURESIFT_RELEASE_ENV_FILE:-/run/lecturesift/release.env}" in api
    assert "LECTURESIFT_EXPECTED_BUILD_REVISION" in api
    assert "re.fullmatch(r'[0-9a-f]{40}', expected)" in api
    assert "revision == expected" in api
    assert api.index("release_ok") < api.index("rollout.get('ready')")
    rollout = _read("lecturesift/rollout_routes.py")
    assert "release_identity_ready" in rollout
    assert '"ready": deployment_ready' in rollout
    assert "if readiness and not deployment_ready" in rollout


def test_caddy_readiness_fails_when_runtime_image_is_not_expected_revision(monkeypatch):
    monkeypatch.setattr(
        rollout_routes.JOBS,
        "redis_health",
        lambda: {"configured": True, "connected": True},
    )
    monkeypatch.setattr(
        rollout_routes.STORAGE,
        "health",
        lambda: {"configured": True, "connected": True},
    )
    monkeypatch.setattr(
        rollout_routes,
        "worker_health",
        lambda: {"configured": True, "reachable": True, "workers": 1},
    )
    monkeypatch.setattr(config, "CELERY_BROKER_URL", "redis://redis:6379/0")
    monkeypatch.setattr(config, "EXPECTED_BUILD_REVISION_CONFIGURED", True)
    monkeypatch.setattr(config, "EXPECTED_BUILD_REVISION", "a" * 40)
    monkeypatch.setattr(config, "BUILD_REVISION", "b" * 40)

    diagnostic = rollout_routes.rollout_health()
    readiness = rollout_routes.rollout_health(readiness=True)

    assert diagnostic["durable_processing_ready"] is True
    assert diagnostic["release"]["ready"] is False
    assert diagnostic["ready"] is False
    assert isinstance(readiness, JSONResponse)
    assert readiness.status_code == 503


def test_release_helper_binds_clean_head_marker_label_and_image_environment():
    release = _read("deploy/release.sh")

    assert "[[ \"$(id -u)\" == \"0\" ]]" in release
    assert "rev-parse --verify 'HEAD^{commit}'" in release
    assert "status --porcelain=v1 --untracked-files=all" in release
    assert "deployment checkout is dirty or contains untracked files" in release
    assert "deployment checkout must be root-owned and not group/other writable" in release
    assert "release commits may not contain symlinks or unmaterialized submodules" in release
    assert "^LECTURESIFT_EXPECTED_BUILD_REVISION=([0-9a-f]{40})$" in release
    assert "mktemp" in release and "mv -fT" in release
    assert "org.opencontainers.image.revision" in release
    assert "LECTURESIFT_BUILD_REVISION=$expected" in release
    assert "source HEAD or cleanliness changed during release build" in release
    assert "git -c core.attributesFile=/dev/null -C \"$ROOT_DIR\"" in release
    assert "archive --format=tar \"$expected_revision\"" in release
    assert 'docker build --pull' in release
    assert 'docker image tag "$candidate_app" "$APP_IMAGE"' in release
    assert "candidate proxy image identity is invalid" in release
    assert "set -x" not in release


def test_systemd_and_recovery_paths_use_release_helper_before_compose_up():
    preflight = _read("deploy/preflight.sh")
    service = _read("deploy/lecturesift.service")
    restore = _read("deploy/restore.sh")
    rehearsal = _read("deploy/rehearsal_stack.sh")

    assert 'bash "$RELEASE_HELPER" prepare' in preflight
    start_preflight = "ExecStartPre=/bin/bash /opt/lecturesift/deploy/preflight.sh"
    start_build = "ExecStartPre=/bin/bash /opt/lecturesift/deploy/release.sh build"
    start_up = "ExecStart=/usr/bin/docker compose up"
    assert service.index(start_preflight) < service.index(start_build) < service.index(start_up)
    reload_preflight = "ExecReload=/bin/bash /opt/lecturesift/deploy/preflight.sh"
    reload_build = "ExecReload=/bin/bash /opt/lecturesift/deploy/release.sh build"
    reload_up = "ExecReload=/usr/bin/docker compose up"
    assert service.index(reload_preflight) < service.index(reload_build) < service.index(reload_up)
    assert restore.index('"$ROOT_DIR/deploy/preflight.sh"') < restore.index(
        'bash "$ROOT_DIR/deploy/release.sh" build'
    ) < restore.index("docker compose stop caddy api worker redis")
    assert 'bash "$ROOT_DIR/deploy/release.sh" prepare' in rehearsal
    assert 'bash "$ROOT_DIR/deploy/release.sh" build' in rehearsal


def test_role_files_cannot_override_baked_or_expected_revision():
    generator = _read("deploy/generate_role_envs.py")

    for key in (
        "LECTURESIFT_BUILD_REVISION",
        "LECTURESIFT_EXPECTED_BUILD_REVISION",
        "LECTURESIFT_RELEASE_ENV_FILE",
        "RENDER_GIT_COMMIT",
    ):
        assert f'"{key}"' in generator.split("HOST_ONLY_KEYS", 1)[1]


def test_github_social_checks_wait_for_exact_github_sha_first():
    workflow = _read(".github/workflows/production-social-check.yml")

    revision_gate = workflow.index('expected_revision="${GITHUB_SHA,,}"')
    comparison = workflow.index('if [ "$revision" = "$expected_revision" ]')
    instagram = workflow.index("Verify Instagram connection and completed launch grid")
    assert revision_gate < comparison < instagram
    assert "Production API did not serve GITHUB_SHA in time" in workflow
    assert "social checks are blocked" in workflow


def test_release_contract_is_documented_without_runtime_secret_override():
    docs = _read("VPS_DEPLOYMENT.md")
    runtime_example = _read("deploy/env.example")

    assert "Every OVH application image is bound to one clean, full Git commit" in docs
    assert "/run/lecturesift/release.env" in docs
    assert "/health.revision" in docs
    assert "bash /opt/lecturesift/deploy/release.sh build" in docs
    assert "LECTURESIFT_BUILD_REVISION=" not in runtime_example
    assert "LECTURESIFT_EXPECTED_BUILD_REVISION=" not in runtime_example
