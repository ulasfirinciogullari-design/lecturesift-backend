from pathlib import Path

from lecturesift.billing import SUPPORTED_CURRENCIES


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def test_global_country_and_currency_assets_are_loaded_where_needed():
    locale_data = (FRONTEND / "locale-data.js").read_text(encoding="utf-8")
    assert "AD AE AF" in locale_data and "ZA ZM ZW" in locale_data
    assert len(SUPPORTED_CURRENCIES) >= 20
    assert {"TRY", "USD", "EUR", "JPY", "KRW", "INR", "BRL", "AED"} <= set(
        SUPPORTED_CURRENCIES
    )

    for page in ("index.html", "register.html", "account.html", "plans.html"):
        assert "locale-data.js" in (FRONTEND / page).read_text(encoding="utf-8")


def test_plan_page_preserves_learning_entitlements_and_zero_decimal_prices():
    script = (FRONTEND / "plans.js").read_text(encoding="utf-8")
    assert 'new Set(["JPY", "KRW"])' in script
    assert "entitlements.minutes ?? plan.minutes" in script
    assert "quiz_questions: 30" in script
    assert "flashcards: 60" in script
    assert "summary_profiles: ALL_SUMMARIES" in script


def test_owner_only_netlify_toolbar_is_hidden_on_every_site_layout():
    for stylesheet in ("styles.css", "auth.css", "legal.css"):
        content = (FRONTEND / stylesheet).read_text(encoding="utf-8")
        assert "iframe#nl-hud-frame{display:none!important}" in content


def test_required_product_and_legal_pages_exist():
    for page in (
        "features.html",
        "plans.html",
        "about.html",
        "contact.html",
        "privacy.html",
        "terms.html",
        "cookies.html",
        "refund.html",
        "register.html",
        "login.html",
        "forgot-password.html",
        "reset-password.html",
        "verify.html",
        "account.html",
    ):
        assert (FRONTEND / page).is_file(), page
