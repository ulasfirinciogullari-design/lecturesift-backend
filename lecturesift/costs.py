"""Durable, invoice-aware operating cost ledger.

Only usage quantities, model/service names and sanitized job identifiers are
stored. Provider secrets, prompts, document text and personal data never enter
the ledger. Monetary values use integer micro-US-dollars to avoid float drift.
"""

from __future__ import annotations

import json
import threading
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import BigInteger, Column, Date, DateTime, Integer, MetaData, String, Table, UniqueConstraint, delete, func, select, update

from . import config
from .billing_service import (
    ENGINE,
    IYZICO_PAYMENT_PROVIDERS,
    MANUAL_ORDERS,
    PAYMENT_ORDERS,
    USAGE_EVENTS,
    init_billing_database,
)


_METADATA = MetaData()
COST_EVENTS = Table(
    "lecturesift_cost_events",
    _METADATA,
    Column("id", String(36), primary_key=True),
    Column("job_id", String(64), nullable=True, index=True),
    Column("user_id", String(36), nullable=True, index=True),
    Column("provider", String(32), nullable=False, index=True),
    Column("service", String(64), nullable=False, index=True),
    Column("resource", String(96), nullable=False),
    Column("quantity_microunits", BigInteger, nullable=False),
    Column("unit", String(32), nullable=False),
    Column("cost_microusd", BigInteger, nullable=False),
    Column("pricing_source", String(500), nullable=False),
    Column("pricing_effective_at", String(16), nullable=False),
    Column("estimation", String(24), nullable=False),
    Column("metadata_json", String(1000), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
)

COST_ACTUALS = Table(
    "lecturesift_cost_actuals",
    _METADATA,
    Column("id", String(36), primary_key=True),
    Column("provider", String(32), nullable=False, index=True),
    Column("service", String(64), nullable=False),
    Column("period_start", Date, nullable=False, index=True),
    Column("period_end", Date, nullable=False, index=True),
    Column("currency", String(3), nullable=False),
    Column("subtotal_minor", BigInteger, nullable=False),
    Column("tax_minor", BigInteger, nullable=False),
    Column("label", String(160), nullable=False),
    Column("source_reference", String(160), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "provider",
        "service",
        "period_start",
        "period_end",
        name="uq_lecturesift_cost_actual_period",
    ),
)

_CTX: ContextVar[dict[str, str | None]] = ContextVar(
    "lecturesift_cost_context", default={"job_id": None, "user_id": None}
)
_INIT_LOCK = threading.Lock()
_INITIALIZED = False
_FX_LOCK = threading.Lock()
_FX_CACHE: tuple[datetime, float, str] | None = None
_BUSINESS_TIMEZONE = ZoneInfo("Europe/Istanbul")

OPENAI_SOURCE = "https://developers.openai.com/api/docs/models"
OPENAI_EFFECTIVE = "2026-08-29"
RATE_CATALOG = {
    "gpt-4o-mini": {
        "input": 0.15,
        "cached_input": 0.075,
        "output": 0.60,
        "basis": 1_000_000,
        "unit": "token",
        "source": "https://developers.openai.com/api/docs/models/gpt-4o-mini",
    },
    "gpt-4o-mini-transcribe": {
        "input": 1.25,
        "output": 5.00,
        "basis": 1_000_000,
        "unit": "audio_token",
        "fallback_minute": 0.003,
        "source": "https://developers.openai.com/api/docs/models/gpt-4o-mini-transcribe",
    },
    "gpt-4o-transcribe-diarize": {
        "input": 2.50,
        "output": 10.00,
        "basis": 1_000_000,
        "unit": "audio_token",
        "fallback_minute": 0.006,
        "source": "https://developers.openai.com/api/docs/models/gpt-4o-transcribe-diarize",
    },
}


def init_cost_database() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    with _INIT_LOCK:
        if not _INITIALIZED:
            COST_EVENTS.create(bind=ENGINE, checkfirst=True)
            COST_ACTUALS.create(bind=ENGINE, checkfirst=True)
            _INITIALIZED = True


@contextmanager
def cost_context(job_id: str | None, user_id: str | None) -> Iterator[None]:
    token = _CTX.set({"job_id": job_id or None, "user_id": user_id or None})
    try:
        yield
    finally:
        _CTX.reset(token)


def _safe_metadata(value: dict[str, Any] | None) -> str:
    allowed: dict[str, Any] = {}
    for key, item in (value or {}).items():
        if key not in {"request_kind", "fallback", "operation", "bytes", "status"}:
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            allowed[key] = item
    return json.dumps(allowed, ensure_ascii=True, separators=(",", ":"))[:1000]


def record_cost(
    *,
    provider: str,
    service: str,
    resource: str,
    quantity: float,
    unit: str,
    price_usd: float,
    price_basis: float = 1.0,
    pricing_source: str,
    pricing_effective_at: str,
    estimation: str = "metered",
    metadata: dict[str, Any] | None = None,
    job_id: str | None = None,
    user_id: str | None = None,
) -> int:
    """Record one sanitized usage event and return its cost in micro-USD."""
    init_cost_database()
    context = _CTX.get()
    safe_quantity = max(0.0, float(quantity))
    cost_microusd = max(0, int(round(safe_quantity * float(price_usd) * 1_000_000 / price_basis)))
    with ENGINE.begin() as connection:
        connection.execute(
            COST_EVENTS.insert().values(
                id=str(uuid.uuid4()),
                job_id=(job_id or context.get("job_id")) or None,
                user_id=(user_id or context.get("user_id")) or None,
                provider=provider[:32],
                service=service[:64],
                resource=resource[:96],
                quantity_microunits=int(round(safe_quantity * 1_000_000)),
                unit=unit[:32],
                cost_microusd=cost_microusd,
                pricing_source=pricing_source[:500],
                pricing_effective_at=pricing_effective_at[:16],
                estimation=estimation[:24],
                metadata_json=_safe_metadata(metadata),
                created_at=datetime.now(timezone.utc),
            )
        )
    return cost_microusd


def _usage_value(usage: Any, *names: str) -> int:
    for name in names:
        value = getattr(usage, name, None)
        if value is None and isinstance(usage, dict):
            value = usage.get(name)
        if value is not None:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                pass
    return 0


def _nested_usage_value(usage: Any, detail_names: tuple[str, ...], value_name: str) -> int:
    for name in detail_names:
        detail = getattr(usage, name, None)
        if detail is None and isinstance(usage, dict):
            detail = usage.get(name)
        if detail is None:
            continue
        value = getattr(detail, value_name, None)
        if value is None and isinstance(detail, dict):
            value = detail.get(value_name)
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            continue
    return 0


def record_openai_response(model: str, response: Any, request_kind: str) -> bool:
    """Record provider-reported token usage. Returns False when unavailable."""
    usage = getattr(response, "usage", None)
    if not usage:
        return False
    rate = RATE_CATALOG.get(model)
    if not rate:
        return False
    input_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
    output_tokens = _usage_value(usage, "completion_tokens", "output_tokens")
    cached_tokens = min(
        input_tokens,
        _nested_usage_value(usage, ("prompt_tokens_details", "input_tokens_details"), "cached_tokens"),
    )
    regular_input = max(0, input_tokens - cached_tokens)
    wrote = False
    for resource, quantity, price in (
        (f"{model}:input", regular_input, float(rate["input"])),
        (f"{model}:cached_input", cached_tokens, float(rate.get("cached_input", rate["input"]))),
        (f"{model}:output", output_tokens, float(rate["output"])),
    ):
        if not quantity:
            continue
        record_cost(
            provider="openai",
            service="ai",
            resource=resource,
            quantity=quantity,
            unit=str(rate["unit"]),
            price_usd=price,
            price_basis=float(rate["basis"]),
            pricing_source=str(rate["source"]),
            pricing_effective_at=OPENAI_EFFECTIVE,
            estimation="provider_usage",
            metadata={"request_kind": request_kind},
        )
        wrote = True
    return wrote


def record_transcription_fallback(model: str, duration_seconds: float) -> None:
    rate = RATE_CATALOG[model]
    record_cost(
        provider="openai",
        service="transcription",
        resource=model,
        quantity=max(0.0, duration_seconds) / 60.0,
        unit="audio_minute",
        price_usd=float(rate["fallback_minute"]),
        pricing_source=str(rate["source"]),
        pricing_effective_at=OPENAI_EFFECTIVE,
        estimation="duration_estimate",
        metadata={"request_kind": "transcription", "fallback": True},
    )


def record_r2_operation(operation: str, *, bytes_count: int = 0, job_id: str | None = None) -> None:
    if operation == "write":
        price, basis, service = 4.50, 1_000_000, "class_a_operation"
    elif operation == "read":
        price, basis, service = 0.36, 1_000_000, "class_b_operation"
    else:
        price, basis, service = 0.0, 1, "free_operation"
    record_cost(
        provider="cloudflare",
        service="r2",
        resource=service,
        quantity=1,
        unit="operation",
        price_usd=price,
        price_basis=basis,
        pricing_source="https://developers.cloudflare.com/r2/pricing/",
        pricing_effective_at="2026-08-07",
        estimation="app_observed",
        metadata={"operation": operation, "bytes": max(0, int(bytes_count))},
        job_id=job_id,
    )


def _cost_slug(value: str, *, field: str, max_length: int) -> str:
    cleaned = str(value or "").strip().casefold().replace(" ", "_")
    if not cleaned or len(cleaned) > max_length or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in cleaned
    ):
        raise ValueError(f"{field} yalnızca harf, rakam, tire ve alt çizgi içerebilir.")
    return cleaned


