from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path


LANGUAGES = ("tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh-CN", "ja", "ko", "hi")
EXCLUDED_TAGS = {"script", "style", "noscript", "code", "svg", "path"}
PROTECTED_COPY = {
    "Lecture", "Sift", "LectureSift", "Meta", "Instagram", "PayTR", "iyzico",
    "Visa", "Mastercard", "Google", "GA4", "API", "URL", "IBAN", "IP", "CSV",
    "PDF", "DOCX", "TXT", "JSON", "MP3", "MP4", "WebM", "TRY", "EUR", "USD",
    "Lite", "Plus", "Pro", "Max", "Business", "ADMIN_ADMIN", "Cloudflare R2",
}
SENSITIVE_FRAGMENTS = (
    "ulaş fırıncıoğulları", "ataturk mahallesi", "atatürk mahallesi", "4495 sokak",
    "samandağ", "hatay", "05336651805", "3860643829", "tr77 0086",
)
TRANSLATABLE_ATTRIBUTES = ("placeholder", "aria-label", "title")
BRAND_VARIANTS = (
    "VortragSift", "ConférenceSift", "ConferenciaSift", "LezioneSift", "PalestraSift",
    "ЛекцияSift", "ЛекцияСифт", "ЛекчерСифт", "лекчерсифт", "लेक्चरसिफ्ट",
    "लेक्चरसिफ़्ट", "लेक्चर सिफ्ट", "讲座筛选", "讲座丝夫", "レクチャーシフト", "강의 선별",
    "Conferencia Sift",
    "__ LECTURESIFT__", "__LECTURESIFT__", "LECTURESIFT",
)
RUNTIME_COPY = {
    "Kaynak alınıyor",
    "Belge metni çıkarılıyor",
    "Ses MP3'e dönüştürülüyor",
    "Dosya paketleniyor",
    "Ses, transkript, görseller, ders içeriği ve çıktılar ayrı adımlarda ilerler.",
    "Belge metni, ders içeriği ve seçilen çıktılar sırayla hazırlanır.",
    "Kaynak yüklenir, sesi dönüştürülür ve MP3 indirmesi paketlenir.",
    "Video kaynağından indirilir ve korumalı dosya olarak hazırlanır.",
    "Kaynak yükleniyor",
    "İşlem sırasında bekliyor",
    "Sonuç dosyaları güvenceye alınıyor",
    "Yasal belgeler",
    "Kurumsal",
    "Satıcı/hizmet sağlayıcı kimliği, siparişe özgü toplam fiyat, vergi, dönem, ödeme yöntemi ve dijital hizmet başlangıcı; kullanıcı onayından hemen önce sipariş özetinde ve Mesafeli Satış Sözleşmesi'nde gösterilir.",
    "Video ve belge kaynakları aynı işte karıştırılamaz. Ayrı ayrı yükle.",
    "Belge boş görünüyor.",
    "Belge izin verilen dosya boyutundan büyük.",
    "Word veya PowerPoint belgesi bozuk ya da güvenli açılamıyor.",
    "PDF güvenli biçimde okunamadı.",
    "Şifreli PDF dosyaları desteklenmiyor.",
    "Belge veya sunum izin verilen sayfa sınırını aşıyor.",
    "Word belgesi okunamadı.",
    "PowerPoint sunumu okunamadı.",
    "Metin dosyası okunamadı.",
    "En az bir belge ekle.",
    "Bu belge biçimi desteklenmiyor.",
    "Belgeden metin çıkarılamadı. Taranmış PDF ise OCR uygulanmış bir sürüm yükle.",
    "Belgelerin toplam metni tek bir güvenli işlem için fazla. Kaynağı bölerek yeniden dene.",
}


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def restore_brand(value: str) -> str:
    for variant in BRAND_VARIANTS:
        value = value.replace(variant, "LectureSift")
    return value


def should_translate(value: str) -> bool:
    value = normalize(value)
    if not value or value in PROTECTED_COPY or not any(character.isalpha() for character in value):
        return False
    if re.fullmatch(r"[A-Z0-9_.:/+\-]+", value):
        return False
    if re.match(r"^(?:https?://|mailto:|tel:|www\.)", value, re.I):
        return False
    if re.fullmatch(r"[^\s@]+@[^\s@]+", value):
        return False
    if any(fragment in value.casefold() for fragment in SENSITIVE_FRAGMENTS):
        return False
    return True


class StaticCopyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.values: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        excluded = tag in EXCLUDED_TAGS or any(parent_excluded for _, parent_excluded in self.stack)
        translated = "data-i18n" in attributes
        self.stack.append((tag, excluded or translated))
        if excluded:
            return
        for attribute in TRANSLATABLE_ATTRIBUTES:
            if attribute in attributes and f"data-i18n-{attribute}" not in attributes:
                self.add(attributes.get(attribute))
        if tag == "meta" and attributes.get("name") == "description":
            self.add(attributes.get("content"))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        if self.stack:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        if not any(excluded for _, excluded in self.stack):
            self.add(data)

    def add(self, value: str | None) -> None:
        value = normalize(value or "")
        if should_translate(value):
            self.values.add(value)


