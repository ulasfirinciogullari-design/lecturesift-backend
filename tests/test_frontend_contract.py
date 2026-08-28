import json
import re
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
    assert 'entitlements.download_enabled === false' in script
    assert 'plans.previewOnly' in script
    assert "quiz_questions: 30" in script
    assert "flashcards: 60" in script
    assert "summary_profiles: ALL_SUMMARIES" in script
    assert "rewarded_minutes_eligible" in script
    assert 'pt("plans.adExperience", "Reklam deneyimi")' in script
    assert "accountAdMode" in (FRONTEND / "account.html").read_text(encoding="utf-8")


def test_owner_only_netlify_toolbar_is_hidden_on_every_site_layout():
    for stylesheet in ("styles.css", "auth.css", "legal.css"):
        content = (FRONTEND / stylesheet).read_text(encoding="utf-8")
        assert "iframe#nl-hud-frame" in content
        assert 'iframe[src^="https://app.netlify.com/cdp/"]{display:none!important}' in content
    script = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    assert 'location.hostname.endsWith(".netlify.app")' in script
    assert "removeNetlifyPreviewFrame" in script
    assert "new MutationObserver" in script


def test_account_identity_and_localized_minutes_do_not_overflow_rtl_mobile():
    style = (FRONTEND / "auth.css").read_text(encoding="utf-8")
    auth = (FRONTEND / "auth.js").read_text(encoding="utf-8")
    admin = (FRONTEND / "admin.js").read_text(encoding="utf-8")
    assert ".account-hero>div{min-width:0;max-width:100%}" in style
    assert "overflow-wrap:anywhere" in style
    assert 't("unit.minuteShort", "dk")' in auth
    assert 'adminT("unit.minuteShort", "dk")' in admin


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
        "admin.html",
        "thanks.html",
    ):
        assert (FRONTEND / page).is_file(), page


def test_every_page_loads_global_language_switcher_and_all_locales_exist():
    for page in FRONTEND.glob("*.html"):
        content = page.read_text(encoding="utf-8")
        assert "page-i18n.js" in content, page.name
        assert "i18n.js" in content, page.name
    script = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    assert '"tr","en","de","fr","es","it","pt","ru","ar","zh","ja","ko","hi"' in script
    assert "language-switcher" in script
    assert 'document.documentElement.dir = selected === "ar" ? "rtl" : "ltr"' in script


def test_static_page_copy_covers_every_language_without_empty_entries():
    script = (FRONTEND / "page-i18n.js").read_text(encoding="utf-8")
    payload = script.split("window.LECTURESIFT_PAGE_COPY=", 1)[1].rstrip(";\n")
    catalog = json.loads(payload)
    assert len(catalog) >= 600
    assert all(len(translations) == 13 for translations in catalog.values())
    assert all(all(str(value).strip() for value in translations) for translations in catalog.values())
    assert all("LSSEP" not in str(translations) for translations in catalog.values())
    forbidden_brand_translations = (
        "VortragSift",
        "ConférenceSift",
        "ConferenciaSift",
        "LezioneSift",
        "PalestraSift",
        "ЛекцияSift",
        "लेक्चरसिफ्ट",
    )
    assert not any(
        variant in str(value)
        for translations in catalog.values()
        for value in translations
        for variant in forbidden_brand_translations
    )

    rows = list(catalog.values())
    for language_index in range(1, 13):
        values = [translations[language_index] for translations in rows]
        for offset in range(1, len(values)):
            run = 0
            for index in range(offset, len(values)):
                run = run + 1 if values[index] == values[index - offset] else 0
                assert run < 3, f"repeated translation block at language {language_index}, row {index}"
    for source in (
        "Hakları karşılaştır",
        "Profil bilgileri",
        "Sipariş no",
        "Hesap sahibi",
        "LectureSift yönetici paneli",
        "KVKK aydınlatma ve gizlilik metni",
    ):
        assert source in catalog

    central_rows = {
        values[0]: values
        for values in (
            json.loads(f"[{payload}]")
            for payload in re.findall(r'^\s*"[^"]+":\[(.*?)\],?$', (FRONTEND / "i18n.js").read_text(encoding="utf-8"), re.MULTILINE)
        )
    }
    corrected_sources = (
        "Yanlış",
        "Cevabı göster",
        "Biliyorum",
        "Tekrar et",
        "Planın, dakikaların ve ödemelerin bu hesaba bağlanır.",
        "Dakika, quiz, bilgi kartı, özet ve dosya haklarını açıkça karşılaştır. Ülke ve para birimine göre yerelleştirilmiş fiyatı seç.",
        "Planların bölgesel liste fiyatını seçtiğin para biriminde göstermek.",
        "Video bağlantıdan alınıyor",
    )
    assert set(corrected_sources) <= central_rows.keys()
    bad_fragments = ("&#", "[ترجمة", "actas", "atas", "회의록", "MOSTRAR")
    bad_exact = {"Ripet", "Повторыть", "재교부"}
    assert not any(fragment in value for source in corrected_sources for value in central_rows[source] for fragment in bad_fragments)
    assert not any(value in bad_exact for source in corrected_sources for value in central_rows[source])


