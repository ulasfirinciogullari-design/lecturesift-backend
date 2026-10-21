"""What the API proves about its process before it accepts a request.

Upload parts are spooled to a private directory on the API work volume, not
to the small /tmp tmpfs, and production never starts without a real session
secret. Tests and local development keep working without either.
"""

import asyncio
import os
from pathlib import Path
import re
import stat
import tempfile
import time

import pytest
import yaml

from lecturesift import api_temp, assistant_maintenance as maintenance, billing, billing_service, config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def work(tmp_path, monkeypatch):
    root = tmp_path / "work"
    root.mkdir()
    monkeypatch.setattr(config, "WORK_DIR", root)
    # Restore tempfile's cached directory after each test.
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    return root


def test_compose_spools_api_uploads_to_the_work_volume():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    api = compose["services"]["api"]
    spool = Path(api["environment"]["TMPDIR"])
    assert "api_work:/var/lib/lecturesift" in api["volumes"]
    assert "ENV LECTURESIFT_WORK_DIR=/var/lib/lecturesift" in (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert Path("/var/lib/lecturesift") in spool.parents
    assert spool != Path("/var/lib/lecturesift/ingress-state")
    # /tmp itself stays a small memory-backed tmpfs.
    assert any(entry.startswith("/tmp:") and "size=128m" in entry for entry in api["tmpfs"])


def test_ingress_body_limit_admits_the_largest_plan_upload_with_multipart_framing():
    caddyfile = (ROOT / "Caddyfile").read_text(encoding="utf-8")
    limit = re.search(r"request_body \{[^}]*max_size (\d+)MB", caddyfile)
    assert limit, "API ingress request body limit is missing"
    ingress_bytes = int(limit.group(1)) * 1000 * 1000  # Caddy "MB" is decimal.
    largest_plan_bytes = max(
        min(config.MAX_VIDEO_BYTES, plan.max_media_upload_mb * 1024 * 1024)
        for plan in (*billing.PLANS, *billing.LEGACY_PLANS)
    )
    assert largest_plan_bytes == 1024 * 1024 * 1024
    assert ingress_bytes >= largest_plan_bytes + 16 * 1024 * 1024


def test_prepare_creates_a_private_spool_directory_and_points_tempfile_at_it(work, monkeypatch):
    spool = work / "tmp"
    monkeypatch.setenv("TMPDIR", str(spool))
    assert api_temp.prepare_api_temp_dir() == spool
    assert stat.S_IMODE(spool.stat().st_mode) == 0o700
    assert tempfile.gettempdir() == str(spool)
    with tempfile.NamedTemporaryFile() as handle:
        assert Path(handle.name).parent == spool

    # A directory left with wider permissions is tightened again.
    os.chmod(spool, 0o755)
    api_temp.prepare_api_temp_dir()
    assert stat.S_IMODE(spool.stat().st_mode) == 0o700


def test_prepare_refuses_a_spool_path_that_is_not_a_private_directory(work, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (work / "tmp").symlink_to(elsewhere, target_is_directory=True)
    monkeypatch.setenv("TMPDIR", str(work / "tmp"))
    with pytest.raises(RuntimeError, match="private directory"):
        api_temp.prepare_api_temp_dir()


@pytest.mark.parametrize("value", ["", "relative/tmp", "/somewhere/else", "/"])
def test_a_developers_own_tmpdir_is_left_alone(work, monkeypatch, value):
    monkeypatch.setenv("TMPDIR", value)
    before = tempfile.tempdir
    assert api_temp.prepare_api_temp_dir() is None
    assert tempfile.tempdir == before
    assert api_temp.clean_stale_temp_entries() == 0


def test_stale_spool_entries_are_removed_and_fresh_ones_kept(work, monkeypatch):
    spool = work / "tmp"
    spool.mkdir()
    monkeypatch.setenv("TMPDIR", str(spool))
    stale_file = spool / "tmpabc.mp4"
    stale_dir = spool / "tmpdir123"
    fresh = spool / "tmpfresh.jpg"
    stale_file.write_bytes(b"old")
    stale_dir.mkdir()
    (stale_dir / "frame.png").write_bytes(b"old")
    fresh.write_bytes(b"new")
    old = time.time() - api_temp.STALE_TEMP_SECONDS - 60
    for path in (stale_file, stale_dir):
        os.utime(path, (old, old))

    assert api_temp.clean_stale_temp_entries() == 2
    assert sorted(path.name for path in spool.iterdir()) == ["tmpfresh.jpg"]
    # The same pruning runs at startup.
    os.utime(fresh, (old, old))
    api_temp.prepare_api_temp_dir()
    assert list(spool.iterdir()) == []


def _quiet_loops(monkeypatch):
    async def idle():
        await asyncio.Future()

    for name in ("_loop", "_job_retention_loop", "_temp_cleanup_loop"):
        monkeypatch.setattr(maintenance, name, idle)
    import lecturesift.job_notifications as notifications
    import lecturesift.renewal_reminders as reminders

    monkeypatch.setattr(notifications, "delivery_loop", idle)
    monkeypatch.setattr(reminders, "reminder_loop", idle)


def test_api_startup_prepares_the_spool_directory(work, monkeypatch):
    _quiet_loops(monkeypatch)
    monkeypatch.setenv("TMPDIR", str(work / "tmp"))

    async def scenario():
        async with maintenance.lifespan(None):
            assert tempfile.gettempdir() == str(work / "tmp")

    asyncio.run(scenario())


@pytest.mark.parametrize("secret", ["", "   ", "short-secret", "x" * 31])
def test_production_api_refuses_to_start_without_a_real_session_secret(monkeypatch, secret):
    _quiet_loops(monkeypatch)
    monkeypatch.setattr(config, "DEPLOYMENT_PROVIDER", "ovh")
    monkeypatch.setattr(config, "BILLING_SESSION_SECRET", secret)

    async def scenario():
        async with maintenance.lifespan(None):
            pytest.fail("the API started without a session secret")

    with pytest.raises(RuntimeError, match="BILLING_SESSION_SECRET"):
        asyncio.run(scenario())


def test_production_api_starts_with_a_real_session_secret(monkeypatch):
    _quiet_loops(monkeypatch)
    monkeypatch.setattr(config, "DEPLOYMENT_PROVIDER", "ovh")
    monkeypatch.setattr(config, "BILLING_SESSION_SECRET", "s" * billing_service.PRODUCTION_SESSION_SECRET_MIN_LENGTH)

    async def scenario():
        async with maintenance.lifespan(None):
            pass

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["unknown", "render"])
def test_tests_and_local_development_start_without_a_session_secret(monkeypatch, provider):
    _quiet_loops(monkeypatch)
    monkeypatch.setattr(config, "DEPLOYMENT_PROVIDER", provider)
    monkeypatch.setattr(config, "BILLING_SESSION_SECRET", "")

    async def scenario():
        async with maintenance.lifespan(None):
            pass

    asyncio.run(scenario())