def save_actual_cost(
    *,
    provider: str,
    service: str,
    period_start: date,
    period_end: date,
    currency: str,
    subtotal_minor: int,
    tax_minor: int,
    label: str,
    source_reference: str,
) -> dict[str, Any]:
    """Create or replace one invoice-backed cost period.

    The source reference is a human-readable invoice/statement identifier, not
    an API secret or an uploaded invoice body.
    """
    init_cost_database()
    selected_provider = _cost_slug(provider, field="Sağlayıcı", max_length=32)
    selected_service = _cost_slug(service, field="Hizmet", max_length=64)
    if period_end < period_start:
        raise ValueError("Fatura döneminin bitişi başlangıçtan önce olamaz.")
    selected_currency = str(currency or "").strip().upper()
    if selected_currency not in {"USD", "TRY"}:
        raise ValueError("Kesin maliyet kaydı için desteklenen para birimleri USD ve TRY'dir.")
    safe_subtotal = int(subtotal_minor)
    safe_tax = int(tax_minor)
    if safe_subtotal < 0 or safe_tax < 0:
        raise ValueError("Tutar ve vergi negatif olamaz.")
    safe_label = str(label or "").strip()[:160]
    safe_source = str(source_reference or "").strip()[:160]
    if not safe_label or not safe_source:
        raise ValueError("Gider açıklaması ve fatura/mutabakat referansı zorunludur.")
    now = datetime.now(timezone.utc)
    with ENGINE.begin() as connection:
        existing = connection.execute(
            select(COST_ACTUALS.c.id).where(
                COST_ACTUALS.c.provider == selected_provider,
                COST_ACTUALS.c.service == selected_service,
                COST_ACTUALS.c.period_start == period_start,
                COST_ACTUALS.c.period_end == period_end,
            )
        ).scalar_one_or_none()
        actual_id = str(existing or uuid.uuid4())
        values = {
            "provider": selected_provider,
            "service": selected_service,
            "period_start": period_start,
            "period_end": period_end,
            "currency": selected_currency,
            "subtotal_minor": safe_subtotal,
            "tax_minor": safe_tax,
            "label": safe_label,
            "source_reference": safe_source,
            "updated_at": now,
        }
        if existing:
            connection.execute(update(COST_ACTUALS).where(COST_ACTUALS.c.id == actual_id).values(**values))
        else:
            connection.execute(COST_ACTUALS.insert().values(id=actual_id, created_at=now, **values))
    return {"id": actual_id, "updated": bool(existing)}


