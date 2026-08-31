"""Private OVH rehearsal checks; never invoked by the production service."""

from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
import uuid

import httpx
from sqlalchemy.engine import make_url

from lecturesift import config
from lecturesift.billing_service import register_user, verify_email
from lecturesift.jobs import JOBS
from lecturesift.storage import STORAGE


BASE_URL = "http://127.0.0.1:8000"


def require(response: httpx.Response, expected: int = 200) -> dict:
    if response.status_code != expected:
        raise RuntimeError(f"HTTP {response.status_code} for {response.request.url.path}")
    return response.json()


def main() -> None:
    database_name = make_url(config.DATABASE_URL).database or ""
    if os.getenv("LECTURESIFT_REHEARSAL") != "1" or not database_name.startswith(
        "lecturesift_rehearsal_"
    ):
        raise RuntimeError("Refusing to run outside an explicit rehearsal database")

    run_id = uuid.uuid4().hex
    test_email = f"ovh-rehearsal-{run_id}@example.invalid"
    registration = register_user(
        test_email,
        secrets.token_urlsafe(32),
        "OVH",
        "Rehearsal",
        country_code="TR",
    )
    account = verify_email(registration["verification_token"])
    token = account["token"]
    auth = {"Authorization": f"Bearer {token}"}
    admin_auth = {"Authorization": f"Bearer {config.ADMIN_ADMIN}"}
    old_admin_auth = {"Authorization": "Bearer 1"}

    summary: dict[str, object] = {
        "account": False,
        "admin": False,
        "old_admin_token_rejected": False,
        "iyzico_configured": False,
        "invalid_payment_webhook_rejected": False,
        "r2_roundtrip": False,
        "analysis_completed": False,
        "durable_result_published": False,
        "result_reopened": False,
        "rehearsal_account_closed": False,
    }
    job_id = ""
    cleanup_state: dict[str, int | None] = {"deleted": None}

    def cleanup_terminal_job() -> None:
        if not job_id or cleanup_state["deleted"] is not None:
            return
        job = JOBS.get(job_id) or {}
        if job.get("status") in {"done", "error"}:
            cleanup_state["deleted"] = STORAGE.delete_job(job_id)

    atexit.register(cleanup_terminal_job)

    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        me = require(client.get("/billing/me", headers=auth))
        summary["account"] = bool(
            me.get("account", {}).get("user", {}).get("email_verified")
        )

        require(client.get("/billing/admin/users?page_size=10", headers=admin_auth))
        summary["admin"] = True
        summary["old_admin_token_rejected"] = (
            client.get("/billing/admin/users?limit=1", headers=old_admin_auth).status_code == 401
        )

        billing_health = require(client.get("/billing/health"))
        summary["iyzico_configured"] = bool(
            billing_health.get("payments", {}).get("iyzico", {}).get("configured")
        )
        if not summary["iyzico_configured"]:
            raise RuntimeError("iyzico is not configured in the rehearsal runtime")
        invalid_webhook = client.post("/billing/iyzico/webhook", json={})
        summary["invalid_payment_webhook_rejected"] = invalid_webhook.status_code == 400

        probe_key = f"probes/ovh-rehearsal/{run_id}.txt"
        probe_source = Path(tempfile.gettempdir()) / f"{run_id}-source.txt"
        probe_target = Path(tempfile.gettempdir()) / f"{run_id}-target.txt"
        probe_payload = f"LectureSift OVH R2 rehearsal {run_id}".encode("utf-8")
        try:
            probe_source.write_bytes(probe_payload)
            STORAGE.upload_file(probe_source, probe_key)
            STORAGE.download_file(probe_key, probe_target)
            if probe_target.read_bytes() != probe_payload:
                raise RuntimeError("R2 roundtrip payload mismatch")
            summary["r2_roundtrip"] = True
        finally:
            STORAGE.delete_keys([probe_key])
            probe_source.unlink(missing_ok=True)
            probe_target.unlink(missing_ok=True)

        lesson = (
            "Fotosentez, bitkilerin ışık enerjisini kimyasal enerjiye dönüştürdüğü süreçtir. "
            "Kloroplast içinde ışığa bağlı tepkimeler ATP ve NADPH üretir. Calvin döngüsü "
            "karbondioksiti kullanarak karbonhidrat sentezler. Stomalar gaz alışverişini "
            "düzenler; ışık şiddeti, sıcaklık ve su miktarı süreç hızını etkiler. "
        ) * 35
        started = time.monotonic()
        response = client.post(
            "/jobs",
            headers=auth,
            files={"files": ("ovh-rehearsal.txt", lesson.encode("utf-8"), "text/plain")},
            data={
                "source_language": "tr",
                "output_language": "tr",
                "summary_style": "standard",
                "quiz_count": "0",
                "flashcard_count": "0",
                "translate_transcript": "false",
                "output_formats": "pdf",
                "job_type": "study_pack",
                "include_summary": "true",
                "include_transcript": "false",
                "include_slides": "false",
            },
        )
        created = require(response)
        job_id = str(created["job_id"])

        deadline = time.monotonic() + 420
        status = "queued"
        while time.monotonic() < deadline:
            job = require(client.get(f"/jobs/{job_id}", headers=auth))
            status = str(job.get("status") or "")
            worker_state = str(job.get("worker_state") or "")
            if status == "error" or (
                status == "done"
                and worker_state == "done"
                and job.get("queue_mode") == "celery"
            ):
                break
            time.sleep(2)
        if (
            status != "done"
            or worker_state != "done"
            or job.get("queue_mode") != "celery"
            or int(job.get("percent") or 0) != 100
            or job.get("stage") != "done"
        ):
            raise RuntimeError(f"Analysis rehearsal ended with status {status!r}")
        summary["analysis_completed"] = True
        summary["analysis_seconds"] = round(time.monotonic() - started, 2)

        persisted = JOBS.get(job_id) or {}
        summary["durable_result_published"] = bool(
            persisted.get("remote_prefix") and persisted.get("remote_result_key")
        )
        if not summary["durable_result_published"]:
            raise RuntimeError("Durable result keys were not published")

        result = require(client.get(f"/jobs/{job_id}/result", headers=auth))
        summary["result_reopened"] = (
            result.get("job_id") == job_id and bool(str(result.get("summary") or "").strip())
            and any(item.get("format") == "PDF" for item in result.get("artifacts", []))
        )

        cleanup_terminal_job()
        closed = require(
            client.request(
                "DELETE",
                f"/billing/admin/users/{account['user']['id']}",
                headers=admin_auth,
                json={
                    "confirmation_email": test_email,
                    "reason": "OVH migration rehearsal cleanup",
                },
            )
        )
        summary["rehearsal_account_closed"] = bool(closed.get("ok")) and int(
            closed.get("deleted_jobs") or 0
        ) == 1

    if job_id:
        cleanup_terminal_job()
        deleted = int(cleanup_state["deleted"] or 0)
        summary["r2_job_objects_deleted"] = deleted
        if deleted < 1:
            raise RuntimeError("Rehearsal R2 job cleanup did not delete any objects")
    if not all(
        summary[key]
        for key in (
            "account",
            "admin",
            "old_admin_token_rejected",
            "iyzico_configured",
            "invalid_payment_webhook_rejected",
            "r2_roundtrip",
            "analysis_completed",
            "durable_result_published",
            "result_reopened",
            "rehearsal_account_closed",
        )
    ):
        raise RuntimeError("One or more rehearsal gates did not pass")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
