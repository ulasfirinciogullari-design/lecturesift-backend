import json
import uuid

from fastapi.testclient import TestClient

from lecturesift.billing_service import register_user, verify_email
from lecturesift.jobs import JOBS
from main import app


def _account() -> tuple[str, str, str]:
    email = f"lifecycle-{uuid.uuid4()}@example.com"
    password = "Strong-test-password1"
    created = register_user(
        email,
        password,
        "Lifecycle",
        "User",
        phone="+905551112233",
        country_code="TR",
    )
    verified = verify_email(created["verification_token"])
    return email, password, verified["token"]


def test_account_export_is_portable_and_account_closure_revokes_access(tmp_path):
    email, password, token = _account()
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/billing/me", headers=headers).json()["account"]
    job_id = f"export-{uuid.uuid4()}"
    JOBS.create(
        job_id,
        tmp_path,
        {"billing_user_id": me["user"]["id"], "output_language": "tr"},
        celery_task_id="private-task",
        source_keys={"audio": ["jobs/private/source.mp4"]},
    )

    exported = client.get("/billing/me/export", headers=headers)
    assert exported.status_code == 200
    payload = exported.json()["export"]
    assert payload["account"]["user"]["email"] == email
    assert payload["jobs"][0]["job_id"] == job_id
    serialized = json.dumps(payload)
    assert "password" not in serialized.casefold()
    assert "private-task" not in serialized
    assert "jobs/private/source.mp4" not in serialized
    assert "billing_user_id" not in serialized

    wrong = client.post(
        "/billing/me/close-account",
        headers=headers,
        json={"current_password": "wrong-password", "email_confirmation": email},
    )
    assert wrong.status_code == 400

    closed = client.post(
        "/billing/me/close-account",
        headers=headers,
        json={"current_password": password, "email_confirmation": email},
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    assert closed.json()["deleted_jobs"] == 1
    assert client.get("/billing/me", headers=headers).status_code == 401
    assert client.post("/billing/login", json={"email": email, "password": password}).status_code == 401

    recreated = register_user(email, password, "New", "Account", country_code="TR")
    assert recreated["user"]["email"] == email
