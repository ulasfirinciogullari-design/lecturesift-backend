import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

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
    "Orijinal video, ses veya belge dosyan, işlem bittiğinde, başarısız olsa bile silinir. "
    "Silme aksarsa dosya, ders silindiğinde veya süresi dolduğunda dersle birlikte kaldırılır."
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
    assert "6 saat" not in home
    assert "işlem başarıyla tamamlanınca silinir" not in home
    row = CATALOG[HOME_SOURCE_DELETION]
    assert all(value.strip() for value in row)
    assert not any(re.search(r"\b6\b", value) for value in row)