def delete_actual_cost(actual_id: str) -> bool:
    init_cost_database()
    with ENGINE.begin() as connection:
        result = connection.execute(delete(COST_ACTUALS).where(COST_ACTUALS.c.id == str(actual_id)))
    return bool(result.rowcount)


def _fx_rate() -> tuple[float, str]:
    global _FX_CACHE
    now = datetime.now(timezone.utc)
    with _FX_LOCK:
        if _FX_CACHE and now - _FX_CACHE[0] < timedelta(hours=6):
            return _FX_CACHE[1], _FX_CACHE[2]
        try:
            response = httpx.get("https://www.tcmb.gov.tr/kurlar/today.xml", timeout=4.0)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            node = root.find(".//Currency[@Kod='USD']/ForexSelling")
            rate = float((node.text or "").replace(",", ".")) if node is not None else 0.0
            if rate > 0:
                _FX_CACHE = (now, rate, "TCMB günlük döviz satış kuru")
                return rate, _FX_CACHE[2]
        except Exception:
            pass
        return config.COST_USD_TRY_FALLBACK, "Yapılandırılmış yedek kur"


def _fixed_services() -> list[dict[str, Any]]:
    values = (
        (
            "render",
            "Web, worker, PostgreSQL, Key Value ve cron",
            config.COST_RENDER_MONTHLY_USD,
            config.COST_RENDER_CONFIRMED,
            "monthly",
            "LECTURESIFT_COST_RENDER_MONTHLY_USD",
            "LECTURESIFT_COST_RENDER_CONFIRMED",
            "https://render.com/pricing",
        ),
        (
            "netlify",
            "Site barındırma planı ve dahil krediler",
            config.COST_NETLIFY_MONTHLY_USD,
            config.COST_NETLIFY_CONFIRMED,
            "monthly",
            "LECTURESIFT_COST_NETLIFY_MONTHLY_USD",
            "LECTURESIFT_COST_NETLIFY_CONFIRMED",
            "https://www.netlify.com/pricing/",
        ),
        (
            "resend",
            "İşlemsel e-posta planı",
            config.COST_RESEND_MONTHLY_USD,
            config.COST_RESEND_CONFIRMED,
            "monthly",
            "LECTURESIFT_COST_RESEND_MONTHLY_USD",
            "LECTURESIFT_COST_RESEND_CONFIRMED",
            "https://resend.com/pricing",
        ),
        (
            "domain",
            "Alan adı kaydı; yıllık tutar aya bölünür",
            config.COST_DOMAIN_ANNUAL_USD,
            config.COST_DOMAIN_CONFIRMED,
            "annual",
            "LECTURESIFT_COST_DOMAIN_ANNUAL_USD",
            "LECTURESIFT_COST_DOMAIN_CONFIRMED",
            "provider_invoice",
        ),
        (
            "other",
            "Muhasebe, izleme ve diğer sabit servisler",
            config.COST_OTHER_MONTHLY_USD,
            config.COST_OTHER_CONFIRMED,
            "monthly",
            "LECTURESIFT_COST_OTHER_MONTHLY_USD",
            "LECTURESIFT_COST_OTHER_CONFIRMED",
            "provider_invoice",
        ),
    )
    services: list[dict[str, Any]] = []
    for provider, label, amount, confirmed, cadence, env_key, confirmation_key, source in values:
        monthly = float(amount) / 12.0 if cadence == "annual" else float(amount)
        services.append(
            {
                "provider": provider,
                "label": label,
                "amount_usd": round(max(0.0, float(amount)), 4),
                "cadence": cadence,
                "monthly_usd": round(max(0.0, monthly), 4),
                "configured": bool(amount or confirmed),
                "confirmed": bool(confirmed),
                "source": source,
                "configuration_key": env_key,
                "confirmation_key": confirmation_key,
            }
        )
    return services