def collect_static_copy(frontend: Path) -> set[str]:
    values: set[str] = set()
    for page in sorted(frontend.glob("*.html")):
        parser = StaticCopyParser()
        parser.feed(page.read_text(encoding="utf-8"))
        values.update(parser.values)
    return values


def read_catalog(path: Path) -> dict[str, list[str]]:
    source = path.read_text(encoding="utf-8")
    payload = source.split("window.LECTURESIFT_PAGE_COPY=", 1)[1].rstrip(";\r\n")
    return json.loads(payload)


def read_central_sources(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    values: set[str] = set()
    for payload in re.findall(r'^\s*"[^"]+":\[(.*?)\],?$', source, re.MULTILINE):
        try:
            row = json.loads(f"[{payload}]")
        except json.JSONDecodeError:
            continue
        if row:
            values.add(normalize(str(row[0])))
    return values


def write_catalog(path: Path, catalog: dict[str, list[str]]) -> None:
    ordered = {}
    for key in sorted(catalog, key=str.casefold):
        values = [restore_brand(str(value)) for value in catalog[key]]
        if key.startswith("LectureSift"):
            values = [
                value if value.count("LectureSift") >= key.count("LectureSift") else f"LectureSift — {value}"
                for value in values
            ]
        ordered[key] = values
    payload = json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))
    path.write_text(
        "// Generated static UI translations. Do not edit entries by hand.\n"
        f"window.LECTURESIFT_PAGE_COPY={payload};\n",
        encoding="utf-8",
    )


def translate_payload(source: str, language: str, attempts: int = 4) -> str:
    if any(fragment in source.casefold() for fragment in SENSITIVE_FRAGMENTS):
        raise ValueError("Refusing to send seller identity or contact data to a translation service.")
    endpoint = "https://translate.googleapis.com/translate_a/single"
    curl = shutil.which("curl.exe") or shutil.which("curl")
    for attempt in range(attempts):
        try:
            if curl:
                raw = subprocess.check_output(
                    [
                        curl, "-fsS", "--get",
                        "--data-urlencode", "client=gtx",
                        "--data-urlencode", "sl=tr",
                        "--data-urlencode", f"tl={language}",
                        "--data-urlencode", "dt=t",
                        "--data-urlencode", f"q={source}",
                        endpoint,
                    ],
                    timeout=35,
                )
                payload = json.loads(raw.decode("utf-8"))
            else:
                query = urllib.parse.urlencode({"client": "gtx", "sl": "tr", "tl": language, "dt": "t", "q": source})
                request = urllib.request.Request(f"{endpoint}?{query}")
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            result = normalize("".join(part[0] for part in payload[0] if part and part[0]))
            if result:
                return result
        except Exception:
            if attempt + 1 == attempts:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"translation failed for {language}: {source[:80]}")


def translate_batch(sources: list[str], language: str) -> list[str]:
    separator = "[[LSSEP_A9F3]]"
    result = translate_payload(f"\n{separator}\n".join(sources), language)
    values = [normalize(value) for value in result.split(separator)]
    if len(values) == len(sources) and all(values):
        return [restore_brand(value) for value in values]
    return [restore_brand(translate_payload(source, language)) for source in sources]


def batches(values: list[str], max_items: int = 12, max_characters: int = 2400):
    batch: list[str] = []
    characters = 0
    for value in values:
        if batch and (len(batch) >= max_items or characters + len(value) > max_characters):
            yield batch
            batch, characters = [], 0
        batch.append(value)
        characters += len(value)
    if batch:
        yield batch


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit and update static LectureSift page translations.")
    parser.add_argument("--check", action="store_true", help="Fail when visible static copy is missing.")
    parser.add_argument("--sync", action="store_true", help="Translate and append missing visible copy.")
    parser.add_argument("--normalize", action="store_true", help="Normalize protected brand names without network access.")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N missing strings.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    frontend = root / "frontend"
    catalog_path = frontend / "page-i18n.js"
    catalog = read_catalog(catalog_path)
    if args.normalize:
        write_catalog(catalog_path, catalog)
        catalog = read_catalog(catalog_path)
    central_sources = read_central_sources(frontend / "i18n.js")
    required = collect_static_copy(frontend) | RUNTIME_COPY
    missing = sorted(required - catalog.keys() - central_sources, key=str.casefold)
    if args.limit:
        missing = missing[: args.limit]
    if missing and args.sync:
        total = len(missing)
        translated = {source: [source] for source in missing}
        completed = 0
        grouped = list(batches(missing))
        for language in LANGUAGES[1:]:
            for batch in grouped:
                for source, value in zip(batch, translate_batch(batch, language), strict=True):
                    translated[source].append(value)
                time.sleep(0.08)
            print(f"Translated {total} strings to {language}.", flush=True)
        for source, values in translated.items():
            catalog[source] = values
            completed += 1
            if completed % 25 == 0 or completed == total:
                print(f"Prepared {completed}/{total} catalog rows.", flush=True)
        write_catalog(catalog_path, catalog)
        missing = sorted(required - catalog.keys() - central_sources, key=str.casefold)
    if missing:
        print(f"Missing {len(missing)} static translations:", file=sys.stderr)
        for value in missing:
            print(f"- {value}", file=sys.stderr)
        return 1 if args.check else 0
    print(f"Static translation coverage complete: {len(required)} visible strings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
