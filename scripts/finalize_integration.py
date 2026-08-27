from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str, *, count: int = 1) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    found = text.count(old)
    if found != count:
        raise RuntimeError(f"{path}: expected {count}, found {found}: {old[:120]!r}")
    target.write_text(text.replace(old, new), encoding="utf-8")


def append_before_body(path: str, script: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if script in text:
        return
    if "</body>" not in text:
        raise RuntimeError(f"{path}: closing body not found")
    target.write_text(text.replace("</body>", f'{script}</body>', 1), encoding="utf-8")


# ReportLab requires an integer alignment constant, not None.
replace("lecturesift/exports.py", "from reportlab.lib.enums import TA_CENTER, TA_RIGHT\n", "from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT\n")
replace("lecturesift/exports.py", '    alignment = TA_RIGHT if language == "ar" else None\n', '    alignment = TA_RIGHT if language == "ar" else TA_LEFT\n')

# Preserve compatibility with existing tests/extensions that monkeypatch the old
# five-argument study-pack callable while the production implementation accepts
# transcript_segments.
replace(
    "lecturesift/pipeline.py",
    "def _public_options(options: dict) -> dict:\n    return {key: value for key, value in options.items() if key != \"billing_user_id\"}\n\n\n",
    "def _public_options(options: dict) -> dict:\n    return {key: value for key, value in options.items() if key != \"billing_user_id\"}\n\n\ndef _make_study_pack_compatible(transcript: str, options: dict, transcript_segments: list[dict]) -> dict:\n    try:\n        return make_study_pack(\n            transcript, options[\"output_language\"], options[\"summary_style\"],\n            options[\"quiz_count\"], options[\"flashcard_count\"],\n            transcript_segments=transcript_segments,\n        )\n    except TypeError as exc:\n        if \"transcript_segments\" not in str(exc):\n            raise\n        return make_study_pack(\n            transcript, options[\"output_language\"], options[\"summary_style\"],\n            options[\"quiz_count\"], options[\"flashcard_count\"],\n        )\n\n\n",
)
replace(
    "lecturesift/pipeline.py",
    "        study_pack = make_study_pack(\n            original_transcript,\n            options[\"output_language\"],\n            options[\"summary_style\"],\n            options[\"quiz_count\"],\n            options[\"flashcard_count\"],\n            transcript_segments=transcript_segments,\n        )\n",
    "        study_pack = _make_study_pack_compatible(original_transcript, options, transcript_segments)\n",
)

# One-time credits do not expire while unused; rights for each completed paid
# job remain attached to that job after the credit is consumed.
replace(
    "lecturesift/commerce.py",
    "        has_paid_order = _paid_manual_order_exists(connection, user_id)\n    return {\n        \"plan_code\": plan_code,\n        \"download_enabled\": bool(plan.download_enabled or (download_until and download_until > now) or has_paid_order),\n        \"visual_translation\": bool(plan.visual_translation or (visual_until and visual_until > now) or has_paid_order),\n",
    "        credit_minutes = int(user.credit_minutes or 0)\n    return {\n        \"plan_code\": plan_code,\n        \"download_enabled\": bool(plan.download_enabled or credit_minutes > 0 or (download_until and download_until > now)),\n        \"visual_translation\": bool(plan.visual_translation or credit_minutes > 0 or (visual_until and visual_until > now)),\n",
)

# Revoke remaining credit or the active subscription after a completed refund.
replace(
    "lecturesift/commerce.py",
    "def update_refund_status(refund_id: str, status: str, provider_reference: str = \"\") -> dict[str, Any]:\n",
    "def revoke_refunded_purchase(purchase_reference: str) -> None:\n    init_commerce_database()\n    with ENGINE.begin() as connection:\n        purchase = connection.execute(select(PURCHASES).where(PURCHASES.c.reference == purchase_reference)).first()\n        if not purchase:\n            return\n        plan = PLAN_BY_CODE.get(purchase.plan_code)\n        if plan and plan.kind == \"one_time\":\n            user = connection.execute(select(USERS).where(USERS.c.id == purchase.user_id)).first()\n            remaining = max(0, int(user.credit_minutes or 0) - int(plan.minutes or 0)) if user else 0\n            connection.execute(update(USERS).where(USERS.c.id == purchase.user_id).values(credit_minutes=remaining))\n        else:\n            connection.execute(update(SUBSCRIPTIONS).where(SUBSCRIPTIONS.c.source_reference == purchase_reference, SUBSCRIPTIONS.c.status == \"active\").values(status=\"refunded\", ends_at=utcnow()))\n        connection.execute(update(PURCHASES).where(PURCHASES.c.reference == purchase_reference).values(status=\"refunded\", updated_at=utcnow()))\n\n\ndef update_refund_status(refund_id: str, status: str, provider_reference: str = \"\") -> dict[str, Any]:\n",
)
replace(
    "lecturesift/commerce_routes.py",
    "from .commerce import account_commerce_status, accept_payment_event, admin_refunds, cancel_account_deletion, create_purchase, mark_job_deleted, mark_purchase_failed, purchase_for_reference, refund_for_admin, request_account_deletion, request_refund, set_cancel_at_period_end, update_refund_status\n",
    "from .commerce import account_commerce_status, accept_payment_event, admin_refunds, cancel_account_deletion, create_purchase, mark_job_deleted, mark_purchase_failed, purchase_for_reference, refund_for_admin, request_account_deletion, request_refund, revoke_refunded_purchase, set_cancel_at_period_end, update_refund_status\n",
)
replace(
    "lecturesift/commerce_routes.py",
    "        result = update_refund_status(refund_id, \"refunded\", provider_reference=provider.get(\"reference_no\") or \"\")\n",
    "        result = update_refund_status(refund_id, \"refunded\", provider_reference=provider.get(\"reference_no\") or \"\")\n        revoke_refunded_purchase(purchase[\"reference\"])\n",
)

# PayTR calls the Turkish lira currency TL while the product catalog uses TRY.
replace(
    "lecturesift/paytr.py",
    '    selected_currency = str(currency or "TRY").upper()\n    if selected_currency not in {"TRY", "TL", "USD", "EUR", "GBP", "RUB"}:\n',
    '    selected_currency = str(currency or "TRY").upper()\n    if selected_currency == "TRY":\n        selected_currency = "TL"\n    if selected_currency not in {"TL", "USD", "EUR", "GBP", "RUB"}:\n',
)

# Account history always uses the billing-owned route after the final product
# integration adds it; this remains independent from transient worker state.
replace("frontend/account-commerce.js", '`${API}/jobs/${encodeURIComponent(jobId)}/download`', '`${API}/billing/jobs/${encodeURIComponent(jobId)}/download`')

# Full-width account cards.
replace("frontend/commerce.css", ".commerce-card.full{grid-column:1/-1}", ".commerce-card.full,.dashboard-card.full{grid-column:1/-1}")

# Legal identity is injected from runtime configuration and sales stays fail-closed.
for page in ("frontend/terms.html", "frontend/privacy.html", "frontend/refund.html", "frontend/cookies.html", "frontend/contact.html"):
    append_before_body(page, '<script src="/legal-config.js?v=1"></script>')

print("Final compatibility and lifecycle fixes applied.")