def test_every_declared_interface_key_has_thirteen_translations():
    html = "\n".join(page.read_text(encoding="utf-8") for page in FRONTEND.glob("*.html"))
    required = set(re.findall(r'data-i18n(?:-placeholder)?="([^"]+)"', html))
    script = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    rows = {
        key: json.loads(f"[{payload}]")
        for key, payload in re.findall(r'^\s*"([^"]+)":\[(.*?)\],?$', script, re.MULTILINE)
    }
    central = required & rows.keys()
    legacy = required - rows.keys()
    workspace_script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    uncovered = sorted(
        key for key in legacy if not re.search(rf"\b{re.escape(key)}\s*:", workspace_script)
    )
    assert not uncovered, uncovered
    assert all(len(rows[key]) == 13 for key in central)
    assert all(all(str(value).strip() for value in rows[key]) for key in central)


def test_dynamic_plan_descriptions_are_localized_and_account_uses_plan_label():
    script = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    plans = (FRONTEND / "plans.js").read_text(encoding="utf-8")
    for code in ("free", "credit", "lite", "plus", "pro", "max", "business"):
        payload = re.search(rf'^\s*"plan\.{code}\.description":\[(.*?)\],?$', script, re.MULTILINE)
        assert payload, code
        values = json.loads(f"[{payload.group(1)}]")
        assert len(values) == 13
        assert len(set(values)) >= 10
    assert '$("accountPlan").textContent = planLabel(account.plan.code);' in plans


def test_profile_admin_bank_and_full_comparison_interfaces_are_present():
    account = (FRONTEND / "account.html").read_text(encoding="utf-8")
    auth = (FRONTEND / "auth.js").read_text(encoding="utf-8")
    plans = (FRONTEND / "plans.html").read_text(encoding="utf-8")
    plan_script = (FRONTEND / "plans.js").read_text(encoding="utf-8")
    admin = (FRONTEND / "admin.html").read_text(encoding="utf-8")
    workspace_script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert all(
        value in account
        for value in (
            "profileForm",
            "passwordForm",
            "accountBankHolder",
            "exportDataButton",
            "closeAccountForm",
        )
    )
    assert "/billing/me/profile" in auth and "/billing/me/change-password" in auth
    assert "/billing/me/subscription/cancel" in auth
    assert "/jobs?limit=30" in auth and "jobHistory" in account
    assert "/billing/me/export" in auth and "/billing/me/close-account" in auth
    assert 'href="/admin.html"' not in account
    assert all(value in plans for value in ("compareHead", "compareBody", "publicBankHolder"))
    assert "renderCompare" in plan_script and "/billing/manual-transfer" in plan_script
    assert "adminTokenForm" in admin and "adminOrders" in admin
    assert "adminReadiness" in admin
    assert "restoreRequestedJob" in workspace_script
    assert 'new URLSearchParams(location.search).get("job")' in workspace_script
    assert "/ask" in workspace_script and "lessonQuestionForm" in workspace_script
    assert "renderExamPrep" in workspace_script and "startExamButton" in workspace_script


