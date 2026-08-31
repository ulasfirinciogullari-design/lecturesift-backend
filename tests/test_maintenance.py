import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lecturesift import config
import lecturesift.app as app_module
import lecturesift.durable_runtime as durable_runtime
from main import app


@pytest.fixture(autouse=True)
def normal_service(monkeypatch):
    monkeypatch.setattr(config, "MAINTENANCE_MODE", "off")
    monkeypatch.setattr(config, "MAINTENANCE_STATE_FILE", Path("missing-runtime-maintenance"))
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _assert_maintenance(response, mode: str) -> None:
    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "LS-MAINT-01",
            "message": "LectureSift kısa süreli bakımda. Lütfen biraz sonra tekrar dene.",
            "mode": mode,
        }
    }
    assert response.headers["retry-after"] == "60"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


def test_maintenance_mode_normalization_fails_closed():
    assert config._maintenance_mode(" OFF ") == "off"
    assert config._maintenance_mode("Drain") == "drain"
    assert config._maintenance_mode("freeze") == "freeze"
    assert config._maintenance_mode("typo") == "freeze"
    assert config._maintenance_mode("") == "freeze"


def test_runtime_maintenance_file_is_same_boot_bounded_and_fail_closed(monkeypatch, tmp_path):
    marker = tmp_path / "maintenance.json"
    boot_id_file = tmp_path / "boot-id"
    boot_id = "11111111-2222-3333-4444-555555555555"
    boot_id_file.write_text(boot_id, encoding="ascii")
    monkeypatch.setattr(config, "MAINTENANCE_STATE_FILE", marker)
    monkeypatch.setattr(config, "_BOOT_ID_FILE", boot_id_file)
    monkeypatch.setattr(config.time, "time", lambda: 1_000_000)

    marker.write_text(
        json.dumps(
            {
                "version": 1,
                "mode": "drain",
                "expires_at": 1_000_600,
                "boot_id": boot_id,
            }
        ),
        encoding="utf-8",
    )
    assert config.current_maintenance_mode() == "drain"

    marker.write_text(marker.read_text().replace(boot_id, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"))
    assert config.current_maintenance_mode() == "off"
    marker.write_text("not-json", encoding="utf-8")
    assert config.current_maintenance_mode() == "freeze"


def test_off_mode_does_not_replace_normal_route_errors():
    response = TestClient(app).post("/billing/register", json={})
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "missing"


def test_drain_blocks_writes_with_cors_and_no_store(monkeypatch):
    monkeypatch.setattr(config, "MAINTENANCE_MODE", "drain")
    response = TestClient(app).post(
        "/billing/register",
        json={},
        headers={"Origin": "https://lecturesift.com"},
    )

    _assert_maintenance(response, "drain")
    assert response.headers["access-control-allow-origin"] == "https://lecturesift.com"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize(
    "path",
    [
        "/jobs",
        "/jobs/a-job/result",
        "/billing/me/export",
        "/billing/me/export/archive",
        "/billing/admin/jobs",
        "/billing/me/rollout",
        "/billing/rewarded-ads",
        "/billing/rewarded-ads/history",
    ],
)
def test_drain_blocks_hazardous_reads(monkeypatch, path: str):
    monkeypatch.setattr(config, "MAINTENANCE_MODE", "drain")
    _assert_maintenance(TestClient(app).get(path), "drain")


def test_drain_keeps_non_hazardous_reads_and_uses_path_boundaries(monkeypatch):
    monkeypatch.setattr(config, "MAINTENANCE_MODE", "drain")
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/billing/plans").status_code == 200
    assert client.get("/jobsmith").status_code == 404
    assert client.get("/billing/me/exported").status_code != 503


def test_drain_allows_only_exact_payment_callback_paths(monkeypatch):
    monkeypatch.setattr(config, "MAINTENANCE_MODE", "drain")
    monkeypatch.setattr(
        app_module,
        "process_iyzico_callback",
        lambda **_kwargs: {"status": "paid"},
    )
    monkeypatch.setattr(app_module, "process_iyzico_webhook", lambda **_kwargs: None)
    monkeypatch.setattr(app_module, "process_paytr_callback", lambda **_kwargs: None)
    client = TestClient(app)

    iyzico_callback = client.post(
        "/billing/iyzico/callback?order=ORDER123",
        data={"token": "provider-token"},
        follow_redirects=False,
    )
    iyzico_webhook = client.post(
        "/billing/iyzico/webhook",
        json={"eventType": "CHECKOUT_FORM_AUTH"},
        headers={"X-IYZ-SIGNATURE-V3": "signed"},
    )
    paytr_callback = client.post(
        "/billing/paytr/callback",
        data={
            "merchant_oid": "ORDER123",
            "status": "success",
            "total_amount": "100",
            "payment_amount": "100",
            "hash": "signed",
        },
    )

    assert iyzico_callback.status_code == 303
    assert iyzico_webhook.status_code == 200
    assert paytr_callback.status_code == 200
    _assert_maintenance(client.post("/billing/iyzico/webhook/", json={}), "drain")
    _assert_maintenance(client.patch("/billing/iyzico/webhook"), "drain")


def test_freeze_exposes_only_exact_health_reads_and_options(monkeypatch):
    monkeypatch.setattr(config, "MAINTENANCE_MODE", "freeze")
    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/billing/health").status_code == 200
    assert client.head("/health").status_code != 503
    _assert_maintenance(client.get("/billing/plans"), "freeze")
    _assert_maintenance(client.post("/health"), "freeze")
    _assert_maintenance(client.post("/billing/iyzico/webhook", json={}), "freeze")

    preflight = client.options(
        "/jobs",
        headers={
            "Origin": "https://lecturesift.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://lecturesift.com"


def test_health_endpoints_report_current_maintenance_mode(monkeypatch):
    monkeypatch.setattr(config, "MAINTENANCE_MODE", "drain")
    client = TestClient(app)

    assert client.get("/health").json()["maintenance_mode"] == "drain"
    assert client.get("/billing/health").json()["maintenance_mode"] == "drain"


def test_durable_startup_recovery_is_suppressed_while_fenced(monkeypatch):
    started = []

    class DeferredThread:
        def __init__(self, *, target, daemon, name):
            started.append({"target": target, "daemon": daemon, "name": name})

        def start(self):
            started[-1]["started"] = True

    monkeypatch.setattr(durable_runtime.threading, "Thread", DeferredThread)
    for mode in ("drain", "freeze"):
        monkeypatch.setattr(config, "MAINTENANCE_MODE", mode)
        durable_runtime._start_durable_recovery(lambda: None)
    assert started == []

    monkeypatch.setattr(config, "MAINTENANCE_MODE", "off")
    durable_runtime._start_durable_recovery(lambda: None)
    assert len(started) == 1
    assert started[0]["daemon"] is True
    assert started[0]["name"] == "lecturesift-recovery"
    assert started[0]["started"] is True


def test_celery_done_state_is_hidden_until_durable_publication(monkeypatch, tmp_path: Path):
    data = {
        "job_id": "publishing-job",
        "status": "done",
        "percent": 100,
        "stage": "done",
        "queue_mode": "celery",
        "worker_state": "processing",
        "options": {"billing_user_id": "user-one", "download_entitled": True},
        "remote_prefix": "",
        "remote_result_key": "",
        "remote_download_key": "",
    }
    materialized = []
    monkeypatch.setattr(app_module.JOBS, "get", lambda job_id: data.copy() if job_id == data["job_id"] else None)
    monkeypatch.setattr(
        app_module.JOBS,
        "ensure_local_file",
        lambda *_args, **_kwargs: materialized.append(True),
    )
    app.dependency_overrides[app_module._billing_user] = lambda: {"id": "user-one"}
    client = TestClient(app)

    status = client.get("/jobs/publishing-job")
    result = client.get("/jobs/publishing-job/result")
    slide = client.get("/jobs/publishing-job/slide/slide_001.jpg")
    artifact = client.get("/jobs/publishing-job/artifact/notes.pdf")
    download = client.get("/jobs/publishing-job/download")
    question = client.post("/jobs/publishing-job/ask", json={"question": "What?"})

    assert status.status_code == 200
    assert status.json()["status"] == "working"
    assert status.json()["percent"] == 99
    assert status.json()["stage"] == "worker_publish"
    assert all(
        response.status_code == 409
        for response in (result, slide, artifact, download, question)
    )
    assert materialized == []
    assert app_module._job_is_publicly_complete(data) is False

    published = {**data, "worker_state": "done"}
    assert app_module._job_is_publicly_complete(published) is True
