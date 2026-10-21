"""Localized copy for the package-expiry reminder emails.

Rendering is pure: it accepts structured subscription facts only. The plan
name comes from the synced site copy and the price from the catalog, both
resolved by the caller at send time, so a renamed or repriced plan is never
spelled out here. Unknown locales use Turkish, the site's default.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import urlencode


SUPPORTED_LANGUAGES = ("tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi")
KINDS = ("3_days", "expiry")

_COPY = {
    "tr": {
        "subject_3_days": "LectureSift paketin 3 gün sonra bitiyor",
        "subject_expiry": "LectureSift paketin bugün bitiyor",
        "title_3_days": "Paketin 3 gün sonra bitiyor",
        "title_expiry": "Paketin bugün bitiyor",
        "greeting": "Merhaba{name},",
        "intro_3_days": "{plan} paketinin dönemi 3 gün sonra, {date} tarihinde sona eriyor.",
        "intro_expiry": "{plan} paketinin dönemi bugün, {date} itibarıyla sona eriyor.",
        "package": "Paket", "ends": "Bitiş", "remaining": "Bu dönemde kalan dakika",
        "price": "Yeniden satın alma fiyatı", "minutes": "{value} dakika",
        "term_monthly": "30 günlük dönem", "term_annual": "yıllık dönem",
        "no_charge": "Hiçbir ödeme otomatik olarak alınmaz: paket kendiliğinden yenilenmez ve kartından ücret çekilmez.",
        "after": "Dönem bittiğinde hesabın ücretsiz plana geçer. Kaldığın yerden devam etmek istersen aynı paketi tek adımda yeniden satın alabilirsin.",
        "early": "Erken yenilemek kayıp değildir: aynı paketi dönem bitmeden satın alırsan yeni dönem mevcut dönemin bittiği an başlar ve o zamana kadar kalan süreni ve dakikalarını kullanmaya devam edersin.",
        "renew": "Paketini yenile",
        "support_text": "Bir sorun olursa bu e-postayı yanıtlayabilir veya destek ekibine yazabilirsin.",
        "support": "Destek ekibine ulaş",
    },
    "en": {
        "subject_3_days": "Your LectureSift package ends in 3 days",
        "subject_expiry": "Your LectureSift package ends today",
        "title_3_days": "Your package ends in 3 days",
        "title_expiry": "Your package ends today",
        "greeting": "Hello{name},",
        "intro_3_days": "The current period of your {plan} package ends in 3 days, on {date}.",
        "intro_expiry": "The current period of your {plan} package ends today, at {date}.",
        "package": "Package", "ends": "Ends", "remaining": "Minutes left in this period",
        "price": "Price to buy again", "minutes": "{value} minutes",
        "term_monthly": "30-day period", "term_annual": "annual period",
        "no_charge": "Nothing is charged automatically: the package does not renew by itself and your card is not debited.",
        "after": "When the period ends, your account moves to the free plan. To carry on where you left off, you can buy the same package again in one step.",
        "early": "Renewing early loses nothing: if you buy the same package before this period ends, the new period starts the moment the current one ends, and you keep using your remaining time and minutes until then.",
        "renew": "Renew your package",
        "support_text": "If anything is wrong, reply to this email or contact support.",
        "support": "Contact support",
    },
    "de": {
        "subject_3_days": "Dein LectureSift-Paket endet in 3 Tagen",
        "subject_expiry": "Dein LectureSift-Paket endet heute",
        "title_3_days": "Dein Paket endet in 3 Tagen",
        "title_expiry": "Dein Paket endet heute",
        "greeting": "Hallo{name},",
        "intro_3_days": "Der aktuelle Zeitraum deines Pakets {plan} endet in 3 Tagen, am {date}.",
        "intro_expiry": "Der aktuelle Zeitraum deines Pakets {plan} endet heute, um {date}.",
        "package": "Paket", "ends": "Ende", "remaining": "Verbleibende Minuten in diesem Zeitraum",
        "price": "Preis für einen erneuten Kauf", "minutes": "{value} Minuten",
        "term_monthly": "30-Tage-Zeitraum", "term_annual": "Jahreszeitraum",
        "no_charge": "Es wird nichts automatisch abgebucht: Das Paket verlängert sich nicht von selbst, und deine Karte wird nicht belastet.",
        "after": "Nach Ablauf des Zeitraums wechselt dein Konto in den kostenlosen Tarif. Um dort weiterzumachen, wo du aufgehört hast, kannst du dasselbe Paket in einem Schritt erneut kaufen.",
        "early": "Frühzeitig zu verlängern kostet dich nichts: Kaufst du dasselbe Paket vor Ablauf dieses Zeitraums, beginnt der neue Zeitraum genau dann, wenn der aktuelle endet, und bis dahin nutzt du deine verbleibende Zeit und Minuten weiter.",
        "renew": "Paket erneuern",
        "support_text": "Falls etwas nicht stimmt, antworte auf diese E-Mail oder kontaktiere den Support.",
        "support": "Support kontaktieren",
    },
    "fr": {
        "subject_3_days": "Votre forfait LectureSift se termine dans 3 jours",
        "subject_expiry": "Votre forfait LectureSift se termine aujourd’hui",
        "title_3_days": "Votre forfait se termine dans 3 jours",
        "title_expiry": "Votre forfait se termine aujourd’hui",
        "greeting": "Bonjour{name},",
        "intro_3_days": "La période en cours de votre forfait {plan} se termine dans 3 jours, le {date}.",
        "intro_expiry": "La période en cours de votre forfait {plan} se termine aujourd’hui, à {date}.",
        "package": "Forfait", "ends": "Fin", "remaining": "Minutes restantes pour cette période",
        "price": "Prix pour racheter le forfait", "minutes": "{value} minutes",
        "term_monthly": "période de 30 jours", "term_annual": "période annuelle",
        "no_charge": "Aucun paiement n’est prélevé automatiquement : le forfait ne se renouvelle pas de lui-même et votre carte n’est pas débitée.",
        "after": "À la fin de la période, votre compte passe à l’offre gratuite. Pour reprendre là où vous en étiez, vous pouvez racheter le même forfait en une étape.",
        "early": "Renouveler en avance ne vous fait rien perdre : si vous achetez le même forfait avant la fin de cette période, la nouvelle période commence au moment où l’actuelle se termine, et vous continuez d’utiliser le temps et les minutes restants d’ici là.",
        "renew": "Renouveler mon forfait",
        "support_text": "En cas de problème, répondez à cet e-mail ou contactez l’assistance.",
        "support": "Contacter l’assistance",
    },
    "es": {
        "subject_3_days": "Tu paquete de LectureSift termina en 3 días",
        "subject_expiry": "Tu paquete de LectureSift termina hoy",
        "title_3_days": "Tu paquete termina en 3 días",
        "title_expiry": "Tu paquete termina hoy",
        "greeting": "Hola{name},",
        "intro_3_days": "El periodo actual de tu paquete {plan} termina en 3 días, el {date}.",
        "intro_expiry": "El periodo actual de tu paquete {plan} termina hoy, a las {date}.",
        "package": "Paquete", "ends": "Fin", "remaining": "Minutos restantes en este periodo",
        "price": "Precio para volver a comprarlo", "minutes": "{value} minutos",
        "term_monthly": "periodo de 30 días", "term_annual": "periodo anual",
        "no_charge": "No se cobra nada automáticamente: el paquete no se renueva por sí solo y no se hace ningún cargo en tu tarjeta.",
        "after": "Cuando termine el periodo, tu cuenta pasará al plan gratuito. Para seguir donde lo dejaste, puedes volver a comprar el mismo paquete en un solo paso.",
        "early": "Renovar antes no te hace perder nada: si compras el mismo paquete antes de que termine este periodo, el nuevo periodo empieza justo cuando termina el actual y hasta entonces sigues usando el tiempo y los minutos que te quedan.",
        "renew": "Renovar mi paquete",
        "support_text": "Si algo no es correcto, responde a este correo o contacta con soporte.",
        "support": "Contactar con soporte",
    },
    "it": {
        "subject_3_days": "Il tuo pacchetto LectureSift termina tra 3 giorni",
        "subject_expiry": "Il tuo pacchetto LectureSift termina oggi",
        "title_3_days": "Il tuo pacchetto termina tra 3 giorni",
        "title_expiry": "Il tuo pacchetto termina oggi",
        "greeting": "Ciao{name},",
        "intro_3_days": "Il periodo corrente del tuo pacchetto {plan} termina tra 3 giorni, il {date}.",
        "intro_expiry": "Il periodo corrente del tuo pacchetto {plan} termina oggi, alle {date}.",
        "package": "Pacchetto", "ends": "Fine", "remaining": "Minuti rimasti in questo periodo",
        "price": "Prezzo per acquistarlo di nuovo", "minutes": "{value} minuti",
        "term_monthly": "periodo di 30 giorni", "term_annual": "periodo annuale",
        "no_charge": "Non viene addebitato nulla automaticamente: il pacchetto non si rinnova da solo e la tua carta non viene addebitata.",
        "after": "Alla fine del periodo il tuo account passa al piano gratuito. Per riprendere da dove avevi lasciato, puoi acquistare di nuovo lo stesso pacchetto in un solo passaggio.",
        "early": "Rinnovare in anticipo non ti fa perdere nulla: se acquisti lo stesso pacchetto prima della fine di questo periodo, il nuovo periodo inizia nel momento in cui termina quello attuale e fino ad allora continui a usare il tempo e i minuti rimanenti.",
        "renew": "Rinnova il pacchetto",
        "support_text": "Se qualcosa non va, rispondi a questa e-mail o contatta l’assistenza.",
        "support": "Contatta l’assistenza",
    },
    "pt": {
        "subject_3_days": "Seu pacote LectureSift termina em 3 dias",
        "subject_expiry": "Seu pacote LectureSift termina hoje",
        "title_3_days": "Seu pacote termina em 3 dias",
        "title_expiry": "Seu pacote termina hoje",
        "greeting": "Olá{name},",
        "intro_3_days": "O período atual do seu pacote {plan} termina em 3 dias, em {date}.",
        "intro_expiry": "O período atual do seu pacote {plan} termina hoje, às {date}.",
        "package": "Pacote", "ends": "Término", "remaining": "Minutos restantes neste período",
        "price": "Preço para comprar novamente", "minutes": "{value} minutos",
        "term_monthly": "período de 30 dias", "term_annual": "período anual",
        "no_charge": "Nada é cobrado automaticamente: o pacote não se renova sozinho e nenhuma cobrança é feita no seu cartão.",
        "after": "Quando o período terminar, sua conta passará para o plano gratuito. Para continuar de onde parou, você pode comprar o mesmo pacote novamente em um só passo.",
        "early": "Renovar antes não faz você perder nada: se comprar o mesmo pacote antes do fim deste período, o novo período começa no momento em que o atual termina, e até lá você continua usando o tempo e os minutos restantes.",
        "renew": "Renovar meu pacote",
        "support_text": "Se algo estiver errado, responda a este e-mail ou fale com o suporte.",
        "support": "Falar com o suporte",
    },
    "ru": {
        "subject_3_days": "Ваш пакет LectureSift заканчивается через 3 дня",
        "subject_expiry": "Ваш пакет LectureSift заканчивается сегодня",
        "title_3_days": "Ваш пакет заканчивается через 3 дня",
        "title_expiry": "Ваш пакет заканчивается сегодня",
        "greeting": "Здравствуйте{name}!",
        "intro_3_days": "Текущий период вашего пакета {plan} заканчивается через 3 дня, {date}.",
        "intro_expiry": "Текущий период вашего пакета {plan} заканчивается сегодня, в {date}.",
        "package": "Пакет", "ends": "Окончание", "remaining": "Оставшиеся минуты в этом периоде",
        "price": "Цена повторной покупки", "minutes": "{value} мин.",
        "term_monthly": "период 30 дней", "term_annual": "годовой период",
        "no_charge": "Ничего не списывается автоматически: пакет не продлевается сам, и средства с вашей карты не снимаются.",
        "after": "Когда период закончится, аккаунт перейдёт на бесплатный тариф. Чтобы продолжить с того же места, вы можете снова купить тот же пакет в один шаг.",
        "early": "Продление заранее ничего не отнимает: если купить тот же пакет до окончания этого периода, новый период начнётся в момент окончания текущего, а до тех пор вы продолжаете пользоваться оставшимся временем и минутами.",
        "renew": "Продлить пакет",
        "support_text": "Если что-то не так, ответьте на это письмо или напишите в поддержку.",
        "support": "Связаться с поддержкой",
    },
    "ar": {
        "subject_3_days": "تنتهي باقتك في LectureSift بعد 3 أيام",
        "subject_expiry": "تنتهي باقتك في LectureSift اليوم",
        "title_3_days": "تنتهي باقتك بعد 3 أيام",
        "title_expiry": "تنتهي باقتك اليوم",
        "greeting": "مرحبًا{name}،",
        "intro_3_days": "تنتهي الفترة الحالية لباقتك {plan} بعد 3 أيام، بتاريخ {date}.",
        "intro_expiry": "تنتهي الفترة الحالية لباقتك {plan} اليوم، في {date}.",
        "package": "الباقة", "ends": "الانتهاء", "remaining": "الدقائق المتبقية في هذه الفترة",
        "price": "سعر الشراء مجددًا", "minutes": "{value} دقيقة",
        "term_monthly": "فترة 30 يومًا", "term_annual": "فترة سنوية",
        "no_charge": "لا يُحصَّل أي مبلغ تلقائيًا: لا تتجدد الباقة من تلقاء نفسها ولا يُخصم أي مبلغ من بطاقتك.",
        "after": "عند انتهاء الفترة ينتقل حسابك إلى الخطة المجانية. لمتابعة ما توقفت عنده، يمكنك شراء الباقة نفسها مجددًا بخطوة واحدة.",
        "early": "التجديد المبكر لا يُفقدك شيئًا: إذا اشتريت الباقة نفسها قبل انتهاء هذه الفترة، تبدأ الفترة الجديدة لحظة انتهاء الفترة الحالية، وتواصل حتى ذلك الحين استخدام الوقت والدقائق المتبقية.",
        "renew": "تجديد الباقة",
        "support_text": "إذا كانت هناك أي مشكلة، يمكنك الرد على هذه الرسالة أو التواصل مع الدعم.",
        "support": "التواصل مع الدعم",
    },
    "zh": {
        "subject_3_days": "你的 LectureSift 套餐将在 3 天后结束",
        "subject_expiry": "你的 LectureSift 套餐今天结束",
        "title_3_days": "你的套餐将在 3 天后结束",
        "title_expiry": "你的套餐今天结束",
        "greeting": "你好{name}，",
        "intro_3_days": "你的 {plan} 套餐当前周期将在 3 天后结束，即 {date}。",
        "intro_expiry": "你的 {plan} 套餐当前周期将于今天 {date} 结束。",
        "package": "套餐", "ends": "结束时间", "remaining": "本周期剩余分钟数",
        "price": "再次购买的价格", "minutes": "{value} 分钟",
        "term_monthly": "30 天周期", "term_annual": "年度周期",
        "no_charge": "不会自动扣款：套餐不会自行续期，也不会从你的银行卡扣费。",
        "after": "周期结束后，你的账户将转为免费套餐。如需从上次停下的地方继续，你可以一步重新购买同一套餐。",
        "early": "提前续订不会有任何损失：如果在本周期结束前购买同一套餐，新周期将在当前周期结束时开始，在此之前你可以继续使用剩余的时间和分钟数。",
        "renew": "续订套餐",
        "support_text": "如有任何问题，请回复此邮件或联系客服。",
        "support": "联系客服",
    },
    "ja": {
        "subject_3_days": "LectureSift のパッケージは3日後に終了します",
        "subject_expiry": "LectureSift のパッケージは本日終了します",
        "title_3_days": "パッケージは3日後に終了します",
        "title_expiry": "パッケージは本日終了します",
        "greeting": "こんにちは{name}",
        "intro_3_days": "ご利用中の {plan} パッケージの現在の期間は、3日後の {date} に終了します。",
        "intro_expiry": "ご利用中の {plan} パッケージの現在の期間は、本日 {date} に終了します。",
        "package": "パッケージ", "ends": "終了", "remaining": "この期間の残り時間（分）",
        "price": "再購入の価格", "minutes": "{value} 分",
        "term_monthly": "30日間の期間", "term_annual": "年間の期間",
        "no_charge": "自動的に請求されることはありません。パッケージは自動更新されず、カードから引き落とされることもありません。",
        "after": "期間が終了すると、アカウントは無料プランに切り替わります。続きから再開したい場合は、同じパッケージをワンステップで再購入できます。",
        "early": "早めに更新しても損はありません。この期間が終わる前に同じパッケージを購入すると、新しい期間は現在の期間が終了した時点で始まり、それまでは残りの期間と時間（分）をそのまま使えます。",
        "renew": "パッケージを更新する",
        "support_text": "問題がある場合は、このメールに返信するか、サポートにお問い合わせください。",
        "support": "サポートに連絡する",
    },
    "ko": {
        "subject_3_days": "LectureSift 패키지가 3일 후 종료됩니다",
        "subject_expiry": "LectureSift 패키지가 오늘 종료됩니다",
        "title_3_days": "패키지가 3일 후 종료됩니다",
        "title_expiry": "패키지가 오늘 종료됩니다",
        "greeting": "안녕하세요{name},",
        "intro_3_days": "{plan} 패키지의 현재 기간이 3일 후인 {date}에 종료됩니다.",
        "intro_expiry": "{plan} 패키지의 현재 기간이 오늘 {date}에 종료됩니다.",
        "package": "패키지", "ends": "종료", "remaining": "이 기간에 남은 분",
        "price": "다시 구매 시 가격", "minutes": "{value}분",
        "term_monthly": "30일 기간", "term_annual": "연간 기간",
        "no_charge": "자동으로 결제되는 금액은 없습니다. 패키지는 자동 갱신되지 않으며 카드에서 금액이 청구되지 않습니다.",
        "after": "기간이 끝나면 계정은 무료 요금제로 전환됩니다. 중단한 곳에서 이어가려면 같은 패키지를 한 번에 다시 구매할 수 있습니다.",
        "early": "미리 갱신해도 손해가 없습니다. 이 기간이 끝나기 전에 같은 패키지를 구매하면 새 기간은 현재 기간이 끝나는 시점에 시작되며, 그때까지 남은 기간과 시간(분)을 계속 사용할 수 있습니다.",
        "renew": "패키지 갱신하기",
        "support_text": "문제가 있으면 이 이메일에 답장하거나 지원팀에 문의하세요.",
        "support": "지원팀에 문의",
    },
    "hi": {
        "subject_3_days": "आपका LectureSift पैकेज 3 दिन में समाप्त हो रहा है",
        "subject_expiry": "आपका LectureSift पैकेज आज समाप्त हो रहा है",
        "title_3_days": "आपका पैकेज 3 दिन में समाप्त हो रहा है",
        "title_expiry": "आपका पैकेज आज समाप्त हो रहा है",
        "greeting": "नमस्ते{name},",
        "intro_3_days": "आपके {plan} पैकेज की वर्तमान अवधि 3 दिन बाद, {date} को समाप्त हो रही है।",
        "intro_expiry": "आपके {plan} पैकेज की वर्तमान अवधि आज, {date} पर समाप्त हो रही है।",
        "package": "पैकेज", "ends": "समाप्ति", "remaining": "इस अवधि में बचे मिनट",
        "price": "दोबारा खरीदने की कीमत", "minutes": "{value} मिनट",
        "term_monthly": "30 दिन की अवधि", "term_annual": "वार्षिक अवधि",
        "no_charge": "कोई भुगतान अपने आप नहीं लिया जाता: पैकेज अपने आप नवीनीकृत नहीं होता और आपके कार्ड से कोई राशि नहीं काटी जाती।",
        "after": "अवधि समाप्त होने पर आपका खाता मुफ़्त प्लान पर चला जाएगा। जहाँ छोड़ा था वहाँ से जारी रखने के लिए आप वही पैकेज एक ही चरण में दोबारा खरीद सकते हैं।",
        "early": "जल्दी नवीनीकरण करने से कुछ नहीं खोता: अगर आप यह अवधि खत्म होने से पहले वही पैकेज खरीदते हैं, तो नई अवधि मौजूदा अवधि खत्म होते ही शुरू होगी और तब तक आप बचा हुआ समय और मिनट इस्तेमाल करते रहेंगे।",
        "renew": "पैकेज नवीनीकृत करें",
        "support_text": "कोई समस्या हो तो इस ईमेल का उत्तर दें या सहायता टीम से संपर्क करें।",
        "support": "सहायता से संपर्क करें",
    },
}

_DECIMAL_COMMA = {"tr", "de", "fr", "es", "it", "pt", "ru"}
_ZERO_DECIMAL_CURRENCIES = {"JPY", "KRW"}
# The seller operates in Türkiye, which has stayed on UTC+3 all year since 2016.
# A fixed offset avoids depending on tzdata being present in the image.
_SELLER_TIME = timezone(timedelta(hours=3))


def _language(value: object) -> str:
    candidate = str(value or "").strip().lower().replace("_", "-").split("-", 1)[0]
    return candidate if candidate in _COPY else "tr"


def _safe_line(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _amount(amount_minor: int, currency: str, locale: str) -> str:
    code = _safe_line(currency, 3).upper()
    minor = max(0, int(amount_minor))
    if code in _ZERO_DECIMAL_CURRENCIES:
        return f"{minor} {code}"
    separator = "," if locale in _DECIMAL_COMMA else "."
    return f"{minor // 100}{separator}{minor % 100:02d} {code}"


def _when(value: datetime) -> str:
    moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return moment.astimezone(_SELLER_TIME).strftime("%Y-%m-%d %H:%M") + " (UTC+03:00)"


def renewal_url(site_url: str, language: str, plan_code: str, interval: str) -> str:
    """The plans page with the same package preselected; login keeps it in ?next=."""
    locale = _language(language)
    base = (site_url or "https://lecturesift.com").rstrip("/")
    prefix = "" if locale == "tr" else f"/{locale}"
    query = urlencode({"plan": _safe_line(plan_code, 32), "interval": "annual" if interval == "annual" else "monthly"})
    return f"{base}{prefix}/plans.html?{query}"


def render_renewal_reminder_email(
    language: str,
    *,
    kind: str,
    plan_code: str,
    plan_name: str,
    interval: str,
    ends_at: datetime,
    remaining_minutes: int | None = None,
    price_minor: int | None = None,
    currency: str = "TRY",
    first_name: str = "",
    site_url: str = "https://lecturesift.com",
) -> dict[str, str]:
    """Render one expiry reminder without side effects."""
    if kind not in KINDS:
        raise ValueError("unknown reminder kind")
    locale = _language(language)
    copy = _COPY[locale]
    name = _safe_line(first_name, 80)
    plan = _safe_line(plan_name, 80) or _safe_line(plan_code, 32).replace("_", " ").title()
    term = copy["term_annual" if interval == "annual" else "term_monthly"]
    when = _when(ends_at)
    rows = [(copy["package"], f"{plan} · {term}"), (copy["ends"], when)]
    if remaining_minutes is not None:
        rows.append((copy["remaining"], copy["minutes"].format(value=max(0, int(remaining_minutes)))))
    if price_minor is not None:
        rows.append((copy["price"], _amount(price_minor, currency, locale)))

    renew_url = renewal_url(site_url, locale, plan_code, interval)
    base = (site_url or "https://lecturesift.com").rstrip("/")
    prefix = "" if locale == "tr" else f"/{locale}"
    support_url = f"{base}{prefix}/contact"
    greeting = copy["greeting"].format(name=f" {name}" if name else "")
    intro = copy[f"intro_{kind}"].format(plan=plan, date=when)
    title = copy[f"title_{kind}"]

    direction = "rtl" if locale == "ar" else "ltr"
    align = "right" if locale == "ar" else "left"

    def paragraph(text: str) -> str:
        return f'<p style="margin:0 0 16px;line-height:1.65">{escape(text)}</p>'

    def link(label: str, url: str) -> str:
        return (f'<p style="margin:10px 0"><a href="{escape(url, quote=True)}" '
                f'style="color:#264e8a;font-weight:600">{escape(label)}</a></p>')

    def button(label: str, url: str) -> str:
        return (f'<p style="margin:8px 0 24px"><a href="{escape(url, quote=True)}" '
                'style="display:inline-block;padding:12px 22px;border-radius:8px;background:#264e8a;'
                f'color:#ffffff;font-weight:600;text-decoration:none">{escape(label)}</a></p>')

    def table(items: list[tuple[str, str]]) -> str:
        cells = "".join(
            f'<tr><td style="padding:8px 12px 8px 0;border-bottom:1px solid #e3e8f0;color:#5a6779;'
            f'vertical-align:top;width:40%;text-align:{align}">{escape(label)}</td>'
            f'<td style="padding:8px 0;border-bottom:1px solid #e3e8f0;font-weight:600;'
            f'vertical-align:top;text-align:{align}">{escape(value)}</td></tr>'
            for label, value in items
        )
        return (f'<table role="presentation" width="100%" dir="{direction}" cellpadding="0" cellspacing="0" '
                f'style="margin:0 0 20px;border-collapse:collapse;font-size:15px">{cells}</table>')

    html = (
        f'<!doctype html><html lang="{locale}" dir="{direction}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
        '<body style="margin:0;background:#f4f6fa;color:#18263a;font-family:Arial,sans-serif">'
        f'<table role="presentation" width="100%" dir="{direction}" cellpadding="0" cellspacing="0">'
        '<tr><td style="padding:24px 12px">'
        f'<div style="max-width:580px;margin:0 auto;background:#fff;border-radius:12px;padding:28px;text-align:{align}">'
        '<p style="margin:0 0 20px;color:#264e8a;font-size:22px;font-weight:700">LectureSift</p>'
        f'<h1 style="margin:0 0 22px;font-size:22px;line-height:1.4">{escape(title)}</h1>'
        f'{paragraph(greeting)}{paragraph(intro)}{table(rows)}'
        f'{paragraph(copy["no_charge"])}{paragraph(copy["after"])}{paragraph(copy["early"])}'
        f'{button(copy["renew"], renew_url)}'
        f'<div style="margin-top:24px">{paragraph(copy["support_text"])}{link(copy["support"], support_url)}</div>'
        '</div></td></tr></table></body></html>'
    )
    sections = [
        "LectureSift", title, greeting, intro,
        "\n".join(f"{label}: {value}" for label, value in rows),
        copy["no_charge"], copy["after"], copy["early"],
        f'{copy["renew"]}: {renew_url}',
        f'{copy["support_text"]}\n{copy["support"]}: {support_url}',
    ]
    return {"subject": copy[f"subject_{kind}"], "html": html, "text": "\n\n".join(sections)}
