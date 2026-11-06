from __future__ import annotations

import os
import subprocess

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
    assert "git -c core.attributesFile=/dev/null" in release
    assert "-c core.autocrlf=false" in release
    assert "-c core.eol=lf" in release
    assert "-c tar.umask=0002" in release
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
    reload = "ExecReload=/bin/bash /opt/lecturesift/deploy/core_reload_step.sh "
    reload_preflight = reload + "/bin/bash /opt/lecturesift/deploy/preflight.sh"
    reload_build = reload + "/bin/bash /opt/lecturesift/deploy/release.sh build"
    reload_up = reload + "/usr/bin/docker compose up"
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

    checkout = workflow.index("uses: actions/checkout@v4")
    publishing_gate = workflow.index(
        "Validate source-controlled Instagram publishing state"
    )
    revision_gate = workflow.index('expected_revision="${GITHUB_SHA,,}"')
    comparison = workflow.index('if [ "$revision" = "$expected_revision" ]')
    instagram = workflow.index("Verify Instagram connection and public media")
    assert checkout < publishing_gate < revision_gate < comparison < instagram
    assert "matching_services" in workflow
    assert 'names == ["lecturesift-instagram-daily"]' in workflow
    assert "len(matching_services) != 1" in workflow
    assert "len(key_lines) != 1" in workflow
    assert "if literal not in values" in workflow
    assert "INSTAGRAM_DAILY_AUTOMATION_ENABLED must be literal true or false" in workflow
    social_condition = (
        "if: ${{ steps.instagram_publishing.outputs.enabled == 'true' }}"
    )
    assert workflow.count(social_condition) == 1
    assert workflow.index(social_condition) > comparison
    assert "github.event_name == 'schedule' && steps.instagram_publishing.outputs.enabled == 'true'" in workflow
    assert "Production API did not serve GITHUB_SHA in time" in workflow
    assert "social checks are blocked" in workflow


def test_github_social_checks_skip_pushes_and_accept_the_live_revision():
    workflow = _read(".github/workflows/production-social-check.yml")
    triggers = workflow.split("permissions:", 1)[0]

    assert "push:" not in triggers
    assert "schedule:" in triggers
    assert "workflow_dispatch:" in triggers
    assert "ref: ${{ inputs.revision || github.sha }}" in workflow
    assert 'expected_revision="${REQUESTED_REVISION,,}"' in workflow


def test_scheduled_social_check_accepts_an_earlier_main_commit_in_production(tmp_path):
    # Workflow-only merges never get an OVH release, so the nightly run must not
    # wait for main's head; it accepts the live revision when it is an ancestor
    # of that head, and only then. A dispatched or requested revision stays exact.
    workflow = _read(".github/workflows/production-social-check.yml")
    wait_step = workflow.split("Wait for the production deployment and API health", 1)[1]
    wait_step = wait_step.split("Verify Instagram connection and public media", 1)[0]

    assert "fetch-depth: 0" in workflow.split("Validate source-controlled", 1)[0]
    assert '[ "${GITHUB_EVENT_NAME:-}" = "schedule" ] && [ -z "${REQUESTED_REVISION:-}" ]' in wait_step
    condition = next(
        line.strip() for line in wait_step.splitlines() if "git merge-base --is-ancestor" in line
    )
    assert condition == '&& git merge-base --is-ancestor "$revision" "$expected_revision"; then'
    guard = wait_step.splitlines()[
        [i for i, line in enumerate(wait_step.splitlines()) if "git merge-base --is-ancestor" in line][0] - 1
    ].strip()
    assert guard == 'if [ "$accept_earlier_main" = true ] && [[ "$revision" =~ ^[0-9a-f]{40}$ ]] \\'

    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        "HOME": str(tmp_path), "PATH": os.environ["PATH"],
    }

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "released")
    released = git("rev-parse", "HEAD")
    git("commit", "-q", "--allow-empty", "-m", "workflow only")
    head = git("rev-parse", "HEAD")
    git("checkout", "-q", "-b", "stray", released)
    git("commit", "-q", "--allow-empty", "-m", "never merged")
    stray = git("rev-parse", "HEAD")
    git("checkout", "-q", "main")

    script = (
        "accept_earlier_main=$ACCEPT\n"
        + guard.rstrip("\\").rstrip() + " "
        + condition.replace("; then", "") + "; then echo accepted; else echo rejected; fi\n"
    )
    cases = [
        ("true", released, head, "accepted"),
        ("true", head, released, "rejected"),  # production ahead of main: not a release
        ("true", stray, head, "rejected"),
        ("true", "unknown", head, "rejected"),
        ("false", released, head, "rejected"),  # requested revision: exact match only
    ]
    for accept, revision, expected, outcome in cases:
        result = subprocess.run(
            ["bash", "-c", script], cwd=repo, check=True, capture_output=True, text=True,
            env={**env, "ACCEPT": accept, "revision": revision, "expected_revision": expected},
        )
        assert result.stdout.strip() == outcome, (accept, revision, expected, result.stdout)


def test_release_contract_is_documented_without_runtime_secret_override():
    docs = _read("VPS_DEPLOYMENT.md")
    runtime_example = _read("deploy/env.example")

    assert "Every OVH application image is bound to one clean, full Git commit" in docs
    assert "/run/lecturesift/release.env" in docs
    assert "/health.revision" in docs
    assert "bash /opt/lecturesift/deploy/release.sh build" in docs
    assert "LECTURESIFT_BUILD_REVISION=" not in runtime_example
    assert "LECTURESIFT_EXPECTED_BUILD_REVISION=" not in runtime_example


def test_scheduled_social_check_verifies_the_istanbul_post_day_not_the_utc_date():
    workflow = _read(".github/workflows/production-social-check.yml")
    step = workflow.split("Verify today's scheduled post", 1)[1]

    assert "instagram/daily/status?day=$post_day" in step
    assert "date -u +%F" not in step
    lines = [line.strip() for line in step.splitlines()]
    post_day_line = next(line for line in lines if line.startswith("post_day="))

    # GitHub starts scheduled runs hours late; a run after midnight UTC must
    # still check the day whose 13:00 Istanbul post is already due (by 13:30).
    cases = {
        "2026-11-03T23:40:00Z": "2026-11-03",  # 02:40 TR next day
        "2026-11-03T01:10:57Z": "2026-11-02",  # 04:10 TR
        "2026-11-03T18:30:00Z": "2026-11-03",  # 21:30 TR, on schedule
        "2026-11-03T10:20:00Z": "2026-11-02",  # 13:20 TR, post may still land
        "2026-11-03T10:40:00Z": "2026-11-03",  # 13:40 TR
    }
    for now, expected in cases.items():
        epoch = subprocess.run(
            ["date", "-d", now, "+%s"], check=True, capture_output=True, text=True
        ).stdout.strip()
        result = subprocess.run(
            ["bash", "-c", f"set -euo pipefail\nnow={epoch}\n{post_day_line}\necho \"$post_day\""],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        assert result == expected, (now, result)
