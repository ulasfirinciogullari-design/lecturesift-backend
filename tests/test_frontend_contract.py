import json
import re
import subprocess
import sys
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


def test_homepage_promotes_campaigns_without_duplicating_plan_checkout():
    homepage = (FRONTEND / "index.html").read_text(encoding="utf-8")
    workspace = (FRONTEND / "workspace.html").read_text(encoding="utf-8")
    assert 'id="campaigns"' in homepage
    assert "Hesapsız 5 dakika deneme" in homepage
    assert 'id="plansGrid"' not in homepage
    assert 'id="workspace"' not in homepage
    assert 'id="workspace"' in workspace
    assert 'data-page="workspace"' in workspace
    assert 'id="transferPanel"' not in homepage
    assert 'href="/plans.html"' in homepage


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
        "workspace.html",
        "document-summary.html",
        "lecture-video-summary.html",
        "quiz-flashcards.html",
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


def test_public_navigation_is_consistent_localized_and_session_aware():
    public_pages = (
        "index.html",
        "workspace.html",
        "features.html",
        "document-summary.html",
        "lecture-video-summary.html",
        "quiz-flashcards.html",
        "plans.html",
        "about.html",
        "contact.html",
        "privacy.html",
        "terms.html",
        "cookies.html",
        "refund.html",
        "distance-sales.html",
    )
    for page in public_pages:
        content = (FRONTEND / page).read_text(encoding="utf-8")
        assert "/site-shell.js?v=4" in content, page
        assert "/rollout.css?v=8" in content, page

    shell = (FRONTEND / "site-shell.js").read_text(encoding="utf-8")
    assert 'const TOKEN_KEY = "lecturesift-billing-token"' in shell
    assert 'fetch(`${API}/billing/me`' in shell
    assert 'localStorage.removeItem(TOKEN_KEY)' in shell
    assert 'response.status === 401 || response.status === 403' in shell
    assert 'i18n.localizedPath(language, path)' in shell
    assert 'account.href = pathFor(signedIn ? "/account.html" : "/login.html")' in shell
    assert 'anchor.setAttribute("aria-current", "page")' in shell
    assert 'https://www.instagram.com/lecturesift/' in shell
    for key in ("menu", "home", "workspace", "features", "plans", "about", "login", "account", "instagram"):
        row = re.search(rf'^\s*{key}: \[(.*?)\],?$', shell, re.MULTILINE)
        assert row, key
        assert len(json.loads(f"[{row.group(1)}]")) == 13

    rollout = (FRONTEND / "rollout.css").read_text(encoding="utf-8")
    assert ".public-header-tools" in rollout
    assert ".public-nav-link.active" in rollout
    assert '@media(max-width:860px)' in rollout
    assert 'grid-template-areas:"theme language" "navigation navigation"' in rollout
    assert "rebuildInformationNavigation" in shell
    assert 'corporatePages = ["/about.html", "/contact.html"]' in shell
    assert '"Yasal belgeler"' in shell
    assert '["Gizlilik ve KVKK", "/privacy.html"]' in shell
    assert '["Mesafeli Satış Sözleşmesi", "/distance-sales.html"]' in shell
    assert 'script.src = "/legal-operator.js?v=1"' not in shell


def test_processing_center_uses_operation_specific_progress_profiles():
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "const PROGRESS_PROFILES" in script
    assert 'document: [["source", "stageSource"], ["document_extraction", "stageDocument"]' in script
    assert 'document_ocr: "stageDocument"' in script
    assert 'audio_export: [["source", "stageSource"], ["audio_extract", "stageMp3"]' in script
    assert 'download_video: [["source", "stageSource"], ["worker_download", "url_download"]' in script
    assert "function configureProgressProfile" in script
    assert "function profileDetail" in script
    assert "uploadingSource" in script


