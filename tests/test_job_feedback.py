"""Result feedback: one thumb per finished job, a plain-text comment, admin export.

Synthetic data only: no request leaves the test process.
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.pool import StaticPool

import lecturesift.app as app_module
from lecturesift import billing_service as billing
from lecturesift import config, job_feedback, jobs, rollout_routes, security
from lecturesift import rollout_service as rollout
from lecturesift import workspace_state as workspace
from main import app

PASSWORD = "Strong-test-password1"
ADMIN = {"Authorization": "Bearer synthetic-admin"}
client = TestClient(app)


def fresh_engine():
    return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


@pytest.fixture
def state(monkeypatch, tmp_path):
    engine = fresh_engine()
    billing.METADATA.create_all(engine)
    workspace.METADATA.create_all(engine)
    # Installed in production by deploy/feedback_schema_release.py (schema v6).
    job_feedback.METADATA.create_all(engine)
    monkeypatch.setattr(billing, "ENGINE", engine)
    monkeypatch.setattr(rollout, "ENGINE", engine)
    monkeypatch.setattr(billing, "_INITIALIZED", True)
    monkeypatch.setattr(config, "REQUIRE_DURABLE_PROCESSING", False)
    monkeypatch.setattr(config, "ADMIN_ADMIN", "synthetic-admin")
    # Fresh process-local limiters with a frozen clock keep limits deterministic
    # and independent of earlier test modules.
    for module in (rollout_routes, app_module):
        limiter = security.RateLimiter()
        limiter._redis = None
        monkeypatch.setattr(module, "RATE_LIMITER", limiter)
    monkeypatch.setattr(security, "time", SimpleNamespace(time=lambda: 1_800_000_000.0))
    monkeypatch.setattr(jobs, "WORK_DIR", tmp_path)
    monkeypatch.setattr(jobs, "REDIS_URL", "")
    store = jobs.JobStore()
    monkeypatch.setattr(jobs, "JOBS", store)
    monkeypatch.setattr(app_module, "JOBS", store)
    yield store
    engine.dispose()


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def new_guest() -> tuple[str, str]:
    response = client.post("/billing/guest-session", json={"device_id": f"device-{uuid.uuid4()}"})
    assert response.status_code == 200, response.text
    body = response.json()
    return body["token"], body["account"]["user"]["id"]


def verified_account() -> tuple[str, str]:
    created = billing.register_user(f"account-{uuid.uuid4().hex}@example.com", PASSWORD)
    verified = billing.verify_email(created["verification_token"])
    return verified["token"], verified["user"]["id"]


def finished_job(store, owner: str, **extra) -> str:
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    values = {"status": "done", "worker_state": "done", "title": "Synthetic lesson", "retention_seconds": 86400}
    store.create(job_id, store._state_path.parent / job_id, {"billing_user_id": owner, "job_type": "study_pack"}, **{**values, **extra})
    return job_id


def send(job_id: str, token: str, **payload):
    return client.post(f"/jobs/{job_id}/feedback", json={"rating": "up", **payload}, headers=auth(token))


def rows() -> list:
    with billing.ENGINE.connect() as connection:
        return connection.execute(select(job_feedback.JOB_FEEDBACK)).all()


def test_feedback_needs_the_signed_in_owner_of_a_finished_job(state):
    store = state
    token, user_id = verified_account()
    other_token, _other = verified_account()
    job = finished_job(store, user_id)
    assert client.post(f"/jobs/{job}/feedback", json={"rating": "up"}).status_code == 401
    foreign = send(job, other_token)
    assert (foreign.status_code, foreign.json()["detail"]["code"]) == (404, "LS-JOB-01")
    pending = finished_job(store, user_id, status="processing", worker_state="working")
    blocked = send(pending, token)
    assert (blocked.status_code, blocked.json()["detail"]["code"]) == (409, "LS-JOB-02")
    assert rows() == []


def test_feedback_rejects_bad_ratings_long_comments_and_unknown_locales(state):
    store = state
    token, user_id = verified_account()
    job = finished_job(store, user_id)
    assert send(job, token, rating="meh").status_code == 422
    assert send(job, token, consent="yes").status_code == 422
    assert send(job, token, comment="x" * 3000).status_code == 422
    too_long = send(job, token, comment="y" * 281)
    assert (too_long.status_code, too_long.json()["detail"]["code"]) == (400, "LS-FEEDBACK-01")
    assert "yyyy" not in too_long.text  # the rule is explained; the comment is never echoed
    unknown = send(job, token, locale="xx")
    assert (unknown.status_code, unknown.json()["detail"]["code"]) == (400, "LS-FEEDBACK-01")
    assert rows() == []
    exact = send(job, token, comment="z" * 280, locale="pt-BR")
    assert exact.status_code == 200, exact.text
    assert len(exact.json()["feedback"]["comment"]) == 280
    assert exact.json()["feedback"]["locale"] == "pt"


def test_comment_is_stored_as_one_plain_line_and_never_logged(state, caplog):
    store = state
    token, user_id = verified_account()
    job = finished_job(store, user_id)
    marker = f"gizli-{uuid.uuid4().hex}"
    raw = f"  <b>Harika</b>\r\n\n oldu​\t{marker}  \x07 "
    with caplog.at_level("DEBUG"):
        response = send(job, token, comment=raw, locale="en-US", consent=True)
    assert response.status_code == 200, response.text
    feedback = response.json()["feedback"]
    # HTML stays inert text (the panel escapes it); whitespace and control
    # characters collapse to single spaces.
    assert feedback["comment"] == f"<b>Harika</b> oldu {marker}"
    assert feedback["locale"] == "en" and feedback["consent"] is True
    assert marker not in caplog.text
    (row,) = rows()
    assert (row.comment, row.locale, bool(row.consent)) == (feedback["comment"], "en", True)


def test_guest_feedback_keeps_no_account_while_member_feedback_keeps_its_owner(state):
    store = state
    guest_token, guest_id = new_guest()
    guest_job = finished_job(store, guest_id)
    member_token, member_id = verified_account()
    member_job = finished_job(store, member_id)
    assert send(guest_job, guest_token, rating="down", comment="Çok uzun sürdü").status_code == 200
    assert send(member_job, member_token, rating="up").status_code == 200

    stored = {row.job_id: row for row in rows()}
    assert stored[guest_job].user_id is None
    assert stored[member_job].user_id == member_id

    listing = client.get("/billing/admin/feedback?limit=250", headers=ADMIN)
    assert listing.status_code == 200, listing.text
    by_job = {item["job_id"]: item for item in listing.json()["feedback"]}
    guest = by_job[guest_job]
    assert (guest["plan"], guest["user_id"], guest["email"], guest["initials"]) == ("guest", None, None, "")
    assert (guest["rating"], guest["comment"], guest["consent"]) == ("down", "Çok uzun sürdü", False)
    member = by_job[member_job]
    assert (member["plan"], member["user_id"]) == ("free", member_id)
    assert member["email"].endswith("@example.com") and member["initials"] == "A"


def test_feedback_is_one_row_per_job_and_a_repeat_replaces_it(state):
    store = state
    token, user_id = verified_account()
    job = finished_job(store, user_id)
    first = send(job, token, rating="up")
    assert first.status_code == 200 and first.json()["feedback"]["updated"] is False
    second = send(job, token, rating="down", comment="Quiz çok kolaydı", consent=True)
    assert second.status_code == 200 and second.json()["feedback"]["updated"] is True
    (row,) = rows()
    assert (row.rating, row.comment, bool(row.consent)) == ("down", "Quiz çok kolaydı", True)
    assert row.updated_at >= row.created_at
    # A later submission is the whole truth again: an empty comment clears the old one.
    assert send(job, token, rating="up", comment="").status_code == 200
    (row,) = rows()
    assert (row.rating, row.comment, bool(row.consent)) == ("up", None, False)


def test_feedback_is_rate_limited_per_account(state):
    store = state
    token, user_id = verified_account()
    job = finished_job(store, user_id)
    for _ in range(20):
        assert send(job, token).status_code == 200
    limited = send(job, token)
    assert (limited.status_code, limited.json()["detail"]["code"]) == (429, "LS-SEC-01")
    assert limited.headers.get("Retry-After")
    # The limit is per account, not per route: another member is unaffected.
    other_token, other_id = verified_account()
    assert send(finished_job(store, other_id), other_token).status_code == 200


def test_admin_listing_is_newest_first_and_csv_export_is_spreadsheet_safe(state, monkeypatch):
    store = state
    ticks = (datetime(2026, 10, 20, 9, tzinfo=timezone.utc) + timedelta(hours=index) for index in range(16))
    monkeypatch.setattr(job_feedback, "_now", lambda: next(ticks))
    token, user_id = verified_account()
    older, newer = finished_job(store, user_id), finished_job(store, user_id)
    assert send(older, token, rating="up", comment='=HYPERLINK("x")', consent=True).status_code == 200
    assert send(newer, token, rating="down", comment="Slaytlar eksikti").status_code == 200

    assert client.get("/billing/admin/feedback").status_code == 401
    assert client.get("/billing/admin/feedback", headers=auth(token)).status_code == 401
    assert client.get("/billing/admin/feedback?limit=0", headers=ADMIN).status_code == 422
    listing = client.get("/billing/admin/feedback?limit=250", headers=ADMIN)
    assert listing.status_code == 200, listing.text
    items = listing.json()["feedback"]
    assert [item["job_id"] for item in items] == [newer, older]
    assert items[0]["created_at"] == "2026-10-20T10:00:00+00:00"
    assert items[1]["consent"] is True and items[1]["comment"] == '=HYPERLINK("x")'
    assert client.get("/billing/admin/feedback?limit=1", headers=ADMIN).json()["feedback"][0]["job_id"] == newer

    assert client.get("/billing/admin/feedback.csv").status_code == 401
    export = client.get("/billing/admin/feedback.csv", headers=ADMIN)
    assert export.status_code == 200, export.text
    assert export.headers["content-type"].startswith("text/csv")
    assert export.headers["content-disposition"] == 'attachment; filename="lecturesift-geri-bildirimler.csv"'
    assert export.headers["cache-control"] == "no-store"
    text = export.content.decode("utf-8")
    header, first, second = text.splitlines()
    assert header == "﻿created_at,updated_at,rating,comment,consent,plan,locale,job_id,user_id,email"
    assert first.startswith("2026-10-20T10:00:00+00:00,") and ",down,Slaytlar eksikti,false,free,tr," in first
    # A leading "=" would run as a formula in a spreadsheet; it is quoted out.
    assert '"\'=HYPERLINK(""x"")",true,free,tr,' in second
    assert user_id in second


def test_export_includes_feedback_and_closing_the_account_erases_it(state):
    store = state
    email = f"export-{uuid.uuid4().hex}@example.com"
    created = billing.register_user(email, PASSWORD)
    verified = billing.verify_email(created["verification_token"])
    token, user_id = verified["token"], verified["user"]["id"]
    job = finished_job(store, user_id)
    assert send(job, token, rating="up", comment="Özet tam yerinde", consent=True, locale="tr").status_code == 200
    guest_token, guest_id = new_guest()
    guest_job = finished_job(store, guest_id)
    assert send(guest_job, guest_token, rating="down").status_code == 200

    exported = client.get("/billing/me/export", headers=auth(token))
    assert exported.status_code == 200, exported.text
    feedback = exported.json()["export"]["feedback"]
    assert [(item["job_id"], item["rating"], item["comment"], item["consent"], item["locale"]) for item in feedback] == [
        (job, "up", "Özet tam yerinde", True, "tr")
    ]
    assert feedback[0]["created_at"].endswith("+00:00")

    closed = client.post(
        "/billing/me/close-account",
        json={"current_password": PASSWORD, "email_confirmation": email},
        headers=auth(token),
    )
    assert closed.status_code == 200, closed.text
    # Only the closed member's rows go; the guest's anonymous row is unrelated.
    assert [row.job_id for row in rows()] == [guest_job]

    # The administrator closure path erases feedback the same way.
    other_email = f"admin-close-{uuid.uuid4().hex}@example.com"
    other = billing.verify_email(billing.register_user(other_email, PASSWORD)["verification_token"])
    other_job = finished_job(store, other["user"]["id"])
    assert send(other_job, other["token"], rating="down").status_code == 200
    rollout.admin_close_user_account(
        other["user"]["id"], confirmation_email=other_email, reason="Synthetic closure", actor="synthetic-admin"
    )
    assert [row.job_id for row in rows()] == [guest_job]


def test_runtime_never_creates_the_table_and_fails_closed_before_the_schema_release(state, monkeypatch):
    store = state
    # The table belongs to the explicit v6 release (deploy/feedback_schema_release.py):
    # rollout initialisation must leave a database without it untouched.
    job_feedback.JOB_FEEDBACK.drop(billing.ENGINE)
    monkeypatch.setattr(billing, "_INITIALIZED", False)
    rollout.init_rollout_database()
    assert "lecturesift_job_feedback" not in inspect(billing.ENGINE).get_table_names()
    assert "lecturesift_job_feedback" not in {table.name for table in billing.METADATA.sorted_tables}

    email = f"pre-release-{uuid.uuid4().hex}@example.com"
    verified = billing.verify_email(billing.register_user(email, PASSWORD)["verification_token"])
    token, user_id = verified["token"], verified["user"]["id"]
    job = finished_job(store, user_id)
    blocked = send(job, token)
    assert (blocked.status_code, blocked.json()["detail"]["code"]) == (503, "LS-FEEDBACK-02")
    listing = client.get("/billing/admin/feedback?limit=250", headers=ADMIN)
    assert (listing.status_code, listing.json()["detail"]["code"]) == (503, "LS-FEEDBACK-03")
    assert client.get("/billing/admin/feedback.csv", headers=ADMIN).status_code == 503
    # Export and closure keep working while the release is still pending.
    exported = client.get("/billing/me/export", headers=auth(token))
    assert exported.status_code == 200 and exported.json()["export"]["feedback"] == []
    closed = client.post(
        "/billing/me/close-account",
        json={"current_password": PASSWORD, "email_confirmation": email},
        headers=auth(token),
    )
    assert closed.status_code == 200, closed.text
