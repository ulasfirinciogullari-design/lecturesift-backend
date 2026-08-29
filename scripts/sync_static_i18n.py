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
    "render", "web_service",
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
    "Belge metni ve OCR işleniyor",
    "Ses MP3'e dönüştürülüyor",
    "Dosya paketleniyor",
    "Ses, transkript, görseller, ders içeriği ve çıktılar ayrı adımlarda ilerler.",
    "Seçilebilir metin ve taranmış sayfalar OCR ile işlenir; ardından çalışma paketi hazırlanır.",
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
    "OCR tamamlandı ancak okunabilir metin bulunamadı. Daha net bir tarama veya doğru kaynak diliyle yeniden dene.",
    "Belgelerin toplam metni tek bir güvenli işlem için fazla. Kaynağı bölerek yeniden dene.",
    "OCR hizmeti geçici olarak kullanılamıyor. Biraz sonra yeniden dene.",
    "Belgede tek işlem için çok fazla taranmış sayfa var. Belgeyi bölerek yeniden dene.",
    "Bir sayfanın OCR işlemi zaman sınırını aştı. Belgeyi bölerek yeniden dene.",
    "Taranmış sayfa veya görsel güvenli biçimde okunamadı.",
    "taranmış sayfa",
    "Belge analizi hazır",
    "Medya kaynağı hazır",
}