def test_every_page_supports_persistent_light_and_dark_themes():
    for page in FRONTEND.glob("*.html"):
        content = page.read_text(encoding="utf-8")
        assert "/theme.css?v=11" in content, page.name
        assert "/theme.js?v=2" in content, page.name
        assert "i18n.js?v=23" in content, page.name
        assert "page-i18n.js?v=6" in content, page.name

    script = (FRONTEND / "theme.js").read_text(encoding="utf-8")
    style = (FRONTEND / "theme.css").read_text(encoding="utf-8")
    cookies = (FRONTEND / "cookies.html").read_text(encoding="utf-8")
    assert 'const STORAGE_KEY = "lecturesift-theme"' in script
    assert 'window.matchMedia("(prefers-color-scheme: light)")' in script
    assert 'root.dataset.theme = theme' in script
    assert 'localStorage.setItem(STORAGE_KEY, nextTheme)' in script
    assert 'className = "theme-toggle"' in script
    assert 'className = "skip-link"' in script
    assert '"theme.skipToContent"' in script
    assert 'html[data-theme="light"]' in style
    assert ".skip-link:focus" in style
    assert ":focus-visible" in style
    assert '@media(prefers-reduced-motion:reduce)' in style
    for surface in (".feature-band", ".campaign-section", ".format-choices", ".account-section-nav"):
        assert surface in style
    workspace = (FRONTEND / "workspace.html").read_text(encoding="utf-8")
    assert 'id="formatChoices" class="format-choices" role="group"' in workspace
    assert '<fieldset id="formatChoices"' not in workspace
    assert "lecturesift-theme" in cookies
    assert all(f'data-i18n="theme.{key}"' in cookies for key in ("storageContent", "storagePurpose", "storageControl"))


def test_keyboard_navigation_and_admin_filters_have_accessible_names():
    index = (FRONTEND / "workspace.html").read_text(encoding="utf-8")
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    admin = (FRONTEND / "admin.html").read_text(encoding="utf-8")
    assert 'role="tablist" aria-label="Ders paketi bölümleri"' in index
    assert index.count('role="tab" aria-selected=') >= 9
    assert index.count('role="tabpanel" aria-labelledby="resultTab-') >= 9
    assert 'data-i18n-aria-label="ask.placeholder"' in index
    assert "function activateResultPane" in app
    assert "function setupResultTabs" in app
    assert all(key in app for key in ("ArrowRight", "ArrowLeft", "Home", "End"))
    for control_id in (
        "adminTimelineFilter", "adminUserSearch", "adminUserStatus", "adminUserPlan",
        "adminUserSort", "adminUserPageSize", "adminBulkAction", "adminBulkMinutes",
        "adminBulkPlan", "adminBulkReason", "adminBulkConfirmation", "adminOrderSearch",
        "adminOrderStatus", "adminOrderProvider", "adminOrderPageSize", "adminMessageSearch",
        "adminMessageStatus", "adminJobStatus",
    ):
        tag = re.search(rf'<(?:input|select)\b[^>]*id="{control_id}"[^>]*>', admin)
        assert tag and 'aria-label="' in tag.group(0), control_id