def test_secure_card_checkout_is_prepared_without_collecting_card_details():
    plans = (FRONTEND / "plans.html").read_text(encoding="utf-8")
    script = (FRONTEND / "plans.js").read_text(encoding="utf-8")
    rollout = (FRONTEND / "rollout.js").read_text(encoding="utf-8")
    headers = (FRONTEND.parent / "netlify.toml").read_text(encoding="utf-8")
    assert all(value in plans for value in ("checkoutForm", "checkoutAddress", "paytrFrame"))
    assert "/billing/checkout" in script and "body.checkout_url" in script
    assert "card_number" not in plans.lower() and "cvv" not in plans.lower()
    assert "frame-src https://www.paytr.com" in headers
    plans_hook = rollout.split("async function installPlansPage()", 1)[1].split(
        "function accountToken()", 1
    )[0]
    assert "data-rollout-plan" not in plans_hook
    assert "renderPlans =" not in plans_hook
    assert all(value in plans for value in ("checkoutTerms", "checkoutEarlyPerformance"))
    assert all(value in plans for value in ("checkoutSummaryPlan", "checkoutSummaryInterval", "checkoutSummaryTotal"))
    assert "/distance-sales.html" in plans
    assert "/assets/payments/iyzico-ile-ode-white.png" in plans
    assert "/assets/payments/iyzico-card-brands-white.png" in plans
    assert (FRONTEND / "assets" / "payments" / "iyzico-ile-ode-white.png").stat().st_size > 20_000
    assert (FRONTEND / "assets" / "payments" / "iyzico-card-brands-white.png").stat().st_size > 10_000
    assert '["application_review", "fallback"].includes(iyzico.status)' in script
    assert "terms_accepted" in script and "early_performance_requested" in script
    assert "reportValidity()" in script


def test_distance_sales_contract_covers_digital_service_checkout_requirements():
    contract = (FRONTEND / "distance-sales.html").read_text(encoding="utf-8")
    operator = (FRONTEND / "legal-operator.js").read_text(encoding="utf-8")
    assert "Mesafeli Satış Sözleşmesi" in contract
    assert "MSS-2026-08-28-v1" in contract
    for topic in (
        "Taraflar",
        "Ön bilgilendirme",
        "Fiyat, vergiler ve ödeme",
        "İfa, teslim ve kullanım",
        "Cayma hakkı ve istisnalar",
        "İptal, ayıplı hizmet ve iade",
        "Kişisel veriler ve işlem güvenliği",
        "Başvuru, uyuşmazlık ve yetki",
    ):
        assert topic in contract
    assert "/billing/operator" in operator
    assert 'a[href*="distance-sales"]' in operator


def test_delivery_and_refund_terms_are_explicit_and_localized():
    refund = (FRONTEND / "refund.html").read_text(encoding="utf-8")
    catalog = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    assert "Teslimat ve İade Şartları" in refund
    assert "Dijital teslimat ve hizmet başlangıcı" in refund
    assert "fiziksel teslimat yapılmaz" in refund
    for key in (
        "refund.pageTitle",
        "refund.navTitle",
        "refund.heroTitle",
        "refund.heroLead",
        "refund.deliveryTitle",
        "refund.deliveryText",
        "legal.taxOffice",
    ):
        assert f'"{key}"' in catalog


def test_legal_operator_identity_is_public_only_after_configuration():
    i18n = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    operator = (FRONTEND / "legal-operator.js").read_text(encoding="utf-8")
    blueprint = (FRONTEND.parent / "render.yaml").read_text(encoding="utf-8")
    assert 'legalOperatorScript.src = "/legal-operator.js?v=1"' in i18n
    assert "/billing/operator" in operator
    assert "if (!operator?.configured) return" in operator
    assert all(
        key in blueprint
        for key in (
            "LEGAL_OPERATOR_NAME",
            "LEGAL_OPERATOR_ADDRESS",
            "LEGAL_OPERATOR_COUNTRY",
            "LEGAL_OPERATOR_PHONE",
            "LEGAL_OPERATOR_EMAIL",
            "LEGAL_TAX_OFFICE",
            "LEGAL_MERSIS_ID",
            "LEGAL_TRADE_REGISTRY",
            "LEGAL_KEP_ADDRESS",
            "LEGAL_CHAMBER_NAME",
        )
    )
    assert 'operator.tax_office' in operator


