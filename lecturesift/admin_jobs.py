"""Explicit administrator access to retained lesson outputs, never source paths."""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from . import billing_service as billing, config
from .jobs import JOBS
from .storage import STORAGE

_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def enrich_owners(items: list[dict]) -> list[dict]:
    ids = {item["owner_id"] for item in items if item.get("owner_id")}
    if not ids:
        return items
    with billing.ENGINE.connect() as connection:
        owners = {row.id: row for row in connection.execute(select(
            billing.USERS.c.id, billing.USERS.c.email,
            billing.USER_PROFILES.c.first_name, billing.USER_PROFILES.c.last_name,
        ).select_from(billing.USERS.outerjoin(billing.USER_PROFILES,
            billing.USERS.c.id == billing.USER_PROFILES.c.user_id)).where(billing.USERS.c.id.in_(ids)))}
    for item in items:
        row = owners.get(item.get("owner_id"))
        if row and not row.email.endswith("@users.invalid"):
            item["owner_email"] = row.email
            item["owner_name"] = f"{row.first_name or ''} {row.last_name or ''}".strip()
    return items


def job_metadata(job_id: str) -> dict:
    if not _JOB_ID.fullmatch(job_id):
        raise HTTPException(404, detail={"message": "Ders kaydı bulunamadı."})
    data = JOBS.metadata(job_id)
    if not data:
        raise HTTPException(404, detail={"message": "Ders kaydı silinmiş veya saklama süresi dolmuş."})
    return data


def _file(data: dict, relative: str) -> Path:
    # Both the stored path and the materialized path must stay inside this job.
    base = config.WORK_DIR / data["job_id"]
    if base.is_symlink() or (not (data.get("remote_prefix") and STORAGE.remote)
        and data.get("job_dir") and Path(data["job_dir"]).resolve() != base.resolve()):
        raise HTTPException(404, detail={"message": "Ders dosyası konumu doğrulanamadı."})
    destination = base / relative
    if destination.is_symlink() or not destination.resolve().is_relative_to(base.resolve()):
        raise HTTPException(404, detail={"message": "Ders dosyası konumu doğrulanamadı."})
    path = JOBS.ensure_local_file(data, local_relative=relative)
    if path is None or path.is_symlink() or not path.resolve().is_relative_to(base.resolve()):
        raise HTTPException(404, detail={"message": "Çıktı dosyası bulunamadı veya saklama süresi dolmuş."})
    return path


def _result(data: dict) -> dict:
    if not JOBS.admin_summary(data)["result_ready"]:
        raise HTTPException(409, detail={"message": "Dersin çıktıları henüz hazır değil."})
    path = _file(data, "result.json")
    if path.stat().st_size > 32 * 1024 * 1024:
        raise HTTPException(413, detail={"message": "Sonuç bu ekranda önizlenemeyecek kadar büyük."})
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("invalid result")
        return result
    except (ValueError, OSError) as exc:
        raise HTTPException(503, detail={"message": "Ders sonucu okunamadı. Tekrar dene."}) from exc


def _filename(value: object) -> str:
    name = str(value or "")
    return name if name and name not in {".", ".."} and not any(
        char in name for char in ("/", "\\", "\r", "\n", "\x00")
    ) and len(name) <= 240 else ""


def audit_access(data: dict, actor: str, action: str) -> None:
    from .rollout_service import _record_admin_account_event, init_rollout_database
    owner_id = (data.get("options") or {}).get("billing_user_id")
    if not owner_id:
        return
    init_rollout_database()
    with billing.ENGINE.begin() as connection:
        email = connection.execute(select(billing.USERS.c.email).where(billing.USERS.c.id == owner_id)).scalar()
        if email:
            _record_admin_account_event(connection, user_id=owner_id, email=email,
                action=action, summary=f"Ders: {data['job_id']}", actor=actor)


def detail(job_id: str, actor: str) -> dict:
    data = job_metadata(job_id)
    job = enrich_owners([JOBS.admin_summary(data)])[0]
    response = {"ok": True, "job": job, "result": None, "result_message": None}
    if job["result_ready"]:
        try:
            result = _result(data)
        except HTTPException as exc:
            if exc.status_code not in {404, 413, 503}:
                raise
            response["result_message"] = exc.detail["message"]
        else:
            # No provider diagnostics, storage keys, paths or download URLs.
            preview = {key: result.get(key) for key in (
                "title", "summary", "key_points", "important_terms", "notes", "exam_focus", "quiz", "flashcards",
            )}
            transcript = str(result.get("transcript") or result.get("transcript_original") or "")
            preview["transcript"] = transcript[:250_000]
            preview["transcript_truncated"] = len(transcript) > 250_000
            sources = result.get("sources") or {}
            preview["source_files"] = list(dict.fromkeys(
                _filename(name) for key in ("source_files", "audio_files", "visual_files")
                for name in (sources.get(key) or []) if _filename(name)
            ))
            preview["artifacts"] = [{"file": _filename(item.get("file")), "label": str(item.get("label") or item["file"])}
                for item in result.get("artifacts", []) if isinstance(item, dict) and _filename(item.get("file"))]
            response["result"] = preview
    audit_access(data, actor, "lesson_inspected")
    return response


def artifact(job_id: str, filename: str, actor: str) -> Path:
    data = job_metadata(job_id)
    if not _filename(filename):
        raise HTTPException(404, detail={"message": "Çıktı dosyası bulunamadı."})
    result = _result(data)
    if filename not in {item.get("file") for item in result.get("artifacts", []) if isinstance(item, dict)}:
        raise HTTPException(404, detail={"message": "Çıktı dosyası bulunamadı."})
    path = _file(data, f"package/{filename}")
    audit_access(data, actor, "lesson_output_downloaded")
    return path