def test_static_page_copy_covers_every_language_without_empty_entries():
    script = (FRONTEND / "page-i18n.js").read_text(encoding="utf-8")
    payload = script.split("window.LECTURESIFT_PAGE_COPY=", 1)[1].rstrip(";\n")
    catalog = json.loads(payload)
    assert len(catalog) >= 600
    assert all(len(translations) == 13 for translations in catalog.values())
    assert all(all(str(value).strip() for value in translations) for translations in catalog.values())
    assert all("LSSEP" not in str(translations) for translations in catalog.values())
    assert "&#10;" not in script
    assert "[ترجمة المصطلح:" not in script
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
    for source, translations in catalog.items():
        if "LectureSift" in source:
            assert all(
                value.count("LectureSift") >= source.count("LectureSift")
                for value in translations
            ), source

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

    coverage = subprocess.run(
        [sys.executable, str(FRONTEND.parent / "scripts" / "sync_static_i18n.py"), "--check"],
        cwd=FRONTEND.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert coverage.returncode == 0, coverage.stdout + coverage.stderr

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


def test_document_errors_are_localized_for_every_supported_language():
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    script = (FRONTEND / "page-i18n.js").read_text(encoding="utf-8")
    payload = script.split("window.LECTURESIFT_PAGE_COPY=", 1)[1].rstrip(";\n")
    catalog = json.loads(payload)
    for code in (
        "LS-UPLOAD-05",
        *(f"LS-DOC-{index:02d}" for index in range(1, 14)),
        *(f"LS-OCR-{index:02d}" for index in range(1, 5)),
    ):
        assert app.count(f'"{code}"') >= 2
    source = "OCR tamamlandı ancak okunabilir metin bulunamadı. Daha net bir tarama veya doğru kaynak diliyle yeniden dene."
    assert len(catalog[source]) == 13


def test_all_ai_processing_errors_have_thirteen_interface_translations():
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    script = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    for index in range(3, 9):
        code = f"LS-AI-{index:02d}"
        key = f"error.ai{index:02d}"
        assert f'"{code}":"{key}"' in app
        payload = re.search(rf'^\s*"{re.escape(key)}":\[(.*?)\],?$', script, re.MULTILINE)
        assert payload, key
        assert len(json.loads(f"[{payload.group(1)}]")) == 13


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
    for code in ("free", "test", "credit", "lite", "plus", "pro", "max", "business"):
        payload = re.search(rf'^\s*"plan\.{code}\.description":\[(.*?)\],?$', script, re.MULTILINE)
        assert payload, code
        values = json.loads(f"[{payload.group(1)}]")
        assert len(values) == 13
        assert len(set(values)) >= 10
    assert '$("accountPlan").textContent = planLabel(account.plan.code);' in plans


def test_profile_admin_automatic_payment_and_full_comparison_interfaces_are_present():
    account = (FRONTEND / "account.html").read_text(encoding="utf-8")
    auth = (FRONTEND / "auth.js").read_text(encoding="utf-8")
    plans = (FRONTEND / "plans.html").read_text(encoding="utf-8")
    plan_script = (FRONTEND / "plans.js").read_text(encoding="utf-8")
    admin = (FRONTEND / "admin.html").read_text(encoding="utf-8")
    admin_script = (FRONTEND / "admin.js").read_text(encoding="utf-8")
    workspace_script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    rollout_script = (FRONTEND / "rollout.js").read_text(encoding="utf-8")
    assert all(
        value in account
        for value in (
            "profileForm",
            "passwordForm",
            "ordersList",
            "exportDataButton",
            "closeAccountForm",
        )
    )
    assert "/billing/me/profile" in auth and "/billing/me/change-password" in auth
    assert "/billing/me/subscription/cancel" in auth
    assert "/jobs?limit=30" in auth and "jobHistory" in account
    assert "/billing/me/export" in auth and "/billing/me/close-account" in auth
    assert 'href="/admin.html"' not in account
    assert all(value in plans for value in (
        "compareHead",
        "compareBody",
        "bankAvailability",
        "protectedBankAvailability",
        "checkoutBankButton",
        "checkoutProtectedBankButton",
    ))
    assert all(value in plans for value in ("transferPanel", "transferReference", "transferAmount", "transferIban", "transferHolder", "transferBank"))
    assert "renderCompare" in plan_script and "createTransfer" in plan_script
    assert "/billing/checkout" in plan_script
    assert "/billing/manual-transfer/orders" in plan_script
    assert "manual_bank_transfer" in plan_script
    assert all(value in plans for value in ("bankTransferGuide", "bankTransferContinue", "bankTransferBack"))
    assert "showBankTransferGuide" in plan_script
    assert 'startHostedCheckout("bank_transfer")' in plan_script
    assert "/billing/manual-transfer" not in auth
    assert "/billing/manual-transfer" not in workspace_script
    assert "/billing/manual-transfer" not in rollout_script
    assert 'code !== "test" || currency === "TRY"' in plan_script
    assert "adminTokenForm" in admin and "adminOrders" in admin
    assert "ADMIN_ADMIN" in admin
    assert "adminReadiness" in admin
    assert "adminContactMessages" in admin
    assert all(value in admin for value in ("adminTimeline", "adminJobs", "adminAlerts", "adminExportOrders", "adminOperationNotice", "adminAccountEvents", "adminPlanDistribution"))
    assert all(
        f'data-admin-view="{view}"' in admin
        and f'data-admin-view-button="{view}"' in admin
        for view in ("overview", "users", "finance", "support", "jobs", "system", "audit")
    )
    assert 'role="tablist"' in admin and 'aria-selected="true"' in admin
    assert "/billing/admin/jobs" in admin_script
    assert all(
        value in admin_script
        for value in (
            "data-user-profile-form",
            "data-user-credit-form",
            "data-user-subscription-form",
            "data-user-revoke",
            "data-user-close-form",
            "/billing/admin/account-events",
            "renderPlanDistribution",
            "google_ads_conversion_configured",
            "ADMIN_SESSION_TOKEN_KEY",
            "sessionStorage.setItem",
            "user.is_protected",
            "Korunan hesap",
            "ADMIN_VIEW_KEY",
            "activateAdminView",
            "setupAdminNavigation",
        )
    )
    assert "adminReadinessChecks" in admin_script and "Opsiyonel · kapalı" in admin_script
    assert "restoreRequestedJob" in workspace_script
    assert 'new URLSearchParams(location.search).get("job")' in workspace_script
    assert "/ask" in workspace_script and "lessonQuestionForm" in workspace_script
    assert "renderExamPrep" in workspace_script and "startExamButton" in workspace_script


def test_admin_large_dataset_controls_and_compact_account_tabs_are_wired():
    admin = (FRONTEND / "admin.html").read_text(encoding="utf-8")
    admin_script = (FRONTEND / "admin.js").read_text(encoding="utf-8")
    account = (FRONTEND / "account.html").read_text(encoding="utf-8")
    auth = (FRONTEND / "auth.js").read_text(encoding="utf-8")
    rollout = (FRONTEND.parent / "lecturesift" / "rollout_routes.py").read_text(encoding="utf-8")
    assert all(
        value in admin
        for value in (
            "adminUsersPagination",
            "adminOrdersPagination",
            "adminBulkToolbar",
            "adminBulkConfirmation",
            "adminUserDialog",
            'data-admin-view="growth"',
        )
    )
    assert all(
        value in admin_script
        for value in (
            "/billing/admin/users?",
            "/billing/admin/orders?",
            "/billing/admin/users/bulk-action",
            "applyAdminBulkAction",
            "confirmation_word",
            "payment_method",
            "ip_network",
        )
    )
    assert all(
        f'data-account-view="{view}"' in account
        and f'data-account-view-button="{view}"' in account
        for view in ("overview", "profile", "payments", "lessons", "security")
    )
    assert "activateAccountView" in auth and "lecturesift-account-view" in auth
    assert '@router.get("/billing/admin/users")' in rollout
    assert '@router.get("/billing/admin/orders")' in rollout
    assert '@router.post("/billing/admin/users/bulk-action")' in rollout


def test_checkout_names_contact_inbox_and_mobile_plan_navigation_are_wired():
    plans_html = (FRONTEND / "plans.html").read_text(encoding="utf-8")
    plans_js = (FRONTEND / "plans.js").read_text(encoding="utf-8")
    contact_html = (FRONTEND / "contact.html").read_text(encoding="utf-8")
    contact_js = (FRONTEND / "contact.js").read_text(encoding="utf-8")
    admin_js = (FRONTEND / "admin.js").read_text(encoding="utf-8")
    rollout_css = (FRONTEND / "rollout.css").read_text(encoding="utf-8")
    assert "checkoutFirstName" in plans_html and "checkoutLastName" in plans_html
    assert 'first_name: $("checkoutFirstName")' in plans_js
    assert 'last_name: $("checkoutLastName")' in plans_js
    assert "contactForm" in contact_html and "/contact/messages" in contact_js
    assert "/billing/admin/contact-messages" in admin_js
    assert ".topbar .top-actions .top-link" in rollout_css
    assert all(
        value in rollout_css
        for value in (
            ".admin-user-grid",
            ".admin-user-card",
            ".admin-user-tools",
            ".admin-form-grid",
            ".admin-workspace",
            ".admin-view[hidden]",
            ".admin-section-nav button[aria-selected=\"true\"]",
            "@media(max-width:620px)",
        )
    )


def test_secure_card_checkout_is_prepared_without_collecting_card_details():
    plans = (FRONTEND / "plans.html").read_text(encoding="utf-8")
    script = (FRONTEND / "plans.js").read_text(encoding="utf-8")
    rollout = (FRONTEND / "rollout.js").read_text(encoding="utf-8")
    headers = (FRONTEND.parent / "netlify.toml").read_text(encoding="utf-8")
    assert all(value in plans for value in ("checkoutForm", "checkoutAddress", "checkoutCity", "checkoutZipCode", "paytrFrame"))
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
    assert "cardProviderStatus" in script and 'body.display_mode === "redirect"' in script
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
    assert "Ödeme, satıcı/sağlayıcının zorunlu kimlik" not in contract


def test_privacy_page_uses_current_controller_identity_without_draft_warning():
    privacy = (FRONTEND / "privacy.html").read_text(encoding="utf-8")
    catalog = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    assert 'data-i18n="privacy.controllerIdentity"' in privacy
    assert "Canlı satış öncesi tamamlanması zorunludur" not in privacy
    assert '"privacy.controllerIdentity"' in catalog


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
    assert 'legalOperatorScript.src = "/legal-operator.js?v=4"' in i18n
    assert "/billing/operator" in operator
    assert "if (!operator?.configured) return" in operator
    assert '"/distance-sales.html", "/contact.html"' in operator
    assert "if (!showOperatorDetails) return" in operator
    assert 'card.dataset.legalOperatorLoading === "true"' in operator
    assert all(
        key in blueprint
        for key in (
            "LEGAL_OPERATOR_NAME",
            "LEGAL_OPERATOR_ADDRESS",
            "LEGAL_OPERATOR_COUNTRY",
            "LEGAL_OPERATOR_PHONE",
            "LEGAL_OPERATOR_EMAIL",
            "BILLING_PROTECTED_EMAILS",
            "LEGAL_TAX_OFFICE",
            "LEGAL_MERSIS_ID",
            "LEGAL_TRADE_REGISTRY",
            "LEGAL_KEP_ADDRESS",
            "LEGAL_CHAMBER_NAME",
        )
    )
    assert 'operator.tax_office' in operator


def test_card_payment_copy_prefers_live_iyzico_without_exposing_paytr_setup():
    plans = (FRONTEND / "plans.js").read_text(encoding="utf-8")
    catalog = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    assert "pendingCardMessage" in plans
    assert 'provider.code === "iyzico"' in plans
    assert 'location.assign(body.checkout_url)' in plans
    assert "Kartlı ödeme için PayTR mağaza bilgileri bekleniyor" not in plans
    assert "Kartlı ödeme için PayTR mağaza bilgileri bekleniyor" not in catalog


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

    for page in (
        "features.html", "document-summary.html", "lecture-video-summary.html",
        "quiz-flashcards.html", "plans.html", "about.html", "contact.html",
    ):
        assert page in sitemap


def test_public_pages_have_share_metadata_canonical_urls_and_structured_data():
    i18n = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    seo = (FRONTEND / "seo.js").read_text(encoding="utf-8")
    sitemap = (FRONTEND / "sitemap.xml").read_text(encoding="utf-8")

    assert 'seoScript.src = "/seo.js?v=4"' in i18n
    assert 'link[rel="canonical"]' in seo
    assert 'meta[property="og:image"]' in seo
    assert 'meta[property="og:image:width"]' in seo
    assert 'meta[property="og:image:height"]' in seo
    assert 'meta[name="twitter:card"]' in seo
    assert '"@type": "SoftwareApplication"' in seo
    assert '"@type": "WebPage"' in seo
    assert '"@type": "BreadcrumbList"' in seo
    assert '"@type": "FAQPage"' in seo
    assert '"@type": "Article"' in seo
    assert 'meta[property="og:locale:alternate"]' in seo
    assert 'applicationCategory: "EducationalApplication"' in seo
    assert 'price: "0"' in seo
    assert "noindex,nofollow,noarchive" in seo
    assert "max-image-preview:large" in seo
    assert 'url: `${PRODUCTION_ORIGIN}/`' in seo
    assert '"/distance-sales.html"' in seo
    assert (FRONTEND / "og-image.png").stat().st_size > 100_000
    assert sitemap.count("<lastmod>2026-08-29</lastmod>") == 13 * 13


def test_netlify_build_prerenders_every_public_language_with_static_seo():
    config = (FRONTEND.parent / "netlify.toml").read_text(encoding="utf-8")
    builder = (FRONTEND.parent / "scripts" / "build_localized_site.mjs").read_text(encoding="utf-8")
    assert 'command = "node scripts/build_localized_site.mjs"' in config
    assert 'publish = "dist"' in config
    assert "Built ${LANGUAGES.length * PUBLIC_PATHS.length} indexable localized pages" in builder
    assert 'rel="canonical"' in builder
    assert 'hreflang="x-default"' in builder
    assert 'data-lecturesift-seo' in builder
    assert '"@type": "Article"' in builder
    assert "deferNonCriticalScripts" in builder
    assert 'staticDescription' in builder
    assert 'validateLocalizedPage' in builder
    assert 'meta name="robots" content="index,follow' not in builder


def test_static_images_reserve_layout_space_to_avoid_content_shift():
    for page in FRONTEND.glob("*.html"):
        content = page.read_text(encoding="utf-8")
        for image in re.findall(r"<img\b[^>]*>", content):
            assert 'width="' in image and 'height="' in image, (page.name, image)


def test_optional_analytics_and_advertising_are_consent_gated():
    i18n = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    consent = (FRONTEND / "consent.js").read_text(encoding="utf-8")
    analytics = (FRONTEND / "analytics.js").read_text(encoding="utf-8")
    cookies = (FRONTEND / "cookies.html").read_text(encoding="utf-8")

    consent_css = (FRONTEND / "consent.css").read_text(encoding="utf-8")
    assert 'consentScript.src = "/consent.js?v=2"' in i18n
    assert 'consentStyle.href = "/consent.css?v=2"' in i18n
    assert 'const STORAGE_KEY = "lecturesift-consent-v1"' in consent
    assert 'footerTarget = document.querySelector(".footer-bottom,.legal-footer,.site-footer,footer")' in consent
    assert ".consent-manage{position:static" in consent_css
    assert ".consent-manage{position:fixed" not in consent_css
    assert 'analytics: false, advertising: false' in consent
    assert 'category === "necessary"' in consent
    assert 'analyticsScript.src = "/analytics.js?v=2"' in i18n
    assert 'window.LectureSiftConsent?.get?.()' in analytics
    assert "/analytics/config" in analytics
    assert "googletagmanager.com/gtag/js" in analytics
    assert 'ad_storage: "denied"' in analytics
    assert "allow_google_signals: false" in analytics
    assert 'const PUBLIC_PATHS = new Set([' in analytics
    assert "reset-password.html" not in analytics
    assert "verify.html" not in analytics
    assert 'ad_storage: "denied"' in analytics
    assert 'ad_user_data: "denied"' in analytics
    assert 'trackConversion' in analytics
    assert '`${ads.id}/${label}`' in analytics
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

    assert 'displayAdsScript.src = "/display-ads.js?v=2"' in i18n
    assert 'displayAdsStyle.href = "/display-ads.css?v=2"' in i18n
    assert 'LectureSiftConsent?.allows("advertising")' in display
    assert 'body.account?.plan?.entitlements?.ad_free === true' in display
    assert 'const PUBLIC_AD_PATHS = new Set([' in display
    allowed_paths = display.split("const PUBLIC_AD_PATHS", 1)[1].split("]);", 1)[0]
    assert '"/account.html"' not in allowed_paths
    assert 'json("/ads/config")' in display
    assert "securepubads.g.doubleclick.net/tag/js/gpt.js" in display
    assert "loadAdSenseAutoAds" in display
    assert "pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=" in display
    assert "renderHouseCampaign" in display
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


def test_guest_trial_becomes_a_single_use_membership_gate():
    rollout = (FRONTEND / "rollout.js").read_text(encoding="utf-8")
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    catalog = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    index = (FRONTEND / "workspace.html").read_text(encoding="utf-8")

    assert 'rollout.guest_trial || null' in rollout
    assert 'guestTrialState?.used' in rollout
    assert 'window.LectureSiftGuestTrial' in rollout
    assert "async ensureAccess()" in rollout
    assert 'LectureSiftGuestTrial?.markUsed?.(jobId)' in app
    assert '"rollout.guestUsed"' in catalog
    assert '"rollout.createFreeAccount"' in catalog
    assert 'src="./app.js?v=21"' in index
    assert 'src="/rollout.js?v=5"' in index
    assert '$("plans").scrollIntoView' not in app


def test_workspace_accepts_scans_and_explains_automatic_ocr():
    page = (FRONTEND / "workspace.html").read_text(encoding="utf-8")
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    guide = (FRONTEND / "document-summary.html").read_text(encoding="utf-8")
    for extension in (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"):
        assert extension in page
        assert f'"{extension.lstrip(".")}"' in script
    assert "taranmış sayfalarda otomatik OCR" in page
    assert "OCR otomatik çalışır" in guide


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
    assert sitemap.count("<url>") == 13 * 13

    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    auth = (FRONTEND / "auth.js").read_text(encoding="utf-8")
    assert "window.LectureSiftI18n?.language" in app
    assert "localizedPath?.(currentLanguage)" in app
    assert "I18N.localizedPath(body.account.user.preferred_language)" in auth


def test_adsense_site_ownership_and_ads_txt_are_published():
    publisher_id = "ca-pub-7608481350058806"
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    seo = (FRONTEND / "seo.js").read_text(encoding="utf-8")
    ads_txt = (FRONTEND / "ads.txt").read_text(encoding="utf-8").strip()

    assert f'<meta name="google-adsense-account" content="{publisher_id}">' in index
    assert f'const ADSENSE_ACCOUNT = "{publisher_id}"' in seo
    assert 'meta[name="google-adsense-account"]' in seo
    assert ads_txt == "google.com, pub-7608481350058806, DIRECT, f08c47fec0942fa0"


def test_google_ads_conversions_are_consent_gated_and_csp_allows_measurement():
    auth = (FRONTEND / "auth.js").read_text(encoding="utf-8")
    plans = (FRONTEND / "plans.js").read_text(encoding="utf-8")
    analytics = (FRONTEND / "analytics.js").read_text(encoding="utf-8")
    headers = (FRONTEND.parent / "netlify.toml").read_text(encoding="utf-8")
    blueprint = (FRONTEND.parent / "render.yaml").read_text(encoding="utf-8")
    assert 'choices().advertising' in analytics
    assert 'recordAnalytics("conversion", "signup"' in auth
    assert 'recordAnalytics("conversion", "purchase"' in auth
    assert 'recordPlanAnalytics("begin_checkout"' in plans
    assert "lecturesift-purchase-${reference}" in auth
    assert "https://www.googletagmanager.com" in headers
    assert "https://www.google-analytics.com" in headers
    assert 'Strict-Transport-Security = "max-age=31536000; includeSubDomains"' in headers
    assert 'for = "/:lang/verify.html"' in headers
    assert 'for = "/:lang/reset-password.html"' in headers
    assert 'for = "/account.html"' in headers and 'for = "/admin.html"' in headers
    assert all(
        key in blueprint
        for key in (
            "LECTURESIFT_GOOGLE_ADS_ID",
            "LECTURESIFT_GOOGLE_ADS_SIGNUP_LABEL",
            "LECTURESIFT_GOOGLE_ADS_PURCHASE_LABEL",
        )
    )
    backend = (FRONTEND.parent / "lecturesift" / "app.py").read_text(encoding="utf-8")
    assert 'allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"]' in backend
