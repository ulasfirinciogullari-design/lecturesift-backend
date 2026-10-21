import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from lecturesift import config
from lecturesift.billing import PLAN_BY_CODE
from scripts import sync_static_i18n as translations


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
CATALOG = translations.read_catalog(FRONTEND / "page-i18n.js")
LANGUAGES = translations.LANGUAGES

# "dakika" is always minutes of media or plan allowance. Machine translation
# repeatedly turned it into meeting minutes or telephone call time.
MEETING_MINUTES = {
    "fr": r"procès-verba",
    "es": r"\bactas?\b",
    "it": r"\bverbal[ei]\b",
    "pt": r"\batas?\b",
    "ru": r"протокол",
    "zh-CN": r"会议记录|通话",
    "ja": r"議事録|通話",
    "ko": r"회의록|통화 시간",
}
PRIVACY_TOPIC = "KVKK / gizlilik başvurusu"
HOME_SOURCE_DELETION = (
    "Orijinal video, ses veya belge dosyan işlem bittiğinde, başarısız olsa bile, en kısa sürede silinir; "
    "her durumda en geç ders silindiğinde veya süresi dolduğunda dersle birlikte kaldırılır."
)
HOME_RESULT_RETENTION = (
    "Sonuçlar planına göre saklanır: misafir denemede 1 gün, ücretsiz hesapta 7 gün, ücretli planlarda 30 günden 2 yıla kadar. "
    "Süresi dolan ders dosyalarıyla birlikte otomatik silinir; istediğin dersi Çalışma Alanı’ndan daha önce de silebilirsin."
)


def _mentions_minutes(source: str) -> bool:
    return "dakika" in source.replace("İ", "i").casefold()


