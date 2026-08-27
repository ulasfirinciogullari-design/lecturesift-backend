from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str, *, count: int = 1) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    found = text.count(old)
    if found != count:
        raise RuntimeError(f"{path}: expected {count}, found {found}: {old[:120]!r}")
    target.write_text(text.replace(old, new), encoding="utf-8")


replace("lecturesift/commerce_routes.py", "from typing import Any\n\n", "from pathlib import Path\nfrom typing import Any\n\n")
replace("lecturesift/commerce_routes.py", "from fastapi.responses import PlainTextResponse\n", "from fastapi.responses import FileResponse, PlainTextResponse\nfrom sqlalchemy import select\n")
replace("lecturesift/commerce_routes.py", "from .billing_service import BillingAuthenticationError, BillingConfigurationError, BillingError, authenticate_session\n", "from .billing_service import ENGINE, BillingAuthenticationError, BillingConfigurationError, BillingError, authenticate_session, utcnow\n")
replace(
    "lecturesift/commerce_routes.py",
    "from .commerce import account_commerce_status, accept_payment_event, admin_refunds, cancel_account_deletion, create_purchase, mark_job_deleted, mark_purchase_failed, purchase_for_reference, refund_for_admin, request_account_deletion, request_refund, revoke_refunded_purchase, set_cancel_at_period_end, update_refund_status\n",
    "from .commerce import JOB_HISTORY, account_commerce_status, accept_payment_event, admin_refunds, cancel_account_deletion, create_purchase, mark_job_deleted, mark_purchase_failed, purchase_for_reference, refund_for_admin, request_account_deletion, request_refund, require_download_access, revoke_refunded_purchase, set_cancel_at_period_end, update_refund_status\n",
)
marker = "@router.delete(\"/billing/jobs/{job_id}\")\ndef delete_job(job_id: str, user: dict = Depends(_user)) -> dict[str, Any]:\n"
route = '''@router.get("/billing/jobs/{job_id}/download")
def download_owned_output(job_id: str, user: dict = Depends(_user)) -> FileResponse:
    try:
        require_download_access(user["id"], job_id)
    except BillingError as exc:
        raise HTTPException(402, detail={"code": "LS-BILL-22", "message": str(exc), "unlock_plan": "mini"}) from exc
    with ENGINE.connect() as connection:
        row = connection.execute(
            select(JOB_HISTORY).where(
                JOB_HISTORY.c.job_id == job_id,
                JOB_HISTORY.c.user_id == user["id"],
                JOB_HISTORY.c.status == "done",
                JOB_HISTORY.c.deleted_at.is_(None),
                JOB_HISTORY.c.retention_until > utcnow(),
            )
        ).first()
    if not row:
        raise HTTPException(404, detail={"code": "LS-JOB-03", "message": "İndirilebilir final ZIP bulunamadı veya saklama süresi doldu."})
    data = JOBS.get(job_id) or {}
    local = Path(str(data.get("result_path") or ""))
    key = str(row.remote_download_key or data.get("remote_download_key") or "")
    if (not local.exists() or local.suffix.casefold() != ".zip") and key and STORAGE.remote:
        local = config.WORK_DIR / job_id / Path(key).name
        STORAGE.download_file(key, local)
        JOBS.update(job_id, result_path=str(local), job_dir=str(local.parent), remote_download_key=key)
    if not local.exists() or local.suffix.casefold() != ".zip":
        raise HTTPException(404, detail={"code": "LS-JOB-03", "message": "Final ZIP şu anda erişilebilir değil."})
    return FileResponse(str(local), media_type="application/zip", filename="LectureSift_Study_Pack.zip")


'''
replace("lecturesift/commerce_routes.py", marker, route + marker)
print("Billing-owned download route applied.")