# Curated admin/accounting copy remains available when the development-time
# translation endpoint is unavailable or rate-limited. Keep the same language
# order as LANGUAGES and review financial terminology rather than accepting a
# blind machine translation.
CURATED_TRANSLATIONS = {
    "%0 doğrulandı": ["%0 doğrulandı", "%0 verified", "%0 bestätigt", "%0 vérifié", "%0 verificado", "%0 verificato", "%0 verificado", "%0 подтверждено", "تم التحقق من %0", "已核对 %0", "%0 確認済み", "%0 확인됨", "%0 सत्यापित"],
    "API anahtarı, kart bilgisi veya fatura içeriği yükleme. Yalnızca toplam tutarı ve fatura/ekstre referansını kaydet.": ["API anahtarı, kart bilgisi veya fatura içeriği yükleme. Yalnızca toplam tutarı ve fatura/ekstre referansını kaydet.", "Do not upload API keys, card details, or invoice content. Record only the total and invoice or statement reference.", "Keine API-Schlüssel, Kartendaten oder Rechnungsinhalte hochladen. Nur Gesamtbetrag und Rechnungs- oder Abrechnungsreferenz erfassen.", "Ne téléversez pas de clé API, de données de carte ni de contenu de facture. Enregistrez uniquement le total et la référence de facture ou de relevé.", "No subas claves API, datos de tarjeta ni contenido de facturas. Registra solo el total y la referencia de factura o extracto.", "Non caricare chiavi API, dati della carta o contenuti della fattura. Registra solo il totale e il riferimento della fattura o dell'estratto.", "Não envie chaves de API, dados de cartão ou conteúdo da fatura. Registre apenas o total e a referência da fatura ou do extrato.", "Не загружайте ключи API, данные карт или содержимое счетов. Укажите только итоговую сумму и номер счёта или выписки.", "لا ترفع مفاتيح API أو بيانات البطاقة أو محتوى الفاتورة. سجّل فقط الإجمالي ومرجع الفاتورة أو الكشف.", "请勿上传 API 密钥、银行卡信息或发票内容。仅记录总额以及发票或对账单编号。", "APIキー、カード情報、請求書の内容はアップロードしないでください。合計額と請求書または明細の参照番号のみを記録します。", "API 키, 카드 정보 또는 청구서 내용을 업로드하지 마세요. 총액과 청구서 또는 명세서 참조 번호만 기록하세요.", "API कुंजी, कार्ड विवरण या चालान की सामग्री अपलोड न करें। केवल कुल राशि और चालान या विवरण संदर्भ दर्ज करें।"],
    "Ara toplam": ["Ara toplam", "Subtotal", "Zwischensumme", "Sous-total", "Subtotal", "Subtotale", "Subtotal", "Промежуточный итог", "المجموع الفرعي", "小计", "小計", "소계", "उप-योग"],
    "Açıklama": ["Açıklama", "Description", "Beschreibung", "Description", "Descripción", "Descrizione", "Descrição", "Описание", "الوصف", "说明", "説明", "설명", "विवरण"],
    "Ağustos 2026 faturası": ["Ağustos 2026 faturası", "August 2026 invoice", "Rechnung August 2026", "Facture d’août 2026", "Factura de agosto de 2026", "Fattura di agosto 2026", "Fatura de agosto de 2026", "Счёт за август 2026 г.", "فاتورة أغسطس 2026", "2026 年 8 月发票", "2026年8月の請求書", "2026년 8월 청구서", "अगस्त 2026 का चालान"],
    "BİRİM EKONOMİ": ["BİRİM EKONOMİ", "UNIT ECONOMICS", "STÜCKÖKONOMIE", "ÉCONOMIE UNITAIRE", "ECONOMÍA UNITARIA", "ECONOMIA UNITARIA", "ECONOMIA UNITÁRIA", "ЮНИТ-ЭКОНОМИКА", "اقتصاديات الوحدة", "单位经济", "ユニットエコノミクス", "단위 경제성", "इकाई अर्थशास्त्र"],
    "Dakika, iş ve gelir karşılığı": ["Dakika, iş ve gelir karşılığı", "Minutes, jobs, and revenue comparison", "Vergleich von Minuten, Aufträgen und Umsatz", "Comparaison des minutes, tâches et revenus", "Comparación de minutos, tareas e ingresos", "Confronto tra minuti, attività e ricavi", "Comparação de minutos, trabalhos e receita", "Сопоставление минут, задач и выручки", "مقارنة الدقائق والمهام والإيرادات", "分钟、任务和收入对比", "分数・ジョブ・収益の比較", "시간·작업·수익 비교", "मिनट, कार्य और आय की तुलना"],
    "DOĞRULUK": ["DOĞRULUK", "ACCURACY", "GENAUIGKEIT", "EXACTITUDE", "EXACTITUD", "ACCURATEZZA", "EXATIDÃO", "ТОЧНОСТЬ", "الدقة", "准确性", "正確性", "정확성", "सटीकता"],
    "Dönem başlangıcı": ["Dönem başlangıcı", "Period start", "Periodenbeginn", "Début de période", "Inicio del período", "Inizio del periodo", "Início do período", "Начало периода", "بداية الفترة", "期间开始", "期間開始", "기간 시작", "अवधि प्रारंभ"],
    "Fatura / mutabakat no": ["Fatura / mutabakat no", "Invoice / reconciliation no.", "Rechnungs-/Abgleichnummer", "N° de facture / rapprochement", "N.º de factura / conciliación", "N. fattura / riconciliazione", "N.º da fatura / conciliação", "№ счёта / сверки", "رقم الفاتورة / المطابقة", "发票/核对编号", "請求書／照合番号", "청구서/대사 번호", "चालान / मिलान संख्या"],
    "Fatura numarası veya ekstre referansı": ["Fatura numarası veya ekstre referansı", "Invoice number or statement reference", "Rechnungsnummer oder Abrechnungsreferenz", "Numéro de facture ou référence de relevé", "Número de factura o referencia de extracto", "Numero fattura o riferimento estratto", "Número da fatura ou referência do extrato", "Номер счёта или ссылка на выписку", "رقم الفاتورة أو مرجع الكشف", "发票编号或对账单编号", "請求書番号または明細参照番号", "청구서 번호 또는 명세서 참조", "चालान संख्या या विवरण संदर्भ"],
    "Fatura ve mutabakat kayıtları": ["Fatura ve mutabakat kayıtları", "Invoice and reconciliation records", "Rechnungs- und Abgleichsdaten", "Factures et rapprochements", "Registros de facturas y conciliación", "Registri di fatture e riconciliazione", "Registros de faturas e conciliação", "Счета и акты сверки", "سجلات الفواتير والمطابقة", "发票与核对记录", "請求書と照合記録", "청구서 및 대사 기록", "चालान और मिलान रिकॉर्ड"],
    "Gider, fatura ve birim ekonomi": ["Gider, fatura ve birim ekonomi", "Costs, invoices, and unit economics", "Kosten, Rechnungen und Stückökonomie", "Coûts, factures et économie unitaire", "Costes, facturas y economía unitaria", "Costi, fatture ed economia unitaria", "Custos, faturas e economia unitária", "Расходы, счета и юнит-экономика", "التكاليف والفواتير واقتصاديات الوحدة", "成本、发票与单位经济", "コスト・請求書・ユニットエコノミクス", "비용·청구서·단위 경제성", "लागत, चालान और इकाई अर्थशास्त्र"],
    "Hizmet": ["Hizmet", "Service", "Dienst", "Service", "Servicio", "Servizio", "Serviço", "Услуга", "الخدمة", "服务", "サービス", "서비스", "सेवा"],
    "KAYNAK / MODEL": ["KAYNAK / MODEL", "RESOURCE / MODEL", "RESSOURCE / MODELL", "RESSOURCE / MODÈLE", "RECURSO / MODELO", "RISORSA / MODELLO", "RECURSO / MODELO", "РЕСУРС / МОДЕЛЬ", "المورد / النموذج", "资源 / 模型", "リソース／モデル", "리소스/모델", "संसाधन / मॉडल"],
    "Kesin gideri kaydet": ["Kesin gideri kaydet", "Save actual cost", "Ist-Kosten speichern", "Enregistrer le coût réel", "Guardar coste real", "Salva costo effettivo", "Salvar custo real", "Сохранить фактический расход", "حفظ التكلفة الفعلية", "保存实际成本", "実コストを保存", "실제 비용 저장", "वास्तविक लागत सहेजें"],
    "KESİN GİDER": ["KESİN GİDER", "ACTUAL COST", "IST-KOSTEN", "COÛT RÉEL", "COSTE REAL", "COSTO EFFETTIVO", "CUSTO REAL", "ФАКТИЧЕСКИЙ РАСХОД", "التكلفة الفعلية", "实际成本", "実コスト", "실제 비용", "वास्तविक लागत"],
    "Kullanım ve fiyat kaynağı kırılımı": ["Kullanım ve fiyat kaynağı kırılımı", "Breakdown by usage and pricing source", "Aufschlüsselung nach Nutzung und Preisquelle", "Ventilation par utilisation et source tarifaire", "Desglose por uso y fuente de precios", "Dettaglio per utilizzo e fonte dei prezzi", "Detalhamento por uso e fonte de preços", "Разбивка по использованию и источнику цен", "تفصيل حسب الاستخدام ومصدر التسعير", "按用量和价格来源细分", "利用量と価格情報源の内訳", "사용량 및 가격 출처별 분석", "उपयोग और मूल्य स्रोत के अनुसार विवरण"],
    "Mutabakat kapsamı": ["Mutabakat kapsamı", "Reconciliation coverage", "Abgleichsabdeckung", "Couverture du rapprochement", "Cobertura de conciliación", "Copertura della riconciliazione", "Cobertura da conciliação", "Охват сверки", "تغطية المطابقة", "核对覆盖率", "照合範囲", "대사 범위", "मिलान कवरेज"],
    "Sağlayıcı": ["Sağlayıcı", "Provider", "Anbieter", "Fournisseur", "Proveedor", "Fornitore", "Fornecedor", "Поставщик", "المزوّد", "服务商", "プロバイダー", "공급자", "प्रदाता"],
    "Tahmini operasyon giderini faturayla doğrulanmış gerçek giderden ayır; her tutarın kaynağını, dönemini ve doğruluk durumunu gör.": ["Tahmini operasyon giderini faturayla doğrulanmış gerçek giderden ayır; her tutarın kaynağını, dönemini ve doğruluk durumunu gör.", "Separate estimated operating costs from invoice-verified actual costs; see the source, period, and verification status of every amount.", "Trenne geschätzte Betriebskosten von durch Rechnungen belegten Ist-Kosten; sieh Quelle, Zeitraum und Prüfstatus jedes Betrags.", "Distinguez les coûts d’exploitation estimés des coûts réels vérifiés par facture ; consultez la source, la période et le statut de vérification de chaque montant.", "Separa los costes operativos estimados de los costes reales verificados por factura; consulta la fuente, el período y el estado de verificación de cada importe.", "Separa i costi operativi stimati dai costi effettivi verificati tramite fattura; consulta fonte, periodo e stato di verifica di ogni importo.", "Separe os custos operacionais estimados dos custos reais verificados por fatura; veja a fonte, o período e o estado de verificação de cada valor.", "Отделяйте оценочные операционные расходы от фактических, подтверждённых счетами; смотрите источник, период и статус проверки каждой суммы.", "افصل تكاليف التشغيل التقديرية عن التكاليف الفعلية المثبتة بالفواتير، واطّلع على مصدر كل مبلغ وفترته وحالة التحقق منه.", "将运营成本估算与经发票核实的实际成本分开；查看每笔金额的来源、期间和核实状态。", "運用コストの見積もりと請求書で確認済みの実コストを分け、各金額の出典・期間・確認状況を表示します。", "예상 운영 비용과 청구서로 확인된 실제 비용을 구분하고 각 금액의 출처, 기간, 확인 상태를 확인하세요.", "अनुमानित संचालन लागत को चालान से सत्यापित वास्तविक लागत से अलग रखें; हर राशि का स्रोत, अवधि और सत्यापन स्थिति देखें।"],
    "Vergi": ["Vergi", "Tax", "Steuer", "Taxe", "Impuesto", "Imposta", "Imposto", "Налог", "الضريبة", "税费", "税", "세금", "कर"],
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
        translated = {source: list(CURATED_TRANSLATIONS.get(source, [source])) for source in missing}
        network_missing = [source for source, values in translated.items() if len(values) == 1]
        completed = 0
        grouped = list(batches(network_missing))
        if network_missing:
            for language in LANGUAGES[1:]:
                for batch in grouped:
                    for source, value in zip(batch, translate_batch(batch, language), strict=True):
                        translated[source].append(value)
                    time.sleep(0.08)
                print(f"Translated {len(network_missing)} strings to {language}.", flush=True)
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
