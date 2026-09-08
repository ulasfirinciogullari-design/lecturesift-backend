from pathlib import Path

import pytest

from scripts import sync_static_i18n as translations


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
WARNINGS = {
    "tr": "Kart numarası, parola veya doğrulama kodu gönderme.",
    "en": "Do not send card numbers, passwords or verification codes.",
    "de": "Sende keine Kartennummern, Passwörter oder Bestätigungscodes.",
    "fr": "N’envoyez pas de numéros de carte, de mots de passe ni de codes de vérification.",
    "es": "No envíes números de tarjeta, contraseñas ni códigos de verificación.",
    "it": "Non inviare numeri di carta, password o codici di verifica.",
    "pt": "Não envie números de cartão, senhas ou códigos de verificação.",
    "ru": "Не отправляйте номера карт, пароли или коды подтверждения.",
    "ar": "لا ترسل أرقام البطاقات أو كلمات المرور أو رموز التحقق.",
    "zh-CN": "请勿发送卡号、密码或验证码。",
    "ja": "カード番号、パスワード、認証コードは送信しないでください。",
    "ko": "카드 번호, 비밀번호 또는 인증 코드는 보내지 마세요.",
    "hi": "कार्ड नंबर, पासवर्ड या सत्यापन कोड न भेजें।",
}


@pytest.mark.parametrize("language,warning", WARNINGS.items())
def test_refund_translation_prohibits_sending_sensitive_details(language, warning):
    catalog = translations.read_catalog(FRONTEND / "page-i18n.js")
    row = catalog[translations.REFUND_REQUEST_COPY]
    assert len(row) == len(translations.LANGUAGES) == 13
    assert row[translations.LANGUAGES.index(language)].endswith(warning)
    assert row == translations.SENSITIVE_TRANSLATIONS[translations.REFUND_REQUEST_COPY]


def test_sensitive_translation_override_matches_the_refund_page_source():
    parser = translations.StaticCopyParser()
    parser.feed((FRONTEND / "refund.html").read_text(encoding="utf-8"))
    assert translations.REFUND_REQUEST_COPY in parser.values
    assert set(WARNINGS) == set(translations.LANGUAGES)
    assert translations.CURATED_TRANSLATIONS[translations.REFUND_REQUEST_COPY] == (
        translations.SENSITIVE_TRANSLATIONS[translations.REFUND_REQUEST_COPY]
    )


def test_catalog_regeneration_repairs_cached_reversed_safety_copy(tmp_path):
    catalog_path = tmp_path / "page-i18n.js"
    stale_row = [translations.REFUND_REQUEST_COPY] + [
        "Send a card number, password or verification code."
    ] * 12
    unrelated_row = ["Unaffected copy"] * 13
    translations.write_catalog(
        catalog_path,
        {translations.REFUND_REQUEST_COPY: stale_row, "Unaffected copy": unrelated_row},
    )
    regenerated = translations.read_catalog(catalog_path)
    assert regenerated[translations.REFUND_REQUEST_COPY] == (
        translations.SENSITIVE_TRANSLATIONS[translations.REFUND_REQUEST_COPY]
    )
    assert regenerated["Unaffected copy"] == unrelated_row
