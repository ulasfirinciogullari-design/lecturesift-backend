"""Localized, privacy-preserving copy for terminal processing failures.

Only structured allowance values are accepted. Provider errors, filenames and
source contents deliberately have no place in this interface.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from html import escape
import re


SUPPORTED_LANGUAGES = ("tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi")

# Keep these independent of frontend or account imports: rendering must not
# perform database reads, contact a translation provider or send an email.
_COPY = {
    "tr": {
        "title": "Kaynağının işlenmesi tamamlanamadı",
        "greeting": "Merhaba{name},",
        "generic": "Kaynağını işlerken bir sorun oluştu ve bu işlem tamamlanamadı.",
        "balance": "Bu kaynağı işlemek için gereken kullanım hakkı, hesabında kalan hakkı aşıyor.",
        "limit": "Bu kaynak, mevcut planının tek işlem için izin verdiği kullanım sınırını aşıyor.",
        "quota": "Bu kaynak, hesabının mevcut kullanım hakkını veya işlem başına kaynak sınırlarından birini aşıyor.",
        "document": "PDF ve diğer belgelerde dakika, kullanım hakkını ölçen bir birimdir; işlemin bekleme veya tamamlanma süresi değildir.",
        "required": "Gereken kullanım hakkı: {value} dakika",
        "remaining": "Hesabında kalan kullanım hakkı: {value} dakika",
        "maximum": "Tek işlem için kullanım sınırı: {value} dakika",
        "quota_next": "Ücretsiz hesapta da mevcut sınırlarına uygun daha kısa bir bölüm veya daha az sayfa ile yeniden deneyebilirsin. Daha fazla kullanım hakkı istersen planları inceleyebilirsin.",
        "generic_next": "Dosyanın açıldığını kontrol edip daha sonra yeniden deneyebilirsin. Sorun devam ederse destek ekibine ulaşabilirsin.",
        "workspace": "Çalışma alanını aç", "plans": "Planları incele", "support": "Destek ekibine ulaş", "reference": "İşlem referansı",
    },
    "en": {
        "title": "We could not finish processing your source",
        "greeting": "Hello{name},",
        "generic": "A problem occurred while processing your source, and this attempt could not be completed.",
        "balance": "The usage allowance needed for this source exceeds the allowance remaining in your account.",
        "limit": "This source exceeds your current plan's usage limit for a single processing job.",
        "quota": "This source exceeds your available allowance or one of your account's per-job source limits.",
        "document": "For PDFs and other documents, minutes are units of usage allowance, not waiting time or processing duration.",
        "required": "Required allowance: {value} minutes",
        "remaining": "Remaining allowance: {value} minutes",
        "maximum": "Allowance limit per job: {value} minutes",
        "quota_next": "Even with a free account, you can try a shorter section or fewer pages within your current limits. If you need more allowance, you can review the available plans.",
        "generic_next": "Check that the file opens, then try again later. If the issue continues, contact support.",
        "workspace": "Open workspace", "plans": "View plans", "support": "Contact support", "reference": "Job reference",
    },
    "de": {
        "title": "Deine Quelle konnte nicht vollständig verarbeitet werden",
        "greeting": "Hallo{name},",
        "generic": "Bei der Verarbeitung deiner Quelle ist ein Problem aufgetreten. Dieser Versuch konnte nicht abgeschlossen werden.",
        "balance": "Das für diese Quelle benötigte Nutzungskontingent übersteigt dein verbleibendes Kontingent.",
        "limit": "Diese Quelle überschreitet das Nutzungslimit deines aktuellen Tarifs für einen einzelnen Auftrag.",
        "quota": "Diese Quelle überschreitet dein verfügbares Kontingent oder eines der Quellenlimits pro Auftrag.",
        "document": "Bei PDFs und anderen Dokumenten sind Minuten Einheiten des Nutzungskontingents, keine Warte- oder Verarbeitungszeit.",
        "required": "Benötigtes Kontingent: {value} Minuten",
        "remaining": "Verbleibendes Kontingent: {value} Minuten",
        "maximum": "Kontingentlimit pro Auftrag: {value} Minuten",
        "quota_next": "Auch mit einem kostenlosen Konto kannst du einen kürzeren Abschnitt oder weniger Seiten innerhalb deiner aktuellen Limits ausprobieren. Wenn du mehr Kontingent benötigst, kannst du dir die Tarife ansehen.",
        "generic_next": "Prüfe, ob sich die Datei öffnen lässt, und versuche es später erneut. Falls das Problem bestehen bleibt, kontaktiere den Support.",
        "workspace": "Arbeitsbereich öffnen", "plans": "Tarife ansehen", "support": "Support kontaktieren", "reference": "Auftragsreferenz",
    },
    "fr": {
        "title": "Le traitement de votre source n’a pas pu être terminé",
        "greeting": "Bonjour{name},",
        "generic": "Un problème est survenu pendant le traitement de votre source. Cette tentative n’a pas pu aboutir.",
        "balance": "Le quota nécessaire pour cette source dépasse le quota restant sur votre compte.",
        "limit": "Cette source dépasse la limite d’utilisation par traitement de votre offre actuelle.",
        "quota": "Cette source dépasse votre quota disponible ou l’une des limites de sources par traitement.",
        "document": "Pour les PDF et autres documents, les minutes sont des unités de quota, et non un temps d’attente ou une durée de traitement.",
        "required": "Quota nécessaire : {value} minutes",
        "remaining": "Quota restant : {value} minutes",
        "maximum": "Limite par traitement : {value} minutes",
        "quota_next": "Même avec un compte gratuit, vous pouvez réessayer avec une section plus courte ou moins de pages, dans vos limites actuelles. Si vous avez besoin de plus de quota, vous pouvez consulter les offres.",
        "generic_next": "Vérifiez que le fichier s’ouvre, puis réessayez plus tard. Si le problème persiste, contactez l’assistance.",
        "workspace": "Ouvrir l’espace de travail", "plans": "Consulter les offres", "support": "Contacter l’assistance", "reference": "Référence du traitement",
    },
    "es": {
        "title": "No se pudo completar el procesamiento de tu fuente",
        "greeting": "Hola{name},",
        "generic": "Ocurrió un problema al procesar tu fuente y este intento no pudo completarse.",
        "balance": "La cuota necesaria para esta fuente supera la cuota restante en tu cuenta.",
        "limit": "Esta fuente supera el límite de uso por tarea de tu plan actual.",
        "quota": "Esta fuente supera tu cuota disponible o uno de los límites de fuentes por tarea.",
        "document": "En PDF y otros documentos, los minutos son unidades de cuota de uso, no tiempo de espera ni duración del procesamiento.",
        "required": "Cuota necesaria: {value} minutos",
        "remaining": "Cuota restante: {value} minutos",
        "maximum": "Límite de cuota por tarea: {value} minutos",
        "quota_next": "Incluso con una cuenta gratuita, puedes probar una sección más corta o menos páginas dentro de tus límites actuales. Si necesitas más cuota, puedes consultar los planes.",
        "generic_next": "Comprueba que el archivo se abre y vuelve a intentarlo más tarde. Si el problema continúa, contacta con soporte.",
        "workspace": "Abrir espacio de trabajo", "plans": "Ver planes", "support": "Contactar con soporte", "reference": "Referencia de la tarea",
    },
    "it": {
        "title": "Non è stato possibile completare l’elaborazione della fonte",
        "greeting": "Ciao{name},",
        "generic": "Si è verificato un problema durante l’elaborazione della fonte. Questo tentativo non è stato completato.",
        "balance": "La quota necessaria per questa fonte supera quella rimasta nel tuo account.",
        "limit": "Questa fonte supera il limite di utilizzo per singola elaborazione del tuo piano attuale.",
        "quota": "Questa fonte supera la quota disponibile o uno dei limiti sulle fonti per singola elaborazione.",
        "document": "Per PDF e altri documenti, i minuti sono unità della quota di utilizzo, non tempi di attesa o di elaborazione.",
        "required": "Quota necessaria: {value} minuti",
        "remaining": "Quota rimanente: {value} minuti",
        "maximum": "Limite di quota per elaborazione: {value} minuti",
        "quota_next": "Anche con un account gratuito puoi riprovare con una sezione più breve o meno pagine, rispettando i limiti attuali. Se ti serve più quota, puoi consultare i piani.",
        "generic_next": "Verifica che il file si apra, poi riprova più tardi. Se il problema persiste, contatta l’assistenza.",
        "workspace": "Apri l’area di lavoro", "plans": "Visualizza i piani", "support": "Contatta l’assistenza", "reference": "Riferimento dell’elaborazione",
    },
    "pt": {
        "title": "Não foi possível concluir o processamento da sua fonte",
        "greeting": "Olá{name},",
        "generic": "Ocorreu um problema durante o processamento da sua fonte e esta tentativa não pôde ser concluída.",
        "balance": "A cota necessária para esta fonte excede a cota restante na sua conta.",
        "limit": "Esta fonte excede o limite de uso por processamento do seu plano atual.",
        "quota": "Esta fonte excede a cota disponível ou um dos limites de fontes por processamento.",
        "document": "Para PDFs e outros documentos, os minutos são unidades da cota de uso, não o tempo de espera ou a duração do processamento.",
        "required": "Cota necessária: {value} minutos",
        "remaining": "Cota restante: {value} minutos",
        "maximum": "Limite de cota por processamento: {value} minutos",
        "quota_next": "Mesmo com uma conta gratuita, você pode tentar uma seção mais curta ou menos páginas dentro dos seus limites atuais. Se precisar de mais cota, pode consultar os planos.",
        "generic_next": "Verifique se o arquivo abre e tente novamente mais tarde. Se o problema continuar, entre em contato com o suporte.",
        "workspace": "Abrir área de trabalho", "plans": "Ver planos", "support": "Falar com o suporte", "reference": "Referência do processamento",
    },
    "ru": {
        "title": "Не удалось завершить обработку источника",
        "greeting": "Здравствуйте{name}!",
        "generic": "При обработке источника возникла проблема. Этот запрос не удалось выполнить до конца.",
        "balance": "Для этого источника требуется больше минут использования, чем осталось в вашем аккаунте.",
        "limit": "Этот источник превышает лимит использования на одно задание в вашем текущем тарифе.",
        "quota": "Этот источник превышает доступный объём использования или один из лимитов источников на задание.",
        "document": "Для PDF и других документов минуты — это единицы объёма использования, а не время ожидания или обработки.",
        "required": "Требуемый объём: {value} мин.",
        "remaining": "Оставшийся объём: {value} мин.",
        "maximum": "Лимит на одно задание: {value} мин.",
        "quota_next": "Даже с бесплатным аккаунтом можно попробовать более короткий фрагмент или меньше страниц в пределах текущих лимитов. Если нужен больший объём, можно посмотреть тарифы.",
        "generic_next": "Убедитесь, что файл открывается, и повторите попытку позже. Если проблема сохраняется, обратитесь в поддержку.",
        "workspace": "Открыть рабочую область", "plans": "Посмотреть тарифы", "support": "Связаться с поддержкой", "reference": "Номер задания",
    },
    "ar": {
        "title": "تعذّر إكمال معالجة مصدرك",
        "greeting": "مرحبًا{name}،",
        "generic": "حدثت مشكلة أثناء معالجة مصدرك ولم تكتمل هذه المحاولة.",
        "balance": "رصيد الاستخدام المطلوب لهذا المصدر يتجاوز الرصيد المتبقي في حسابك.",
        "limit": "يتجاوز هذا المصدر حد الاستخدام لكل عملية في خطتك الحالية.",
        "quota": "يتجاوز هذا المصدر رصيدك المتاح أو أحد حدود المصادر لكل عملية.",
        "document": "في ملفات PDF والمستندات الأخرى، تمثّل الدقائق وحدات من رصيد الاستخدام، وليست وقت الانتظار أو مدة المعالجة.",
        "required": "الرصيد المطلوب: {value} دقيقة",
        "remaining": "الرصيد المتبقي: {value} دقيقة",
        "maximum": "حد الرصيد لكل عملية: {value} دقيقة",
        "quota_next": "حتى مع الحساب المجاني، يمكنك تجربة جزء أقصر أو صفحات أقل ضمن حدودك الحالية. وإذا احتجت إلى رصيد إضافي، يمكنك الاطلاع على الخطط.",
        "generic_next": "تحقّق من إمكانية فتح الملف ثم حاول مجددًا لاحقًا. إذا استمرت المشكلة، تواصل مع الدعم.",
        "workspace": "فتح مساحة العمل", "plans": "عرض الخطط", "support": "التواصل مع الدعم", "reference": "مرجع العملية",
    },
    "zh": {
        "title": "未能完成您的资料处理",
        "greeting": "您好{name}：",
        "generic": "处理您的资料时出现问题，本次处理未能完成。",
        "balance": "处理这份资料所需的使用额度超过了您账户中的剩余额度。",
        "limit": "这份资料超过了您当前套餐的单次处理使用额度上限。",
        "quota": "这份资料超过了您的可用额度或单次处理的某项资料限制。",
        "document": "对于 PDF 和其他文档，“分钟”是使用额度的计量单位，并非等待时间或处理时长。",
        "required": "所需额度：{value} 分钟",
        "remaining": "剩余额度：{value} 分钟",
        "maximum": "单次处理额度上限：{value} 分钟",
        "quota_next": "即使使用免费账户，您也可以在当前限制内尝试较短的章节或较少的页面。如需更多额度，可以查看套餐。",
        "generic_next": "请确认文件能够打开，稍后再试。如果问题持续，请联系支持团队。",
        "workspace": "打开工作区", "plans": "查看套餐", "support": "联系支持", "reference": "任务编号",
    },
    "ja": {
        "title": "資料の処理を完了できませんでした",
        "greeting": "こんにちは{name}。",
        "generic": "資料の処理中に問題が発生し、今回の処理を完了できませんでした。",
        "balance": "この資料に必要な利用枠が、アカウントの残りの利用枠を超えています。",
        "limit": "この資料は、現在のプランの1回の処理あたりの利用上限を超えています。",
        "quota": "この資料は、利用可能な枠または1回の処理あたりの資料制限のいずれかを超えています。",
        "document": "PDFなどの文書では、「分」は利用枠を表す単位です。待ち時間や処理時間を意味するものではありません。",
        "required": "必要な利用枠：{value} 分",
        "remaining": "残りの利用枠：{value} 分",
        "maximum": "1回の処理あたりの利用上限：{value} 分",
        "quota_next": "無料アカウントでも、現在の上限内で短い部分や少ないページ数で再度お試しいただけます。利用枠を増やしたい場合は、プランをご確認ください。",
        "generic_next": "ファイルが開けることを確認し、時間をおいて再度お試しください。問題が続く場合は、サポートにお問い合わせください。",
        "workspace": "ワークスペースを開く", "plans": "プランを見る", "support": "サポートに問い合わせる", "reference": "処理番号",
    },
    "ko": {
        "title": "자료 처리를 완료하지 못했습니다",
        "greeting": "안녕하세요{name}.",
        "generic": "자료를 처리하는 중 문제가 발생하여 이번 처리를 완료하지 못했습니다.",
        "balance": "이 자료에 필요한 사용량이 계정에 남아 있는 사용량을 초과합니다.",
        "limit": "이 자료는 현재 요금제의 작업당 사용량 한도를 초과합니다.",
        "quota": "이 자료는 사용 가능한 잔여량 또는 작업당 자료 제한 중 하나를 초과합니다.",
        "document": "PDF 및 기타 문서에서 '분'은 사용량을 측정하는 단위이며, 대기 시간이나 처리 시간이 아닙니다.",
        "required": "필요한 사용량: {value}분",
        "remaining": "남은 사용량: {value}분",
        "maximum": "작업당 사용량 한도: {value}분",
        "quota_next": "무료 계정에서도 현재 한도 내의 짧은 부분이나 더 적은 페이지로 다시 시도할 수 있습니다. 사용량이 더 필요하면 요금제를 확인해 보세요.",
        "generic_next": "파일이 열리는지 확인한 후 나중에 다시 시도해 주세요. 문제가 계속되면 지원팀에 문의해 주세요.",
        "workspace": "작업 공간 열기", "plans": "요금제 보기", "support": "지원팀에 문의", "reference": "작업 번호",
    },
    "hi": {
        "title": "आपके स्रोत की प्रोसेसिंग पूरी नहीं हो सकी",
        "greeting": "नमस्ते{name},",
        "generic": "आपके स्रोत की प्रोसेसिंग के दौरान समस्या आई और यह प्रयास पूरा नहीं हो सका।",
        "balance": "इस स्रोत के लिए आवश्यक उपयोग कोटा आपके खाते में बचे कोटे से अधिक है।",
        "limit": "यह स्रोत आपके मौजूदा प्लान की प्रति कार्य उपयोग सीमा से अधिक है।",
        "quota": "यह स्रोत आपके उपलब्ध कोटे या प्रति कार्य स्रोत की किसी सीमा से अधिक है।",
        "document": "PDF और दूसरे दस्तावेज़ों के लिए मिनट उपयोग कोटे की इकाई हैं, प्रतीक्षा या प्रोसेसिंग का समय नहीं।",
        "required": "आवश्यक कोटा: {value} मिनट",
        "remaining": "बचा हुआ कोटा: {value} मिनट",
        "maximum": "प्रति कार्य कोटा सीमा: {value} मिनट",
        "quota_next": "मुफ़्त खाते में भी आप मौजूदा सीमाओं के भीतर छोटे हिस्से या कम पन्नों के साथ फिर से कोशिश कर सकते हैं। अधिक कोटा चाहिए तो उपलब्ध प्लान देख सकते हैं।",
        "generic_next": "जाँचें कि फ़ाइल खुल रही है, फिर कुछ समय बाद दोबारा कोशिश करें। समस्या बनी रहे तो सहायता टीम से संपर्क करें।",
        "workspace": "कार्यस्थान खोलें", "plans": "प्लान देखें", "support": "सहायता से संपर्क करें", "reference": "कार्य संदर्भ",
    },
}


def _language(value: object) -> str:
    candidate = str(value or "").strip().lower().replace("_", "-").split("-", 1)[0]
    return candidate if candidate in _COPY else "tr"


def _minutes(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite() or number < 0 or number > 1_000_000_000:
        return None
    return number


def _safe_line(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def render_job_failure_email(
    language: str,
    *,
    error_code: str,
    required_minutes: int | float | None = None,
    remaining_minutes: int | float | None = None,
    max_minutes_per_job: int | float | None = None,
    first_name: str = "",
    job_id: str = "",
    document_mode: bool = False,
) -> dict[str, str]:
    """Render one terminal-error notification without side effects.

    Unknown locales use Turkish, the site's default. Regional variants resolve
    to their supported base language. Allowance numbers are optional and should
    come from structured billing data, never from parsing provider exceptions.
    """
    locale = _language(language)
    copy = _COPY[locale]
    required = _minutes(required_minutes)
    remaining = _minutes(remaining_minutes)
    maximum = _minutes(max_minutes_per_job)
    quota = error_code == "LS-BILL-10"
    reason = "generic"
    if quota:
        reason = "quota"
        if required is not None and remaining is not None and required > remaining:
            reason = "balance"
        elif required is not None and maximum is not None and required > maximum:
            reason = "limit"

    name = _safe_line(first_name, 100)
    greeting = copy["greeting"].format(name=f" {name}" if name else "")
    paragraphs = [greeting, copy[reason]]
    if quota:
        for key, value in (("required", required), ("remaining", remaining), ("maximum", maximum)):
            if value is not None:
                amount = format(value, ".2f").rstrip("0").rstrip(".")
                paragraphs.append(copy[key].format(value=amount))
        if document_mode:
            paragraphs.append(copy["document"])
    paragraphs.append(copy["quota_next" if quota else "generic_next"])

    # A reference helps support locate the job. Constrain it to the opaque job
    # identifier format instead of echoing arbitrary diagnostics or filenames.
    reference = str(job_id or "")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", reference):
        paragraphs.append(f'{copy["reference"]}: {reference}')
    prefix = "" if locale == "tr" else f"/{locale}"
    links = [(copy["workspace"], f"https://lecturesift.com{prefix}/workspace.html")]
    if quota:
        links.append((copy["plans"], f"https://lecturesift.com{prefix}/plans"))
    links.append((copy["support"], f"https://lecturesift.com{prefix}/contact"))

    direction = "rtl" if locale == "ar" else "ltr"
    align = "right" if locale == "ar" else "left"
    body = "".join(f'<p style="margin:0 0 16px;line-height:1.65">{escape(part)}</p>' for part in paragraphs)
    actions = "".join(
        f'<p style="margin:14px 0"><a href="{escape(url, quote=True)}" '
        f'style="color:#264e8a;font-weight:600">{escape(label)}</a></p>'
        for label, url in links
    )
    html = (
        f'<!doctype html><html lang="{locale}" dir="{direction}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
        '<body style="margin:0;background:#f4f6fa;color:#18263a;font-family:Arial,sans-serif">'
        f'<table role="presentation" width="100%" dir="{direction}" cellpadding="0" cellspacing="0">'
        '<tr><td style="padding:24px 12px">'
        f'<div style="max-width:580px;margin:0 auto;background:#fff;border-radius:12px;padding:28px;text-align:{align}">'
        '<p style="margin:0 0 20px;color:#264e8a;font-size:22px;font-weight:700">LectureSift</p>'
        f'<h1 style="margin:0 0 22px;font-size:22px;line-height:1.4">{escape(copy["title"])}</h1>'
        f'{body}{actions}</div></td></tr></table></body></html>'
    )
    text = "\n\n".join(["LectureSift", copy["title"], *paragraphs, *(f"{label}: {url}" for label, url in links)])
    return {"subject": f'LectureSift · {copy["title"]}', "html": html, "text": text}
