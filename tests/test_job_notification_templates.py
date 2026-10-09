"""Mail rendering regressions: privacy, allowance semantics and all locales."""

from html.parser import HTMLParser

import pytest

from lecturesift.job_notification_templates import (
    SUPPORTED_LANGUAGES,
    render_job_failure_email,
)


class _EmailHTML(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.tags = []
        self.root = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.tags.append(tag)
        if tag == "html":
            self.root = attributes
        if tag == "a":
            self.links.append(attributes["href"])


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_all_site_languages_have_localized_copy_and_working_route_contract(language):
    mail = render_job_failure_email(
        language, error_code="LS-BILL-10", required_minutes=777,
        remaining_minutes=61, max_minutes_per_job=300,
        document_mode=True, job_id="synthetic-job-123",
    )
    parsed = _EmailHTML(mail["html"])
    prefix = "" if language == "tr" else f"/{language}"
    assert parsed.root == {"lang": language, "dir": "rtl" if language == "ar" else "ltr"}
    assert parsed.links == [
        f"https://lecturesift.com{prefix}/workspace.html",
        f"https://lecturesift.com{prefix}/plans",
        f"https://lecturesift.com{prefix}/contact",
    ]
    for value in ("777", "61", "300", "synthetic-job-123", "PDF"):
        assert value in mail["text"]
        assert value in mail["html"]
    assert not any(token in mail["text"] for token in ("{value}", "{name}", "None"))
    if language != "en":
        assert "Required allowance" not in mail["text"]
        assert "Check that the file opens" not in mail["text"]


def test_localized_subjects_are_distinct_across_all_thirteen_languages():
    subjects = {
        render_job_failure_email(language, error_code="LS-SYSTEM-01")["subject"]
        for language in SUPPORTED_LANGUAGES
    }
    assert len(subjects) == 13


@pytest.mark.parametrize("locale, expected", [("en-US", "en"), ("PT_br", "pt"), ("AR-sa", "ar"), ("zh-CN", "zh"), ("unknown", "tr"), ("", "tr")])
def test_regional_languages_and_safe_default(locale, expected):
    mail = render_job_failure_email(locale, error_code="LS-SYSTEM-01")
    assert _EmailHTML(mail["html"]).root["lang"] == expected


def test_quota_numbers_describe_usage_not_document_processing_duration():
    mail = render_job_failure_email(
        "tr", error_code="LS-BILL-10", required_minutes=3395,
        remaining_minutes=60, max_minutes_per_job=30, document_mode=True,
    )
    assert "3395 dakika" in mail["text"]
    assert "60 dakika" in mail["text"]
    assert "30 dakika" in mail["text"]
    assert "bekleme veya tamamlanma süresi değildir" in mail["text"]
    assert "Ücretsiz hesapta" in mail["text"]
    assert "daha kısa bir bölüm" in mail["text"]
    assert "iade" not in mail["text"].lower()
    assert "kesilmedi" not in mail["text"].lower()


def test_balance_rejection_takes_precedence_over_per_job_limit():
    mail = render_job_failure_email(
        "en", error_code="LS-BILL-10", required_minutes=150,
        remaining_minutes=20, max_minutes_per_job=30,
    )
    assert "exceeds the allowance remaining in your account" in mail["text"]


def test_per_job_limit_does_not_falsely_say_balance_is_insufficient():
    mail = render_job_failure_email(
        "en", error_code="LS-BILL-10", required_minutes=50,
        remaining_minutes=500, max_minutes_per_job=30,
    )
    assert "usage limit for a single processing job" in mail["text"]
    assert "exceeds the allowance remaining in your account" not in mail["text"]


def test_missing_allowance_numbers_does_not_invent_a_balance_or_processing_time():
    mail = render_job_failure_email("en", error_code="LS-BILL-10", document_mode=True)
    assert "one of your account's per-job source limits" in mail["text"]
    assert "Required allowance:" not in mail["text"]
    assert "Remaining allowance:" not in mail["text"]
    assert "not waiting time or processing duration" in mail["text"]


def test_zero_balance_is_kept_and_invalid_numeric_values_are_omitted():
    mail = render_job_failure_email(
        "en", error_code="LS-BILL-10", required_minutes=float("nan"),
        remaining_minutes=0, max_minutes_per_job=float("inf"),
    )
    assert "Remaining allowance: 0 minutes" in mail["text"]
    assert "Required allowance:" not in mail["text"]
    assert "Allowance limit per job:" not in mail["text"]


@pytest.mark.parametrize("invalid", [-1, True, "<script>bad</script>", "-Infinity", "1e9999"])
def test_untrusted_numeric_fields_cannot_render_raw_content(invalid):
    mail = render_job_failure_email("en", error_code="LS-BILL-10", required_minutes=invalid)
    assert "Required allowance:" not in mail["text"]
    assert "script" not in _EmailHTML(mail["html"]).tags


def test_name_is_escaped_and_reference_cannot_inject_html_or_urls():
    mail = render_job_failure_email(
        "en", error_code="LS-SYSTEM-01",
        first_name='<img src=x onerror="bad()"> & User\r\nName',
        job_id='https://untrusted.example/?private=provider-secret',
    )
    parsed = _EmailHTML(mail["html"])
    assert "img" not in parsed.tags
    assert "&lt;img" in mail["html"]
    assert "&amp; User Name" in mail["html"]
    assert "untrusted.example" not in mail["text"]
    assert "provider-secret" not in mail["html"]
    assert "\r" not in mail["subject"] and "\n" not in mail["subject"]


def test_unknown_error_cannot_leak_diagnostics_or_turn_into_a_sales_message():
    mail = render_job_failure_email(
        "en", error_code="provider-secret /users/private/source.pdf",
        required_minutes=123, remaining_minutes=0,
    )
    assert "A problem occurred" in mail["text"]
    assert "try again later" in mail["text"]
    assert "Contact support" in mail["text"]
    assert "/plans" not in mail["text"]
    assert "provider-secret" not in mail["text"]
    assert "source.pdf" not in mail["text"]
    assert "Required allowance" not in mail["text"]


@pytest.mark.parametrize("error_code", ["LS-AI-01", "LS-AI-02", "LS-AI-03", "LS-AI-08", "LS-SYSTEM-01", "LS-UPLOAD-02"])
def test_provider_and_processing_failures_do_not_blame_users_allowance(error_code):
    mail = render_job_failure_email("en", error_code=error_code)
    assert "A problem occurred" in mail["text"]
    assert "free account" not in mail["text"]
    assert "allowance" not in mail["text"]
    assert "refund" not in mail["text"]
    assert "not charged" not in mail["text"]
