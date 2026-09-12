"""Versioned library and admin state. Tables are installed by an explicit release.

Logical folder names never become filesystem paths. Ownership is checked under
the same billing-user lock as account closure and privileged credit changes.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import Boolean, Column, DateTime, MetaData, String, Table, delete, exists, inspect, or_, select, update

from . import billing_service as billing

METADATA = MetaData()
FOLDERS = Table(
    "workspace_folders_v1", METADATA,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("name", String(80), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
ITEMS = Table(
    "workspace_items_v1", METADATA,
    Column("job_id", String(64), primary_key=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("folder_id", String(36), nullable=True, index=True),
)
AD_FREE_GRANTS = Table(
    "admin_ad_free_grants_v1", METADATA,
    Column("user_id", String(36), primary_key=True),
    Column("enabled", Boolean, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
ORDER_ARCHIVES = Table(
    "admin_order_archives_v1", METADATA,
    Column("reference", String(64), primary_key=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


def require_schema(connection):
    if not all(inspect(connection).has_table(table.name) for table in METADATA.sorted_tables):
        raise billing.BillingConfigurationError("Çalışma alanı güncellemesi henüz hazır değil.")


def owner(connection, user_id):
    billing._lock_billing_user(connection, user_id)
    user = connection.execute(select(billing.USERS).where(billing.USERS.c.id == user_id)).first()
    if not user or user.email.endswith("@users.invalid"):
        raise billing.BillingError("Kullanıcı bulunamadı.")
    return user


def _audit(connection, user, action, summary, actor):
    from .rollout_service import _record_admin_account_event
    _record_admin_account_event(connection, user_id=user.id, email=user.email,
                                action=action, summary=summary, actor=actor)


def _reason(value):
    value = str(value or "").strip()
    if not 4 <= len(value) <= 240:
        raise billing.BillingError("İşlem nedenini 4–240 karakterle yaz.")
    return value


def manual_ad_free(connection, user_id):
    # Old releases can still read billing while the new schema is installed.
    if not inspect(connection).has_table(AD_FREE_GRANTS.name):
        return False
    return bool(connection.execute(select(AD_FREE_GRANTS.c.enabled).where(
        AD_FREE_GRANTS.c.user_id == user_id,
    )).scalar())


def visible_order_condition(table):
    # Preserve provider reconciliation and audit records. A later successful
    # callback makes an archived unfinished purchase visible again.
    return or_(table.c.status.in_(["paid", "refunded", "partially_refunded"]), ~exists(select(ORDER_ARCHIVES.c.reference).where(
        ORDER_ARCHIVES.c.reference == table.c.reference,
    )))



def visible_orders(connection, query, table):
    if inspect(connection).has_table(ORDER_ARCHIVES.name):
        return query.where(visible_order_condition(table))
    return query


def export_state(connection, user_id):
    result = {}
    for table in (FOLDERS, ITEMS, AD_FREE_GRANTS):
        if inspect(connection).has_table(table.name):
            result[table.name] = [dict(row._mapping) for row in connection.execute(
                select(table).where(table.c.user_id == user_id)).all()]
    return result

def set_ad_free(user_id, enabled, reason, actor):
    reason = _reason(reason)
    with billing.ENGINE.begin() as connection:
        require_schema(connection)
        user = owner(connection, user_id)
        if user.email.endswith("@guest.lecturesift.invalid"):
            raise billing.BillingError("Önce kayıtlı bir kullanıcı seç.")
        existing = connection.execute(select(AD_FREE_GRANTS).where(AD_FREE_GRANTS.c.user_id == user_id)).first()
        if not existing:
            connection.execute(AD_FREE_GRANTS.insert().values(user_id=user_id, enabled=enabled, updated_at=billing.utcnow()))
        elif bool(existing.enabled) != bool(enabled):
            connection.execute(update(AD_FREE_GRANTS).where(AD_FREE_GRANTS.c.user_id == user_id).values(enabled=enabled, updated_at=billing.utcnow()))
        else:
            return {"manual_ad_free": bool(enabled)}
        _audit(connection, user, "ad_free_grant_changed", f"Kalıcı reklamsız tanımı: {bool(enabled)}. {reason}", actor)
    return {"manual_ad_free": bool(enabled)}


def grant_credits(user_id, credits, days, request_id, reason, actor):
    from . import assistant_wallet as wallet
    from .errors import LectureSiftError
    try:
        wallet.require_available()
    except LectureSiftError as exc:
        raise billing.BillingConfigurationError("Asistan kredisi tanımlama şu anda kullanılamıyor.") from exc
    reason = _reason(reason)
    if isinstance(credits, bool) or not 1 <= credits <= 100_000 or not 1 <= days <= 365:
        raise billing.BillingError("1–100.000 kredi ve 1–365 gün geçerlilik seç.")
    try:
        request_id = str(uuid.UUID(request_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise billing.BillingError("İşlem kimliği geçersiz.") from exc
    reference = "ADMIN-" + request_id
    key = wallet._id(user_id, reference, "")
    now = billing.utcnow()
    with billing.ENGINE.begin() as connection:
        require_schema(connection)
        user = owner(connection, user_id)
        if user.email.endswith("@guest.lecturesift.invalid"):
            raise billing.BillingError("Önce kayıtlı bir kullanıcı seç.")
        previous = connection.execute(select(wallet.GRANTS).where(wallet.GRANTS.c.id == key)).first()
        if previous:
            if previous.credits != credits or round((wallet._utc(previous.expires_at) - wallet._utc(previous.created_at)).total_seconds()) != days * 86400:
                raise billing.BillingError("Bu işlem kimliği farklı bir kredi tanımı için kullanılmış.")
            return {"credits_added": credits, "replayed": True}
        wallet._grant(connection, user_id, reference, "admin", credits, now + timedelta(days=days), now)
        _audit(connection, user, "assistant_credits_added", f"{credits} asistan kredisi, {days} gün. {reason}", actor)
    return {"credits_added": credits, "replayed": False}


def admin_entitlements(user_id):
    from . import assistant_wallet as wallet
    from .errors import LectureSiftError
    available = True
    try:
        wallet.require_available()
    except LectureSiftError:
        available = False
    with billing.ENGINE.begin() as connection:
        require_schema(connection)
        user = owner(connection, user_id)
        profile = connection.execute(select(billing.USER_PROFILES).where(billing.USER_PROFILES.c.user_id == user_id)).first()
        manual = manual_ad_free(connection, user_id)
        permanent = billing._has_permanent_ad_free(connection, user_id)
        now = billing.utcnow()
        blocked = wallet._sync(connection, user_id, now) if available and profile and profile.email_verified_at and not user.email.endswith("@guest.lecturesift.invalid") else set()
        balance = sum(row.remaining for row in wallet._spendable(connection, user_id, now, blocked)) if available else None
    return {"assistant_available": available, "assistant_credits": balance, "manual_ad_free": manual, "permanent_ad_free": permanent,
            "registered": not user.email.endswith("@guest.lecturesift.invalid")}


def archive_unpaid_order(reference, actor):
    with billing.ENGINE.begin() as connection:
        require_schema(connection)
        for table in (billing.PAYMENT_ORDERS, billing.MANUAL_ORDERS):
            record = connection.execute(select(table).where(table.c.reference == reference)).first()
            if record:
                break
        else:
            raise billing.BillingError("Ödeme kaydı bulunamadı.")
        billing._lock_billing_user(connection, record.user_id)
        user = connection.execute(select(billing.USERS).where(billing.USERS.c.id == record.user_id)).one()
        record = connection.execute(select(table).where(table.c.reference == reference).with_for_update()).one()
        if record.status in {"paid", "refunded", "partially_refunded"}:
            raise billing.BillingError("Tamamlanmış veya iade edilmiş ödemeler temizlenemez.")
        if not connection.execute(select(ORDER_ARCHIVES.c.reference).where(ORDER_ARCHIVES.c.reference == reference)).first():
            connection.execute(ORDER_ARCHIVES.insert().values(reference=reference, user_id=user.id, created_at=billing.utcnow()))
            _audit(connection, user, "unfinished_order_archived", f"Tamamlanmamış sipariş panelden kaldırıldı: {reference}", actor)
    return {"reference": reference, "archived": True}


def library(user_id):
    from .jobs import JOBS, is_job_deletable
    with billing.ENGINE.begin() as connection:
        require_schema(connection)
        owner(connection, user_id)
        folders = connection.execute(select(FOLDERS).where(FOLDERS.c.user_id == user_id).order_by(FOLDERS.c.created_at)).all()
        items = connection.execute(select(ITEMS).where(ITEMS.c.user_id == user_id)).all()
    placements = {row.job_id: row.folder_id for row in items}
    jobs = JOBS.list_for_user(user_id, limit=None)
    safe_jobs = [{"job_id": row["job_id"], "title": row.get("title") or "", "created": row.get("created"),
                  "status": row.get("status"), "stage": row.get("stage"), "folder_id": placements.get(row["job_id"]),
                  "stored_bytes": row.get("stored_bytes"), "job_type": row.get("options", {}).get("job_type", "study_pack"),
                  "expires_at": float(row.get("updated", row.get("created", 0))) + max(60, int(row.get("retention_seconds", 86400))),
                  "can_delete": is_job_deletable(row)}
                 for row in jobs]
    return {"folders": [{"id": row.id, "name": row.name} for row in folders], "jobs": safe_jobs,
            "stored_bytes": sum(int(row["stored_bytes"] or 0) for row in safe_jobs),
            "size_complete": all(row["stored_bytes"] is not None for row in safe_jobs)}


def save_folder(user_id, name, folder_id=None):
    name = " ".join(str(name or "").split())
    if not 1 <= len(name) <= 80:
        raise billing.BillingError("Klasör adı 1–80 karakter olmalı.")
    with billing.ENGINE.begin() as connection:
        require_schema(connection)
        owner(connection, user_id)
        rows = connection.execute(select(FOLDERS).where(FOLDERS.c.user_id == user_id)).all()
        if any(row.name.casefold() == name.casefold() and row.id != folder_id for row in rows):
            raise billing.BillingError("Bu isimde bir klasör zaten var.")
        if folder_id:
            if not any(row.id == folder_id for row in rows):
                raise billing.BillingError("Klasör bulunamadı.")
            connection.execute(update(FOLDERS).where(FOLDERS.c.id == folder_id, FOLDERS.c.user_id == user_id).values(name=name))
        else:
            if len(rows) >= 200:
                raise billing.BillingError("En fazla 200 klasör oluşturabilirsin.")
            folder_id = str(uuid.uuid4())
            connection.execute(FOLDERS.insert().values(id=folder_id, user_id=user_id, name=name, created_at=billing.utcnow()))
    return {"id": folder_id, "name": name}


def remove_folder(user_id, folder_id):
    with billing.ENGINE.begin() as connection:
        require_schema(connection)
        owner(connection, user_id)
        row = connection.execute(select(FOLDERS).where(FOLDERS.c.id == folder_id, FOLDERS.c.user_id == user_id)).first()
        if not row:
            raise billing.BillingError("Klasör bulunamadı.")
        connection.execute(update(ITEMS).where(ITEMS.c.user_id == user_id, ITEMS.c.folder_id == folder_id).values(folder_id=None))
        connection.execute(delete(FOLDERS).where(FOLDERS.c.id == folder_id, FOLDERS.c.user_id == user_id))


def move_lesson(user_id, job_id, folder_id):
    from .jobs import JOBS
    with billing.ENGINE.begin() as connection:
        require_schema(connection)
        owner(connection, user_id)
        job = JOBS.metadata(job_id)
        if not job or job.get("options", {}).get("billing_user_id") != user_id:
            raise billing.BillingError("Ders bulunamadı.")
        if folder_id and not connection.execute(select(FOLDERS.c.id).where(FOLDERS.c.id == folder_id, FOLDERS.c.user_id == user_id)).first():
            raise billing.BillingError("Klasör bulunamadı.")
        existing = connection.execute(select(ITEMS).where(ITEMS.c.job_id == job_id)).first()
        if existing:
            if existing.user_id != user_id:
                raise billing.BillingError("Ders bulunamadı.")
            connection.execute(update(ITEMS).where(ITEMS.c.job_id == job_id).values(folder_id=folder_id or None))
        else:
            connection.execute(ITEMS.insert().values(job_id=job_id, user_id=user_id, folder_id=folder_id or None))


def delete_lesson(user_id, job_id):
    from .jobs import JOBS
    # Serialize with account closure and library edits, with no provider AI call.
    with billing.ENGINE.begin() as connection:
        require_schema(connection)
        owner(connection, user_id)
        JOBS.delete_owned(user_id, job_id)
        connection.execute(delete(ITEMS).where(ITEMS.c.job_id == job_id, ITEMS.c.user_id == user_id))


def erase_account_state(connection, user_id):
    for table in (ITEMS, FOLDERS, AD_FREE_GRANTS):
        if inspect(connection).has_table(table.name):
            connection.execute(delete(table).where(table.c.user_id == user_id))