def _page(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


class _AltParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        alt = dict(attrs).get("alt")
        if tag == "img" and alt:
            self.values.append(translations.normalize(alt))


def test_german_minutes_are_never_translated_as_protokoll():
    german = LANGUAGES.index("de")
    offenders = [
        source for source, row in CATALOG.items()
        if _mentions_minutes(source) and "protokoll" in row[german].casefold()
    ]
    assert offenders == []


@pytest.mark.parametrize("language,pattern", MEETING_MINUTES.items())
def test_minutes_are_never_translated_as_meeting_minutes_or_call_time(language, pattern):
    index = LANGUAGES.index(language)
    offenders = [
        source for source, row in CATALOG.items()
        if _mentions_minutes(source) and re.search(pattern, row[index], re.IGNORECASE)
    ]
    assert offenders == []


def test_image_alt_text_is_translated_for_every_language():
    alts = set()
    for page in FRONTEND.glob("*.html"):
        parser = _AltParser()
        parser.feed(page.read_text(encoding="utf-8"))
        alts.update(value for value in parser.values if translations.should_translate(value))
    assert "iyzico ile Öde, Mastercard, Visa, American Express ve TROY" in alts
    for alt in sorted(alts):
        row = CATALOG.get(alt)
        assert row and len(row) == len(LANGUAGES) and all(value.strip() for value in row), alt
        assert row[LANGUAGES.index("en")] != alt, alt


def test_privacy_request_topic_matches_the_contact_form_in_every_language():
    options = re.findall(r"<option>([^<]+)</option>", _page("contact.html"))
    assert PRIVACY_TOPIC in options
    privacy = _page("privacy.html")
    assert f"“{PRIVACY_TOPIC}”" in privacy
    assert "Gizlilik/KVKK talebi" not in privacy
    paragraph = next(source for source in CATALOG if source.startswith(f"“{PRIVACY_TOPIC}”"))
    for index, language in enumerate(LANGUAGES):
        assert CATALOG[PRIVACY_TOPIC][index] in CATALOG[paragraph][index], language


def test_workspace_offer_copy_matches_the_free_plan():
    free = PLAN_BY_CODE["free"]
    assert free.minutes == 120 and free.export_formats == ("pdf",)
    assert {"docx", "txt"} <= set(PLAN_BY_CODE["lite"].export_formats)
    workspace = _page("workspace.html")
    # The free allowance resets every month; it is not a one-time first 120 minutes.
    assert "İlk 120 dakika" not in workspace
    assert "<h3>Her ay 120 dakika ücretsiz</h3>" in workspace
    assert "<span>✓ Ücretsiz hesapla her ay 120 dakika</span>" in workspace
    # Free accounts and guests download PDF only; Word and TXT need a paid plan.
    assert "✓ PDF, Word ve TXT" not in workspace
    assert "<span>✓ PDF; ücretli planlarda Word ve TXT</span>" in workspace


def test_homepage_upload_deletion_describes_the_cleanup_that_actually_runs():
    # Sources are deleted when a job ends, on success and on failure. Failed
    # object-storage deletes are not retried, so the homepage must not promise
    # a fixed deadline; leftovers go with the lesson's own deletion or expiry.
    home = _page("index.html")
    assert f"<li>{HOME_SOURCE_DELETION}</li>" in home
    assert f"<li>{HOME_RESULT_RETENTION}</li>" in home
    assert "6 saat" not in home
    assert "işlem başarıyla tamamlanınca silinir" not in home
    assert "Silme aksarsa" not in home
    # Retention follows the plan catalog: guest 1 day, free 7, and from 30
    # days up to 2 years (730) across every paid plan, since the homepage
    # links to all plans (rollout_service registers the hidden guest plan).
    from lecturesift import rollout_service  # noqa: F401
    assert PLAN_BY_CODE["guest"].history_days == 1
    assert PLAN_BY_CODE["free"].history_days == 7
    # Assistant credit and ad-free purchases keep no lessons, so only plans
    # that process minutes (and the quoted Business plan) count here.
    paid = [plan.history_days for code, plan in PLAN_BY_CODE.items()
            if plan.kind in {"one_time", "subscription", "quote"} and code != "test"
            and (plan.minutes or plan.kind == "quote")]
    assert min(paid) == 30 and max(paid) == 730
    for sentence in (HOME_SOURCE_DELETION, HOME_RESULT_RETENTION):
        row = CATALOG[sentence]
        assert all(value.strip() for value in row)
        assert not any(re.search(r"\b6\b", value) for value in row)


def test_topic_guides_quote_the_trial_and_free_plan_limits_the_code_enforces():
    # The exam and topic guides quote these allowances in their FAQ and
    # LectureSift sections. If a plan changes, update the guides with it.
    free = PLAN_BY_CODE["free"]
    assert config.GUEST_TRIAL_MAX_MINUTES == 30
    assert (free.minutes, free.history_days, free.max_minutes_per_job, free.max_files_per_job) == (120, 7, 60, 3)
    assert free.export_formats == ("pdf",)
    # "Paid plans allow longer jobs, more files and Word/TXT export."
    for code in ("credit", "lite", "plus", "pro", "max"):
        plan = PLAN_BY_CODE[code]
        assert plan.max_minutes_per_job > free.max_minutes_per_job, code
        assert plan.max_files_per_job > free.max_files_per_job, code
        assert {"docx", "txt"} <= set(plan.export_formats), code
    resources = json.loads((FRONTEND / "study-resources.json").read_text(encoding="utf-8"))
    topics = [page for page in resources["pages"] if page["kind"] == "topic"]
    assert len(topics) == 7
    required = {"tr": ("30 dakikaya kadar", "her ay 120 dakika"), "en": ("up to 30 minutes", "120 minutes each month")}
    retention = {"tr": r"(\d+) gün saklanır", "en": r"kept for (\d+) days"}
    for page in topics:
        for language, phrases in required.items():
            body = page[language]["body"]
            for phrase in phrases:
                assert phrase in body, (page["slug"], language, phrase)
            assert set(re.findall(retention[language], body)) <= {str(free.history_days)}, page["slug"]
    long_tr = next(page for page in topics if page["slug"] == "long-lecture-recordings")["tr"]["body"]
    long_en = next(page for page in topics if page["slug"] == "long-lecture-recordings")["en"]["body"]
    assert "tek bir iş en fazla 60 dakika ve 3 kaynak" in long_tr
    assert "at most 60 minutes long and contain up to 3 sources" in long_en
    # Source cleanup is best-effort (see the homepage test above), so the
    # guide carries the same fallback instead of an unconditional promise.
    assert "başarısız olsa bile silinir; silme aksarsa dosya, ders silindiğinde veya süresi dolduğunda" in long_tr
    assert "even if processing fails; if that deletion fails, the file is removed with the lesson" in long_en
    assert "iş bittiğinde silinir" not in long_tr and "deleted when the job ends" not in long_en
    # The Exam preset asks for 20 questions and 40 cards; only the free plan
    # caps them lower, so the finals guide quotes that cap instead of a flat 20/40.
    assert (free.quiz_questions, free.flashcards) == (10, 20)
    assert (PLAN_BY_CODE["lite"].quiz_questions, PLAN_BY_CODE["lite"].flashcards) == (20, 40)
    finals = next(page for page in topics if page["slug"] == "final-exam-study-plan")
    assert "ücretsiz hesapta sınır 10 soru ve 20 karttır" in finals["tr"]["body"]
    assert "free accounts are limited to 10 questions and 20 cards" in finals["en"]["body"]
    assert "Lite" not in finals["tr"]["body"] and "Lite" not in finals["en"]["body"]
    assert "planının sınırına göre ayarlanır" not in finals["tr"]["body"]
    assert "adjusted to your plan's limits" not in finals["en"]["body"]
