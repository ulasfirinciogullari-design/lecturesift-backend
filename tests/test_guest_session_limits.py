"""New guest trials are bounded per client IP and per UTC day; resuming is not.

Uses an isolated SQLite engine, a fixed clock and a fresh process-local rate
limiter, so no shared trial rows or limiter buckets are involved.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from lecturesift import billing_service as billing, config, rollout_routes, rollout_service as rollout, security
from main import app

LIMIT_MESSAGE = (
    "Bugünlük hesapsız deneme hakkı doldu. "
    "Ücretsiz hesap açarak her ay 120 dakika kullanabilirsin."
)
# Three hours before the next UTC day, so Retry-After is easy to verify.
START = datetime(2026, 10, 19, 21, 0, tzinfo=timezone.utc)
NEXT_DAY = START + timedelta(hours=3, seconds=1)


def _fresh_limiter() -> security.RateLimiter:
    limiter = security.RateLimiter()
    limiter._redis = None
    return limiter


@pytest.fixture
def state(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    billing.METADATA.create_all(engine)
    monkeypatch.setattr(billing, "ENGINE", engine)
    monkeypatch.setattr(billing, "_INITIALIZED", True)
    monkeypatch.setattr(rollout, "ENGINE", engine)
    monkeypatch.setattr(config, "BILLING_SESSION_SECRET", "synthetic-guest-limit-test-session-secret")
    monkeypatch.setattr(config, "BILLING_LEGACY_SESSION_SECRET_HEX", "")
    monkeypatch.setattr(config, "GUEST_TRIAL_IP_DAILY_LIMIT", 3)
    monkeypatch.setattr(config, "GUEST_TRIAL_GLOBAL_DAILY_LIMIT", 100)
    clock = {"now": START}
    monkeypatch.setattr(billing, "utcnow", lambda: clock["now"])
    monkeypatch.setattr(rollout, "utcnow", lambda: clock["now"])
    monkeypatch.setattr(security, "time", SimpleNamespace(time=lambda: clock["now"].timestamp()))
    monkeypatch.setattr(rollout_routes, "RATE_LIMITER", _fresh_limiter())
    yield {"engine": engine, "clock": clock, "client": TestClient(app)}
    engine.dispose()


def _open(state, device: str, ip: str):
    return state["client"].post(
        "/billing/guest-session",
        json={"device_id": device},
        headers={"X-Forwarded-For": ip, "User-Agent": "LectureSift-Guest-Limit-Test"},
    )


def _device() -> str:
    return f"guest-limit-{uuid.uuid4()}"


def _trial_count(state) -> int:
    with state["engine"].connect() as connection:
        return connection.execute(select(func.count()).select_from(rollout.GUEST_TRIALS)).scalar_one()


def _assert_limited(response) -> None:
    assert response.status_code == 429, response.text
    assert response.json()["detail"] == {"code": "LS-GUEST-06", "message": LIMIT_MESSAGE}
    assert response.headers["Retry-After"] == str(3 * 60 * 60)


def test_new_trials_are_limited_per_client_ip_without_blocking_resume_or_other_ips(state):
    devices = [_device() for _ in range(4)]
    created = [_open(state, device, "198.51.100.7") for device in devices[:3]]
    assert [response.status_code for response in created] == [200, 200, 200]
    assert all(response.json()["resumed"] is False for response in created)

    _assert_limited(_open(state, devices[3], "198.51.100.7"))
    assert _trial_count(state) == 3

    resumed = _open(state, devices[0], "198.51.100.7")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["resumed"] is True
    assert resumed.json()["account"]["user"]["id"] == created[0].json()["account"]["user"]["id"]

    other_ip = _open(state, devices[3], "203.0.113.9")
    assert other_ip.status_code == 200 and other_ip.json()["resumed"] is False

    state["clock"]["now"] = NEXT_DAY
    next_day = _open(state, _device(), "198.51.100.7")
    assert next_day.status_code == 200 and next_day.json()["resumed"] is False


def test_ipv6_clients_are_grouped_by_prefix_and_mapped_ipv4_by_address(state, monkeypatch):
    monkeypatch.setattr(config, "GUEST_TRIAL_IP_DAILY_LIMIT", 1)
    assert _open(state, _device(), "2001:db8:1:2::10").status_code == 200
    _assert_limited(_open(state, _device(), "2001:db8:1:2::99"))
    assert _open(state, _device(), "2001:db8:1:3::10").status_code == 200

    assert _open(state, _device(), "198.51.100.20").status_code == 200
    _assert_limited(_open(state, _device(), "::ffff:198.51.100.20"))
    assert _open(state, _device(), "::ffff:198.51.100.21").status_code == 200


def test_global_daily_cap_survives_a_restart_and_resets_on_the_next_utc_day(state, monkeypatch):
    monkeypatch.setattr(config, "GUEST_TRIAL_IP_DAILY_LIMIT", 1)
    monkeypatch.setattr(config, "GUEST_TRIAL_GLOBAL_DAILY_LIMIT", 2)
    first_device = _device()
    assert _open(state, first_device, "198.51.100.30").status_code == 200
    assert _open(state, _device(), "198.51.100.31").status_code == 200
    _assert_limited(_open(state, _device(), "198.51.100.32"))
    assert _trial_count(state) == 2

    # The global refusal did not use this address's only new-trial slot.
    monkeypatch.setattr(config, "GUEST_TRIAL_GLOBAL_DAILY_LIMIT", 3)
    assert _open(state, _device(), "198.51.100.32").status_code == 200

    # A restarted API loses its in-memory limiter but not the stored trials.
    monkeypatch.setattr(rollout_routes, "RATE_LIMITER", _fresh_limiter())
    _assert_limited(_open(state, _device(), "198.51.100.33"))
    resumed = _open(state, first_device, "198.51.100.34")
    assert resumed.status_code == 200 and resumed.json()["resumed"] is True
    assert _trial_count(state) == 3

    state["clock"]["now"] = NEXT_DAY
    assert _open(state, _device(), "198.51.100.33").status_code == 200
    assert _trial_count(state) == 4


def test_reopening_an_existing_trial_is_never_counted_or_blocked(state, monkeypatch):
    monkeypatch.setattr(config, "GUEST_TRIAL_IP_DAILY_LIMIT", 1)
    device = _device()
    first = _open(state, device, "198.51.100.40")
    assert first.status_code == 200 and first.json()["resumed"] is False
    user_id = first.json()["account"]["user"]["id"]
    for ip in ("198.51.100.40", "198.51.100.40", "203.0.113.41"):
        again = _open(state, device, ip)
        assert again.status_code == 200, again.text
        assert again.json()["resumed"] is True
        assert again.json()["account"]["user"]["id"] == user_id

    # Resuming from the second address did not use its single new-trial slot.
    assert _open(state, _device(), "203.0.113.41").status_code == 200
    _assert_limited(_open(state, _device(), "198.51.100.40"))

    # Pausing new trials still lets an existing device back in.
    monkeypatch.setattr(config, "GUEST_TRIAL_GLOBAL_DAILY_LIMIT", 0)
    _assert_limited(_open(state, _device(), "192.0.2.50"))
    paused = _open(state, device, "192.0.2.50")
    assert paused.status_code == 200 and paused.json()["resumed"] is True
    assert paused.json()["account"]["user"]["id"] == user_id