def test_runtime_translation_keys_exist_for_every_dynamic_message():
    runtime = (FRONTEND / "rollout.js").read_text(encoding="utf-8")
    catalog = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    used_keys = set(re.findall(r'rt\(["\']([^"\']+)["\']', runtime))
    catalog_keys = set(re.findall(r'^\s*,?["\']([^"\']+)["\']\s*:\s*\[', catalog, re.MULTILINE))
    assert used_keys
    assert used_keys <= catalog_keys

    translation_rows = re.findall(r'^\s*,?["\'][^"\']+["\']\s*:\s*(\[[^\n]+\])', catalog, re.MULTILINE)
    assert translation_rows
    assert all(len(json.loads(row)) == 13 for row in translation_rows)
    assert all(all(str(value).strip() for value in json.loads(row)) for row in translation_rows)


def test_account_plans_admin_and_legal_dynamic_copy_is_fully_localized():
    catalog_script = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    catalog_keys = set(re.findall(r'^\s*,?["\']([^"\']+)["\']\s*:\s*\[', catalog_script, re.MULTILINE))
    scripts = {
        "auth.js": r'\bt\(["\']([^"\']+)["\']',
        "plans.js": r'\bpt\(["\']([^"\']+)["\']',
        "admin.js": r'\badminT\(["\']([^"\']+)["\']',
        "legal-operator.js": r'\bt\(["\']([^"\']+)["\']',
    }
    for filename, pattern in scripts.items():
        used = set(re.findall(pattern, (FRONTEND / filename).read_text(encoding="utf-8")))
        assert used <= catalog_keys, (filename, sorted(used - catalog_keys))


def test_private_pages_are_excluded_from_search_indexing():
    private_pages = (
        "login.html",
        "register.html",
        "account.html",
        "forgot-password.html",
        "verify.html",
        "reset-password.html",
        "admin.html",
    )
    for page in private_pages:
        content = (FRONTEND / page).read_text(encoding="utf-8")
        assert '<meta name="robots" content="noindex,nofollow">' in content, page

    sitemap = (FRONTEND / "sitemap.xml").read_text(encoding="utf-8")
    robots = (FRONTEND / "robots.txt").read_text(encoding="utf-8")
    for page in private_pages:
        assert page not in sitemap
        assert f"Disallow: /{page}" in robots

    for page in ("features.html", "plans.html", "about.html", "contact.html"):
        assert page in sitemap


def test_public_pages_have_share_metadata_canonical_urls_and_structured_data():
    i18n = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    seo = (FRONTEND / "seo.js").read_text(encoding="utf-8")
    sitemap = (FRONTEND / "sitemap.xml").read_text(encoding="utf-8")

    assert 'seoScript.src = "/seo.js?v=1"' in i18n
    assert 'link[rel="canonical"]' in seo
    assert 'meta[property="og:image"]' in seo
    assert 'meta[name="twitter:card"]' in seo
    assert '"@type": "SoftwareApplication"' in seo
    assert '"@type": "WebPage"' in seo
    assert '"@type": "BreadcrumbList"' in seo
    assert '"@type": "FAQPage"' in seo
    assert 'meta[property="og:locale:alternate"]' in seo
    assert 'applicationCategory: "EducationalApplication"' in seo
    assert 'price: "0"' in seo
    assert "noindex,nofollow,noarchive" in seo
    assert "max-image-preview:large" in seo
    assert 'url: `${PRODUCTION_ORIGIN}/`' in seo
    assert (FRONTEND / "og-image.png").stat().st_size > 100_000
    assert sitemap.count("<lastmod>2026-08-28</lastmod>") == 10 * 13


def test_optional_analytics_and_advertising_are_consent_gated():
    i18n = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    consent = (FRONTEND / "consent.js").read_text(encoding="utf-8")
    cookies = (FRONTEND / "cookies.html").read_text(encoding="utf-8")

    assert 'consentScript.src = "/consent.js?v=1"' in i18n
    assert 'consentStyle.href = "/consent.css?v=1"' in i18n
    assert 'const STORAGE_KEY = "lecturesift-consent-v1"' in consent
    assert 'analytics: false, advertising: false' in consent
    assert 'category === "necessary"' in consent
    assert "lecturesift-consent-v1" in cookies
    assert "consent.storageContent" in cookies
    rewarded = (FRONTEND / "rewarded-ads.js").read_text(encoding="utf-8")
    rollout = (FRONTEND / "rollout.js").read_text(encoding="utf-8")
    assert 'LectureSiftConsent?.allows("advertising")' in rewarded
    assert "securepubads.g.doubleclick.net/tag/js/gpt.js" in rewarded
    assert 'OutOfPageFormat.REWARDED' in rewarded
    assert 'event.makeRewardedVisible()' in rewarded
    assert "/billing/rewarded-ads/session" in rollout
    assert "/billing/rewarded-ads/claim" in rollout
    assert "cookies.adPolicy" in cookies
    assert 'basePath === "/privacy.html"' in i18n
    assert 'basePath === "/terms.html"' in i18n
    assert 't("privacy.adsDisclosure")' in i18n
    assert 't("terms.rewardPolicy")' in i18n


