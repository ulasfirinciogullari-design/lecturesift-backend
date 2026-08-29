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
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import httpx
from sqlalchemy import BigInteger, Column, DateTime, Integer, MetaData, String, Table, func, select

from . import config
from .billing_service import ENGINE


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

_CTX: ContextVar[dict[str, str | None]] = ContextVar(
    "lecturesift_cost_context", default={"job_id": None, "user_id": None}
)
_INIT_LOCK = threading.Lock()
_INITIALIZED = False
_FX_LOCK = threading.Lock()
_FX_CACHE: tuple[datetime, float, str] | None = None

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
        ("render", "Web, worker, PostgreSQL, Key Value ve cron", config.COST_RENDER_MONTHLY_USD, "https://render.com/pricing"),
        ("netlify", "Site barındırma planı", config.COST_NETLIFY_MONTHLY_USD, "https://www.netlify.com/pricing/"),
        ("resend", "İşlemsel e-posta planı", config.COST_RESEND_MONTHLY_USD, "https://resend.com/pricing"),
        ("other", "Diğer sabit servisler", config.COST_OTHER_MONTHLY_USD, "environment"),
    )
    return [
        {"provider": provider, "label": label, "monthly_usd": round(max(0.0, amount), 4), "source": source}
        for provider, label, amount, source in values
    ]


def cost_overview(days: int = 30, limit: int = 100) -> dict[str, Any]:
    init_cost_database()
    safe_days = max(1, min(int(days), 3660))
    since = datetime.now(timezone.utc) - timedelta(days=safe_days)
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
    fx, fx_source = _fx_rate()
    variable_usd = float(total_micro or 0) / 1_000_000
    fixed = _fixed_services()
    fixed_monthly = sum(float(item["monthly_usd"]) for item in fixed)
    period_fixed = fixed_monthly * safe_days / 30.4375
    return {
        "period_days": safe_days,
        "currency": {"base": "USD", "usd_try": round(fx, 4), "source": fx_source},
        "totals": {
            "variable_usd": round(variable_usd, 6),
            "period_fixed_usd": round(period_fixed, 4),
            "combined_usd": round(variable_usd + period_fixed, 4),
            "combined_try": round((variable_usd + period_fixed) * fx, 2),
            "monthly_fixed_usd": round(fixed_monthly, 4),
        },
        "by_provider": [
            {
                "provider": row.provider,
                "service": row.service,
                "cost_usd": round(float(row.cost or 0) / 1_000_000, 6),
                "events": int(row.events or 0),
            }
            for row in totals
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
        "rate_catalog": {
            "openai": RATE_CATALOG,
            "cloudflare_r2": {
                "storage_gb_month_usd": 0.015,
                "class_a_million_usd": 4.50,
                "class_b_million_usd": 0.36,
                "egress_usd": 0,
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
        ],
        "disclaimer": (
            "Uygulamanın gördüğü kullanım üzerinden brüt liste fiyatı ve yapılandırılmış sabit gider tahminidir. "
            "Ücretsiz kotalar, vergiler, kur farkları, sözleşme indirimleri ve sağlayıcıya özel komisyonlar düşülmez; "
            "sağlayıcı faturaları ve ödeme mutabakatları kesin kaynaktır."
        ),
    }