def _covered_days(
    rows: list[dict[str, Any]],
    provider: str,
    period_start: date,
    period_end: date,
) -> int:
    """Return unique calendar days covered by invoice records for a provider."""
    intervals: list[tuple[date, date]] = []
    for item in rows:
        if item["provider"] != provider:
            continue
        start = max(period_start, date.fromisoformat(item["period_start"]))
        end = min(period_end, date.fromisoformat(item["period_end"]))
        if start <= end:
            intervals.append((start, end))
    if not intervals:
        return 0
    intervals.sort()
    merged: list[tuple[date, date]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + timedelta(days=1):
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return sum((end - start).days + 1 for start, end in merged)


def _actual_cost_public(row: Any, fx: float) -> dict[str, Any]:
    total_minor = int(row.subtotal_minor or 0) + int(row.tax_minor or 0)
    total = total_minor / 100.0
    total_usd = total if row.currency == "USD" else total / fx
    total_try = total if row.currency == "TRY" else total * fx
    return {
        "id": row.id,
        "provider": row.provider,
        "service": row.service,
        "period_start": row.period_start.isoformat(),
        "period_end": row.period_end.isoformat(),
        "currency": row.currency,
        "subtotal_minor": int(row.subtotal_minor or 0),
        "tax_minor": int(row.tax_minor or 0),
        "total_minor": total_minor,
        "total_usd": round(total_usd, 4),
        "total_try": round(total_try, 2),
        "label": row.label,
        "source_reference": row.source_reference,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def cost_overview(days: int = 30, limit: int = 100) -> dict[str, Any]:
    init_cost_database()
    init_billing_database()
    safe_days = max(1, min(int(days), 3660))
    generated_at = datetime.now(timezone.utc)
    since = generated_at - timedelta(days=safe_days)
    # Invoice periods are merchant calendar dates. Near midnight in Türkiye,
    # UTC can still be on the previous date and would otherwise hide a cost
    # entered for "today" from a one-day report.
    report_end_date = generated_at.astimezone(_BUSINESS_TIMEZONE).date()
    report_start_date = report_end_date - timedelta(days=safe_days - 1)
    with ENGINE.connect() as connection:
        totals = connection.execute(
            select(
                COST_EVENTS.c.provider,
                COST_EVENTS.c.service,
                func.sum(COST_EVENTS.c.cost_microusd).label("cost"),
                func.count().label("events"),
            )
            .where(COST_EVENTS.c.created_at >= since)
            .group_by(COST_EVENTS.c.provider, COST_EVENTS.c.service)
            .order_by(func.sum(COST_EVENTS.c.cost_microusd).desc())
        ).all()
        estimation_totals = connection.execute(
            select(
                COST_EVENTS.c.estimation,
                func.sum(COST_EVENTS.c.cost_microusd).label("cost"),
                func.count().label("events"),
            )
            .where(COST_EVENTS.c.created_at >= since)
            .group_by(COST_EVENTS.c.estimation)
            .order_by(func.sum(COST_EVENTS.c.cost_microusd).desc())
        ).all()
        resources = connection.execute(
            select(
                COST_EVENTS.c.provider,
                COST_EVENTS.c.service,
                COST_EVENTS.c.resource,
                COST_EVENTS.c.unit,
                COST_EVENTS.c.estimation,
                func.sum(COST_EVENTS.c.quantity_microunits).label("quantity"),
                func.sum(COST_EVENTS.c.cost_microusd).label("cost"),
                func.count().label("events"),
                func.min(COST_EVENTS.c.pricing_source).label("pricing_source"),
                func.min(COST_EVENTS.c.pricing_effective_at).label("pricing_effective_at"),
            )
            .where(COST_EVENTS.c.created_at >= since)
            .group_by(
                COST_EVENTS.c.provider,
                COST_EVENTS.c.service,
                COST_EVENTS.c.resource,
                COST_EVENTS.c.unit,
                COST_EVENTS.c.estimation,
            )
            .order_by(func.sum(COST_EVENTS.c.cost_microusd).desc())
        ).all()
        jobs = connection.execute(
            select(
                COST_EVENTS.c.job_id,
                func.sum(COST_EVENTS.c.cost_microusd).label("cost"),
                func.min(COST_EVENTS.c.created_at).label("started_at"),
                func.max(COST_EVENTS.c.created_at).label("updated_at"),
                func.count().label("events"),
            )
            .where(COST_EVENTS.c.created_at >= since, COST_EVENTS.c.job_id.is_not(None))
            .group_by(COST_EVENTS.c.job_id)
            .order_by(func.sum(COST_EVENTS.c.cost_microusd).desc())
            .limit(max(1, min(int(limit), 500)))
        ).all()
        total_micro = connection.execute(
            select(func.coalesce(func.sum(COST_EVENTS.c.cost_microusd), 0)).where(COST_EVENTS.c.created_at >= since)
        ).scalar_one()
        distinct_jobs = connection.execute(
            select(func.count(func.distinct(COST_EVENTS.c.job_id))).where(
                COST_EVENTS.c.created_at >= since,
                COST_EVENTS.c.job_id.is_not(None),
            )
        ).scalar_one()
        usage_minutes = connection.execute(
            select(func.coalesce(func.sum(USAGE_EVENTS.c.minutes), 0)).where(USAGE_EVENTS.c.occurred_at >= since)
        ).scalar_one()
        actual_rows = connection.execute(
            select(COST_ACTUALS)
            .where(
                COST_ACTUALS.c.period_end >= report_start_date,
                COST_ACTUALS.c.period_start <= report_end_date,
            )
            .order_by(COST_ACTUALS.c.period_end.desc(), COST_ACTUALS.c.provider.asc())
        ).all()
        manual_revenue = connection.execute(
            select(
                MANUAL_ORDERS.c.currency,
                func.sum(MANUAL_ORDERS.c.amount_minor).label("amount"),
                func.count().label("orders"),
            )
            .where(MANUAL_ORDERS.c.status == "paid", MANUAL_ORDERS.c.created_at >= since)
            .group_by(MANUAL_ORDERS.c.currency)
        ).all()
        payment_revenue = connection.execute(
            select(
                PAYMENT_ORDERS.c.currency,
                func.sum(PAYMENT_ORDERS.c.amount_minor).label("amount"),
                func.count().label("orders"),
            )
            .where(PAYMENT_ORDERS.c.status == "paid", PAYMENT_ORDERS.c.created_at >= since)
            .group_by(PAYMENT_ORDERS.c.currency)
        ).all()
        paid_providers = set(
            connection.execute(
                select(PAYMENT_ORDERS.c.provider).where(
                    PAYMENT_ORDERS.c.status == "paid", PAYMENT_ORDERS.c.created_at >= since
                )
            ).scalars()
        )
    fx, fx_source = _fx_rate()
    variable_usd = float(total_micro or 0) / 1_000_000
    fixed = _fixed_services()
    fixed_monthly = sum(float(item["monthly_usd"]) for item in fixed)
    confirmed_fixed_monthly = sum(float(item["monthly_usd"]) for item in fixed if item["confirmed"])
    period_fixed = fixed_monthly * safe_days / 30.4375
    period_confirmed_fixed = confirmed_fixed_monthly * safe_days / 30.4375
    estimation_map = {str(row.estimation): float(row.cost or 0) / 1_000_000 for row in estimation_totals}
    provider_reported_usd = estimation_map.get("provider_usage", 0.0)
    observed_usage_usd = estimation_map.get("app_observed", 0.0)
    estimated_usage_usd = sum(
        amount for mode, amount in estimation_map.items() if mode not in {"provider_usage", "app_observed"}
    )
    actuals = [_actual_cost_public(row, fx) for row in actual_rows]
    actual_invoice_usd = sum(float(item["total_usd"]) for item in actuals)
    actual_by_currency: dict[str, dict[str, int]] = {}
    for item in actuals:
        bucket = actual_by_currency.setdefault(item["currency"], {"total_minor": 0, "records": 0})
        bucket["total_minor"] += int(item["total_minor"])
        bucket["records"] += 1
    revenue: dict[str, dict[str, int]] = {}
    for row in [*manual_revenue, *payment_revenue]:
        code = str(row.currency or "").upper()
        bucket = revenue.setdefault(code, {"amount_minor": 0, "orders": 0})
        bucket["amount_minor"] += int(row.amount or 0)
        bucket["orders"] += int(row.orders or 0)
    known_revenue_usd = 0.0
    unsupported_revenue: list[str] = []
    for currency, item in revenue.items():
        if currency == "USD":
            known_revenue_usd += item["amount_minor"] / 100.0
        elif currency == "TRY":
            known_revenue_usd += item["amount_minor"] / 100.0 / fx
        else:
            unsupported_revenue.append(currency)
    operating_estimate = variable_usd + period_fixed
    contribution_usd = known_revenue_usd - operating_estimate
    # These services are part of the deployed architecture even when their
    # current invoice is zero or the planning value has not yet been entered.
    fixed_active = {"render", "netlify", "resend", "domain"}
    fixed_active.update(str(item["provider"]) for item in fixed if item["configured"])
    metered_active = {str(row.provider) for row in totals}
    payment_active = {
        "iyzico" if str(item) in IYZICO_PAYMENT_PROVIDERS else str(item)
        for item in paid_providers
    }
    advertising_active = {"google_ads"} if config.GOOGLE_ADS_ID else set()
    active_providers = sorted(fixed_active | metered_active | payment_active | advertising_active)
    coverage_by_provider = {
        provider: min(safe_days, _covered_days(actuals, provider, report_start_date, report_end_date))
        for provider in active_providers
    }
    verified_providers = sorted(
        provider for provider, covered in coverage_by_provider.items() if covered == safe_days
    )
    partially_verified = sorted(
        provider for provider, covered in coverage_by_provider.items() if 0 < covered < safe_days
    )
    unreconciled = sorted(provider for provider, covered in coverage_by_provider.items() if covered < safe_days)
    provider_days = len(active_providers) * safe_days
    covered_provider_days = sum(coverage_by_provider.values())
    coverage = 100.0 if not provider_days else covered_provider_days * 100.0 / provider_days
    processed_minutes = int(usage_minutes or 0)
    job_count = int(distinct_jobs or 0)
    return {
        "generated_at": generated_at.isoformat(),
        "period_days": safe_days,
        "period": {
            "start": since.isoformat(),
            "end": generated_at.isoformat(),
            "invoice_start": report_start_date.isoformat(),
            "invoice_end": report_end_date.isoformat(),
        },
        "currency": {"base": "USD", "usd_try": round(fx, 4), "source": fx_source},
        "totals": {
            "variable_usd": round(variable_usd, 6),
            "provider_reported_list_usd": round(provider_reported_usd, 6),
            "observed_list_usd": round(observed_usage_usd, 6),
            "estimated_usage_usd": round(estimated_usage_usd, 6),
            "period_fixed_usd": round(period_fixed, 4),
            "period_confirmed_fixed_usd": round(period_confirmed_fixed, 4),
            "period_unconfirmed_fixed_usd": round(max(0.0, period_fixed - period_confirmed_fixed), 4),
            "combined_usd": round(operating_estimate, 4),
            "combined_try": round(operating_estimate * fx, 2),
            "monthly_fixed_usd": round(fixed_monthly, 4),
            "actual_invoice_usd": round(actual_invoice_usd, 4),
        },
        "accuracy": {
            "status": "verified" if active_providers and not unreconciled else ("partial" if covered_provider_days else "unverified"),
            "coverage_percent": round(coverage, 1),
            "active_providers": active_providers,
            "verified_providers": verified_providers,
            "partially_verified_providers": partially_verified,
            "unreconciled_providers": unreconciled,
            "covered_provider_days": covered_provider_days,
            "required_provider_days": provider_days,
            "coverage_days_by_provider": coverage_by_provider,
            "provider_reported_events": sum(int(row.events or 0) for row in estimation_totals if row.estimation == "provider_usage"),
            "estimated_events": sum(int(row.events or 0) for row in estimation_totals if row.estimation != "provider_usage"),
            "rule": "Yüzde 100 yalnızca seçili takvim döneminin her günü, her aktif sağlayıcı için fatura/mutabakat kaydıyla kapsandığında gösterilir.",
        },
        "unit_economics": {
            "processed_minutes": processed_minutes,
            "costed_jobs": job_count,
            "variable_cost_per_minute_usd": round(variable_usd / processed_minutes, 8) if processed_minutes else None,
            "operating_cost_per_minute_usd": round(operating_estimate / processed_minutes, 8) if processed_minutes else None,
            "operating_cost_per_job_usd": round(operating_estimate / job_count, 6) if job_count else None,
            "known_revenue_usd": round(known_revenue_usd, 4),
            "contribution_before_fees_tax_usd": round(contribution_usd, 4),
            "contribution_margin_percent": round(contribution_usd * 100.0 / known_revenue_usd, 2) if known_revenue_usd else None,
            "unsupported_revenue_currencies": sorted(unsupported_revenue),
            "warning": "Katkı payı; ödeme komisyonu, iade, KDV/vergiler ve faturaya girmemiş giderler düşülmeden önceki göstergedir.",
        },
        "revenue_by_currency": [
            {"currency": currency, **item} for currency, item in sorted(revenue.items())
        ],
        "actual_by_currency": [
            {"currency": currency, **item} for currency, item in sorted(actual_by_currency.items())
        ],
        "by_provider": [
            {
                "provider": row.provider,
                "service": row.service,
                "cost_usd": round(float(row.cost or 0) / 1_000_000, 6),
                "events": int(row.events or 0),
            }
            for row in totals
        ],
        "by_resource": [
            {
                "provider": row.provider,
                "service": row.service,
                "resource": row.resource,
                "quantity": round(float(row.quantity or 0) / 1_000_000, 6),
                "unit": row.unit,
                "estimation": row.estimation,
                "cost_usd": round(float(row.cost or 0) / 1_000_000, 6),
                "events": int(row.events or 0),
                "pricing_source": row.pricing_source,
                "pricing_effective_at": row.pricing_effective_at,
            }
            for row in resources
        ],
        "jobs": [
            {
                "job_id": row.job_id,
                "cost_usd": round(float(row.cost or 0) / 1_000_000, 6),
                "cost_try": round(float(row.cost or 0) / 1_000_000 * fx, 4),
                "events": int(row.events or 0),
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in jobs
        ],
        "fixed_services": fixed,
        "actual_costs": actuals,
        "rate_catalog": {
            "openai": RATE_CATALOG,
            "cloudflare_r2": {
                "storage_gb_month_usd": 0.015,
                "class_a_million_usd": 4.50,
                "class_b_million_usd": 0.36,
                "egress_usd": 0,
                "standard_free_storage_gb_month": 10,
                "standard_free_class_a_operations": 1_000_000,
                "standard_free_class_b_operations": 10_000_000,
                "billing_rounds_up_to_whole_unit": True,
                "source": "https://developers.cloudflare.com/r2/pricing/",
                "effective_at": "2026-08-07",
            },
            "netlify": {"source": "https://www.netlify.com/pricing/", "effective_at": "2026-08-29"},
            "render": {"source": "https://render.com/pricing", "effective_at": "2026-08-29"},
            "resend": {"source": "https://resend.com/pricing", "effective_at": "2026-08-29"},
        },
        "external_invoice_sources": [
            {
                "provider": "iyzico / PayTR",
                "label": "Kart komisyonu, iade ve işlem ücretleri",
                "reason": "İş yeri sözleşmesine özeldir; sağlayıcı mutabakatı kesin kaynaktır.",
            },
            {
                "provider": "Google Ads",
                "label": "Reklam harcaması",
                "reason": "Google Ads faturalandırma hesabından takip edilir; site trafiği maliyet değildir.",
            },
            {
                "provider": "Cloudflare R2",
                "label": "Depolanan GB-ay ve ücretsiz kullanım kotası",
                "reason": "Uygulama istekleri ölçer; kesin GB-ay ve ücretsiz kota etkisi Cloudflare faturasında görülür.",
            },
            {
                "provider": "Render / Netlify / Resend / alan adı",
                "label": "Plan, ek kullanım, vergi ve dönemsel indirimler",
                "reason": "Sabit plan alanları bütçedir; kesin kayıt için aynı dönemin faturası mutabakat bölümüne girilir.",
            },
        ],
        "disclaimer": (
            "Operasyon tahmini, sağlayıcının bildirdiği kullanım veya uygulamanın gördüğü adetler ile güncel brüt liste "
            "fiyatlarını kullanır. Cloudflare ücretsiz kotası ve yuvarlaması, vergiler, kur farkı, sözleşme indirimi, "
            "ödeme komisyonu ve iadeler yalnızca fatura/mutabakat kaydı varsa kesin toplamda yer alır. Muhasebesel kesin "
            "kayıtlar için sağlayıcı faturası, banka ekstresi ve ödeme kuruluşu mutabakatı kesin kaynaktır."
        ),
    }