def test_banner_ads_are_opt_in_public_only_and_paid_plans_are_ad_free():
    i18n = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    display = (FRONTEND / "display-ads.js").read_text(encoding="utf-8")
    blueprint = (FRONTEND.parent / "render.yaml").read_text(encoding="utf-8")

    assert 'displayAdsScript.src = "/display-ads.js?v=1"' in i18n
    assert 'displayAdsStyle.href = "/display-ads.css?v=1"' in i18n
    assert 'LectureSiftConsent?.allows("advertising")' in display
    assert 'body.account?.plan?.entitlements?.ad_free === true' in display
    assert 'const PUBLIC_AD_PATHS = new Set([' in display
    allowed_paths = display.split("const PUBLIC_AD_PATHS", 1)[1].split("]);", 1)[0]
    assert '"/account.html"' not in allowed_paths
    assert 'json("/ads/config")' in display
    assert "securepubads.g.doubleclick.net/tag/js/gpt.js" in display
    assert "event.isEmpty) container.remove()" in display
    assert "LECTURESIFT_DISPLAY_ADS_ENABLED" in blueprint
    assert 'value: "false"' in blueprint.split("LECTURESIFT_DISPLAY_ADS_ENABLED", 1)[1][:80]
    assert (FRONTEND / "display-ads.css").is_file()
    assert (FRONTEND.parent / "SEO_AND_ADS_READINESS.md").is_file()


def test_free_results_show_a_localized_download_paywall():
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    catalog = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    assert "data.download_enabled !== false" in app
    assert 't("plans.unlockDownload"' in app
    assert '"plans.unlockDownload"' in catalog


def test_transcript_timeline_is_rendered_with_localized_precision_status():
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    catalog = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    assert "data.transcript_timestamps_mode" in app
    assert "latestResult.transcript_segments" in app
    assert '"transcript.preciseTimestamps"' in catalog
    assert '"transcript.estimatedTimestamps"' in catalog


def test_contact_form_has_a_branded_noindex_success_page():
    i18n = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    thanks = (FRONTEND / "thanks.html").read_text(encoding="utf-8")
    robots = (FRONTEND / "robots.txt").read_text(encoding="utf-8")
    assert 'localizedPath(selected, "/thanks.html")' in i18n
    assert '<meta name="robots" content="noindex,nofollow">' in thanks
    assert 'data-i18n="thanks.title"' in thanks
    assert "Disallow: /thanks.html" in robots


def test_every_supported_language_has_a_stable_indexable_url():
    i18n = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    seo = (FRONTEND / "seo.js").read_text(encoding="utf-8")
    redirects = (FRONTEND / "_redirects").read_text(encoding="utf-8")
    sitemap = (FRONTEND / "sitemap.xml").read_text(encoding="utf-8")
    languages = ("tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi")

    assert "if (pathLanguage) return pathLanguage" in i18n
    assert "localizedPath(picker.value)" in i18n
    assert 'link[rel="alternate"][hreflang=' in seo
    assert 'hreflang: "x-default"' in seo
    for language in languages[1:]:
        assert f"/{language}/*" in redirects
        assert f"<loc>https://lecturesift.com/{language}/</loc>" in sitemap
        assert f'hreflang="{language}"' in sitemap
    assert sitemap.count("<url>") == 10 * 13

    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    auth = (FRONTEND / "auth.js").read_text(encoding="utf-8")
    assert "window.LectureSiftI18n?.language" in app
    assert "localizedPath?.(currentLanguage)" in app
    assert "I18N.localizedPath(body.account.user.preferred_language)" in auth
