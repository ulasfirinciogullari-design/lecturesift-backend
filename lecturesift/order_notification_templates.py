"""Localized order-confirmation copy for completed purchases.

Only structured order facts and the public seller identity are accepted.
Provider responses, card data and billing addresses have no place here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape


SUPPORTED_LANGUAGES = ("tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi")

# Seller rows follow frontend/legal-operator.js: the same fields, in the same
# order and with the same labels as the distance-sales and contact pages.
SELLER_FIELDS = (
    "operator_name", "address", "country", "phone", "email", "tax_id", "tax_office",
    "registration_id", "mersis_id", "trade_registry", "kep_address", "chamber_name",
)

# Keep these independent of frontend or account imports: rendering must not
# perform database reads, contact a translation provider or send an email.
_COPY = {
    "tr": {
        "title": "Siparişin onaylandı",
        "greeting": "Merhaba{name},",
        "intro": "Ödemen onaylandı ve satın aldığın paket hesabına tanımlandı. Sipariş özetin aşağıdadır.",
        "order_number": "Sipariş numarası", "package": "Paket", "amount": "Tutar", "date": "Satın alma tarihi",
        "added": "Hesabına eklenen", "allowance": "Kullanım hakkı", "validity": "Geçerlilik",
        "minutes": "{value} dakika", "credits": "{value} asistan kredisi",
        "allowance_monthly": "30 günlük dönem boyunca {value} dakika", "allowance_annual": "Her ay {value} dakika",
        "validity_value": "{days} gün · {end} tarihine kadar", "starts": "Başlangıç",
        "fixed_term": "Bu, tek seferlik ödemeyle satın alınan {days} günlük sabit dönemli bir pakettir. Otomatik olarak yenilenmez ve senden yeniden ödeme alınmaz.",
        "one_time": "Bu, tek seferlik bir satın almadır; abonelik değildir ve otomatik olarak yenilenmez.",
        "account_text": "Paketini, kalan kullanım hakkını ve siparişlerini Hesabım sayfasında görebilirsin.",
        "account": "Hesabım sayfasını aç",
        "legal_text": "Bu sipariş, Mesafeli Satış Sözleşmesi ile Teslimat, İptal ve İade Şartları kapsamındadır.",
        "distance_sales": "Mesafeli Satış Sözleşmesi", "refund": "Teslimat, İptal ve İade Şartları",
        "support_text": "Bir sorun olursa bu e-postayı yanıtlayabilir veya sipariş numaranla destek ekibine yazabilirsin.",
        "support": "Destek ekibine ulaş", "seller": "Hizmet sağlayıcı bilgileri",
        "plan_credit": "Dakika Paketi", "plan_ad_free": "Kalıcı reklamsız kullanım",
        "plan_test": "1 TL Test Paketi", "plan_assistant": "Asistan kredisi",
    },
    "en": {
        "title": "Your order is confirmed",
        "greeting": "Hello{name},",
        "intro": "Your payment has been confirmed and your purchase has been added to your account. Your order summary is below.",
        "order_number": "Order number", "package": "Package", "amount": "Amount", "date": "Purchase date",
        "added": "Added to your account", "allowance": "Usage allowance", "validity": "Validity",
        "minutes": "{value} minutes", "credits": "{value} assistant credits",
        "allowance_monthly": "{value} minutes for the 30-day period", "allowance_annual": "{value} minutes per month",
        "validity_value": "{days} days, until {end}", "starts": "Starts",
        "fixed_term": "This is a fixed {days}-day package bought with a one-time payment. It does not renew automatically, and you will not be charged again.",
        "one_time": "This is a one-time purchase, not a subscription, and it does not renew automatically.",
        "account_text": "You can see your package, remaining allowance and orders on your account page.",
        "account": "Open my account",
        "legal_text": "This order is governed by the Distance Sales Agreement and the Delivery, Cancellation and Refund Terms.",
        "distance_sales": "Distance Sales Agreement", "refund": "Delivery, Cancellation and Refund Terms",
        "support_text": "If anything is wrong, reply to this email or contact support with your order number.",
        "support": "Contact support", "seller": "Service provider details",
        "plan_credit": "Minute Pack", "plan_ad_free": "Permanent ad-free access",
        "plan_test": "₺1 Payment Test", "plan_assistant": "Assistant credits",
    },
    "de": {
        "title": "Deine Bestellung ist bestätigt",
        "greeting": "Hallo{name},",
        "intro": "Deine Zahlung wurde bestätigt und dein Kauf wurde deinem Konto gutgeschrieben. Hier ist deine Bestellübersicht.",
        "order_number": "Bestellnummer", "package": "Paket", "amount": "Betrag", "date": "Kaufdatum",
        "added": "Deinem Konto hinzugefügt", "allowance": "Nutzungskontingent", "validity": "Gültigkeit",
        "minutes": "{value} Minuten", "credits": "{value} Assistenten-Credits",
        "allowance_monthly": "{value} Minuten für den 30-tägigen Zeitraum", "allowance_annual": "{value} Minuten pro Monat",
        "validity_value": "{days} Tage, bis {end}", "starts": "Beginn",
        "fixed_term": "Dies ist ein Paket mit fester Laufzeit von {days} Tagen, das du mit einer Einmalzahlung gekauft hast. Es verlängert sich nicht automatisch, und es wird keine weitere Zahlung eingezogen.",
        "one_time": "Dies ist ein Einmalkauf, kein Abonnement, und er verlängert sich nicht automatisch.",
        "account_text": "Dein Paket, dein verbleibendes Kontingent und deine Bestellungen findest du in deinem Konto.",
        "account": "Mein Konto öffnen",
        "legal_text": "Für diese Bestellung gelten der Fernabsatzvertrag sowie die Liefer-, Kündigungs- und Erstattungsbedingungen.",
        "distance_sales": "Fernabsatzvertrag", "refund": "Liefer-, Kündigungs- und Erstattungsbedingungen",
        "support_text": "Falls etwas nicht stimmt, antworte auf diese E-Mail oder kontaktiere den Support mit deiner Bestellnummer.",
        "support": "Support kontaktieren", "seller": "Anbieterangaben",
        "plan_credit": "Minutenpaket", "plan_ad_free": "Dauerhaft werbefrei",
        "plan_test": "1-₺-Zahlungstest", "plan_assistant": "Assistenten-Credits",
    },
    "fr": {
        "title": "Votre commande est confirmée",
        "greeting": "Bonjour{name},",
        "intro": "Votre paiement a été confirmé et votre achat a été ajouté à votre compte. Voici le récapitulatif de votre commande.",
        "order_number": "Numéro de commande", "package": "Forfait", "amount": "Montant", "date": "Date d’achat",
        "added": "Ajouté à votre compte", "allowance": "Quota d’utilisation", "validity": "Validité",
        "minutes": "{value} minutes", "credits": "{value} crédits assistant",
        "allowance_monthly": "{value} minutes pour la période de 30 jours", "allowance_annual": "{value} minutes par mois",
        "validity_value": "{days} jours, jusqu’au {end}", "starts": "Début",
        "fixed_term": "Il s’agit d’un forfait à durée fixe de {days} jours, acheté par un paiement unique. Il ne se renouvelle pas automatiquement et aucun nouveau prélèvement ne sera effectué.",
        "one_time": "Il s’agit d’un achat unique, et non d’un abonnement ; il ne se renouvelle pas automatiquement.",
        "account_text": "Vous pouvez consulter votre forfait, votre quota restant et vos commandes sur la page de votre compte.",
        "account": "Ouvrir mon compte",
        "legal_text": "Cette commande est régie par le Contrat de vente à distance et les Conditions de livraison, d’annulation et de remboursement.",
        "distance_sales": "Contrat de vente à distance", "refund": "Conditions de livraison, d’annulation et de remboursement",
        "support_text": "En cas de problème, répondez à cet e-mail ou contactez l’assistance en indiquant votre numéro de commande.",
        "support": "Contacter l’assistance", "seller": "Informations du fournisseur",
        "plan_credit": "Pack de minutes", "plan_ad_free": "Accès sans publicité permanent",
        "plan_test": "Test de paiement à 1 ₺", "plan_assistant": "Crédits assistant",
    },
    "es": {
        "title": "Tu pedido está confirmado",
        "greeting": "Hola{name},",
        "intro": "Tu pago se ha confirmado y tu compra se ha añadido a tu cuenta. A continuación tienes el resumen de tu pedido.",
        "order_number": "Número de pedido", "package": "Paquete", "amount": "Importe", "date": "Fecha de compra",
        "added": "Añadido a tu cuenta", "allowance": "Cuota de uso", "validity": "Validez",
        "minutes": "{value} minutos", "credits": "{value} créditos del asistente",
        "allowance_monthly": "{value} minutos para el periodo de 30 días", "allowance_annual": "{value} minutos al mes",
        "validity_value": "{days} días, hasta el {end}", "starts": "Inicio",
        "fixed_term": "Es un paquete de duración fija de {days} días adquirido con un pago único. No se renueva automáticamente y no se te volverá a cobrar.",
        "one_time": "Es una compra única, no una suscripción, y no se renueva automáticamente.",
        "account_text": "Puedes ver tu paquete, tu cuota restante y tus pedidos en la página de tu cuenta.",
        "account": "Abrir mi cuenta",
        "legal_text": "Este pedido se rige por el Contrato de venta a distancia y las Condiciones de entrega, cancelación y reembolso.",
        "distance_sales": "Contrato de venta a distancia", "refund": "Condiciones de entrega, cancelación y reembolso",
        "support_text": "Si algo no es correcto, responde a este correo o contacta con soporte indicando tu número de pedido.",
        "support": "Contactar con soporte", "seller": "Datos del proveedor",
        "plan_credit": "Paquete de minutos", "plan_ad_free": "Acceso permanente sin anuncios",
        "plan_test": "Prueba de pago de 1 ₺", "plan_assistant": "Créditos del asistente",
    },
    "it": {
        "title": "Il tuo ordine è confermato",
        "greeting": "Ciao{name},",
        "intro": "Il pagamento è stato confermato e l’acquisto è stato aggiunto al tuo account. Ecco il riepilogo dell’ordine.",
        "order_number": "Numero ordine", "package": "Pacchetto", "amount": "Importo", "date": "Data di acquisto",
        "added": "Aggiunto al tuo account", "allowance": "Quota di utilizzo", "validity": "Validità",
        "minutes": "{value} minuti", "credits": "{value} crediti assistente",
        "allowance_monthly": "{value} minuti per il periodo di 30 giorni", "allowance_annual": "{value} minuti al mese",
        "validity_value": "{days} giorni, fino al {end}", "starts": "Inizio",
        "fixed_term": "Si tratta di un pacchetto a durata fissa di {days} giorni acquistato con un pagamento unico. Non si rinnova automaticamente e non ti verrà addebitato altro.",
        "one_time": "Si tratta di un acquisto singolo, non di un abbonamento, e non si rinnova automaticamente.",
        "account_text": "Puoi vedere il tuo pacchetto, la quota rimanente e i tuoi ordini nella pagina del tuo account.",
        "account": "Apri il mio account",
        "legal_text": "Questo ordine è disciplinato dal Contratto di vendita a distanza e dalle Condizioni di consegna, annullamento e rimborso.",
        "distance_sales": "Contratto di vendita a distanza", "refund": "Condizioni di consegna, annullamento e rimborso",
        "support_text": "Se qualcosa non va, rispondi a questa e-mail o contatta l’assistenza indicando il numero dell’ordine.",
        "support": "Contatta l’assistenza", "seller": "Dati del fornitore",
        "plan_credit": "Pacchetto minuti", "plan_ad_free": "Accesso permanente senza pubblicità",
        "plan_test": "Test pagamento da 1 ₺", "plan_assistant": "Crediti assistente",
    },
    "pt": {
        "title": "Seu pedido foi confirmado",
        "greeting": "Olá{name},",
        "intro": "Seu pagamento foi confirmado e sua compra foi adicionada à sua conta. Veja abaixo o resumo do pedido.",
        "order_number": "Número do pedido", "package": "Pacote", "amount": "Valor", "date": "Data da compra",
        "added": "Adicionado à sua conta", "allowance": "Cota de uso", "validity": "Validade",
        "minutes": "{value} minutos", "credits": "{value} créditos do assistente",
        "allowance_monthly": "{value} minutos para o período de 30 dias", "allowance_annual": "{value} minutos por mês",
        "validity_value": "{days} dias, até {end}", "starts": "Início",
        "fixed_term": "Este é um pacote de prazo fixo de {days} dias, comprado com um pagamento único. Ele não é renovado automaticamente e nenhuma nova cobrança será feita.",
        "one_time": "Esta é uma compra única, não uma assinatura, e não é renovada automaticamente.",
        "account_text": "Você pode ver seu pacote, a cota restante e seus pedidos na página da sua conta.",
        "account": "Abrir minha conta",
        "legal_text": "Este pedido é regido pelo Contrato de venda à distância e pelos Termos de entrega, cancelamento e reembolso.",
        "distance_sales": "Contrato de venda à distância", "refund": "Termos de entrega, cancelamento e reembolso",
        "support_text": "Se algo estiver errado, responda a este e-mail ou fale com o suporte informando o número do pedido.",
        "support": "Falar com o suporte", "seller": "Dados do fornecedor",
        "plan_credit": "Pacote de minutos", "plan_ad_free": "Acesso permanente sem anúncios",
        "plan_test": "Teste de pagamento de 1 ₺", "plan_assistant": "Créditos do assistente",
    },
    "ru": {
        "title": "Ваш заказ подтверждён",
        "greeting": "Здравствуйте{name}!",
        "intro": "Ваш платёж подтверждён, покупка добавлена в ваш аккаунт. Ниже — сводка заказа.",
        "order_number": "Номер заказа", "package": "Пакет", "amount": "Сумма", "date": "Дата покупки",
        "added": "Добавлено в аккаунт", "allowance": "Объём использования", "validity": "Срок действия",
        "minutes": "{value} мин.", "credits": "Кредиты ассистента: {value}",
        "allowance_monthly": "{value} мин. на 30-дневный период", "allowance_annual": "{value} мин. в месяц",
        "validity_value": "{days} дн., до {end}", "starts": "Начало",
        "fixed_term": "Это пакет с фиксированным сроком {days} дн., купленный разовым платежом. Он не продлевается автоматически, повторных списаний не будет.",
        "one_time": "Это разовая покупка, а не подписка; она не продлевается автоматически.",
        "account_text": "Пакет, оставшийся объём и заказы можно посмотреть на странице аккаунта.",
        "account": "Открыть мой аккаунт",
        "legal_text": "Этот заказ регулируется Договором дистанционной продажи и Условиями предоставления, отмены и возврата.",
        "distance_sales": "Договор дистанционной продажи", "refund": "Условия предоставления, отмены и возврата",
        "support_text": "Если что-то не так, ответьте на это письмо или напишите в поддержку, указав номер заказа.",
        "support": "Связаться с поддержкой", "seller": "Сведения о поставщике",
        "plan_credit": "Пакет минут", "plan_ad_free": "Навсегда без рекламы",
        "plan_test": "Тестовый платёж на 1 ₺", "plan_assistant": "Кредиты ассистента",
    },
    "ar": {
        "title": "تم تأكيد طلبك",
        "greeting": "مرحبًا{name}،",
        "intro": "تم تأكيد دفعتك وإضافة مشترياتك إلى حسابك. تجد ملخص طلبك أدناه.",
        "order_number": "رقم الطلب", "package": "الباقة", "amount": "المبلغ", "date": "تاريخ الشراء",
        "added": "المضاف إلى حسابك", "allowance": "رصيد الاستخدام", "validity": "مدة الصلاحية",
        "minutes": "{value} دقيقة", "credits": "رصيد المساعد: {value}",
        "allowance_monthly": "{value} دقيقة لفترة 30 يومًا", "allowance_annual": "{value} دقيقة شهريًا",
        "validity_value": "{days} يومًا، حتى {end}", "starts": "البداية",
        "fixed_term": "هذه باقة محددة المدة لمدة {days} يومًا، تم شراؤها بدفعة واحدة. لا تتجدد تلقائيًا ولن يتم تحصيل أي مبلغ آخر منك.",
        "one_time": "هذه عملية شراء لمرة واحدة وليست اشتراكًا، ولا تتجدد تلقائيًا.",
        "account_text": "يمكنك الاطلاع على باقتك ورصيدك المتبقي وطلباتك في صفحة حسابك.",
        "account": "فتح حسابي",
        "legal_text": "يخضع هذا الطلب لعقد البيع عن بُعد ولشروط التسليم والإلغاء والاسترداد.",
        "distance_sales": "عقد البيع عن بُعد", "refund": "شروط التسليم والإلغاء والاسترداد",
        "support_text": "إذا كانت هناك أي مشكلة، يمكنك الرد على هذه الرسالة أو التواصل مع الدعم مع ذكر رقم طلبك.",
        "support": "التواصل مع الدعم", "seller": "بيانات مزود الخدمة",
        "plan_credit": "حزمة دقائق", "plan_ad_free": "استخدام دائم بلا إعلانات",
        "plan_test": "اختبار دفع بقيمة 1 ₺", "plan_assistant": "رصيد المساعد",
    },
    "zh": {
        "title": "您的订单已确认",
        "greeting": "您好{name}：",
        "intro": "您的付款已确认，所购内容已添加到您的账户。订单摘要如下。",
        "order_number": "订单号", "package": "套餐", "amount": "金额", "date": "购买日期",
        "added": "已添加到账户", "allowance": "使用额度", "validity": "有效期",
        "minutes": "{value} 分钟", "credits": "{value} 助手额度",
        "allowance_monthly": "30 天期限内 {value} 分钟", "allowance_annual": "每月 {value} 分钟",
        "validity_value": "{days} 天，至 {end}", "starts": "开始日期",
        "fixed_term": "这是通过一次性付款购买的 {days} 天固定期限套餐，不会自动续订，也不会再次向您收费。",
        "one_time": "这是一次性购买，不是订阅，不会自动续订。",
        "account_text": "您可以在账户页面查看套餐、剩余额度和订单。",
        "account": "打开我的账户",
        "legal_text": "本订单受《远程销售协议》及《交付、取消与退款条款》约束。",
        "distance_sales": "远程销售协议", "refund": "交付、取消与退款条款",
        "support_text": "如有任何问题，请直接回复此邮件，或提供订单号联系支持团队。",
        "support": "联系支持", "seller": "服务商信息",
        "plan_credit": "分钟包", "plan_ad_free": "永久免广告",
        "plan_test": "1 ₺ 支付测试", "plan_assistant": "助手额度",
    },
    "ja": {
        "title": "ご注文が確定しました",
        "greeting": "こんにちは{name}。",
        "intro": "お支払いが確認され、ご購入内容がアカウントに追加されました。ご注文の概要は以下のとおりです。",
        "order_number": "注文番号", "package": "パッケージ", "amount": "金額", "date": "購入日時",
        "added": "アカウントに追加", "allowance": "利用枠", "validity": "有効期間",
        "minutes": "{value} 分", "credits": "アシスタントクレジット {value}",
        "allowance_monthly": "30日間の期間内で {value} 分", "allowance_annual": "毎月 {value} 分",
        "validity_value": "{days}日間（{end} まで）", "starts": "開始日",
        "fixed_term": "これは一度のお支払いで購入した{days}日間の固定期間パッケージです。自動更新されず、追加で請求されることはありません。",
        "one_time": "これは1回限りの購入で、サブスクリプションではありません。自動更新されません。",
        "account_text": "パッケージ、残りの利用枠、注文履歴はアカウントページで確認できます。",
        "account": "マイアカウントを開く",
        "legal_text": "このご注文には、通信販売契約および提供・解約・返金条件が適用されます。",
        "distance_sales": "通信販売契約", "refund": "提供・解約・返金条件",
        "support_text": "問題がある場合は、このメールに返信するか、注文番号を添えてサポートにお問い合わせください。",
        "support": "サポートに問い合わせる", "seller": "サービス提供者情報",
        "plan_credit": "処理時間パック", "plan_ad_free": "広告を永久に非表示",
        "plan_test": "1 ₺ 決済テスト", "plan_assistant": "アシスタントクレジット",
    },
    "ko": {
        "title": "주문이 확정되었습니다",
        "greeting": "안녕하세요{name}.",
        "intro": "결제가 확인되어 구매하신 상품이 계정에 추가되었습니다. 주문 요약은 아래와 같습니다.",
        "order_number": "주문 번호", "package": "패키지", "amount": "금액", "date": "구매 일시",
        "added": "계정에 추가됨", "allowance": "사용량", "validity": "유효 기간",
        "minutes": "{value}분", "credits": "도우미 크레딧 {value}",
        "allowance_monthly": "30일 기간 동안 {value}분", "allowance_annual": "매월 {value}분",
        "validity_value": "{days}일, {end}까지", "starts": "시작일",
        "fixed_term": "이 패키지는 1회 결제로 구매한 {days}일 고정 기간 패키지입니다. 자동으로 갱신되지 않으며 추가 요금이 청구되지 않습니다.",
        "one_time": "이 구매는 1회성 구매로, 구독이 아니며 자동으로 갱신되지 않습니다.",
        "account_text": "패키지, 남은 사용량, 주문 내역은 내 계정 페이지에서 확인할 수 있습니다.",
        "account": "내 계정 열기",
        "legal_text": "이 주문에는 전자상거래 계약과 서비스 제공·취소·환불 조건이 적용됩니다.",
        "distance_sales": "전자상거래 계약", "refund": "서비스 제공·취소·환불 조건",
        "support_text": "문제가 있으면 이 이메일에 회신하거나 주문 번호와 함께 지원팀에 문의해 주세요.",
        "support": "지원팀에 문의", "seller": "서비스 제공자 정보",
        "plan_credit": "시간 팩", "plan_ad_free": "영구 광고 제거",
        "plan_test": "1 ₺ 결제 테스트", "plan_assistant": "도우미 크레딧",
    },
    "hi": {
        "title": "आपका ऑर्डर कन्फ़र्म हो गया है",
        "greeting": "नमस्ते{name},",
        "intro": "आपके भुगतान की पुष्टि हो गई है और आपकी खरीदारी आपके खाते में जोड़ दी गई है। आपके ऑर्डर का सारांश नीचे है।",
        "order_number": "ऑर्डर नंबर", "package": "पैकेज", "amount": "राशि", "date": "खरीद की तारीख",
        "added": "आपके खाते में जोड़ा गया", "allowance": "उपयोग कोटा", "validity": "वैधता",
        "minutes": "{value} मिनट", "credits": "{value} सहायक क्रेडिट",
        "allowance_monthly": "30 दिन की अवधि के लिए {value} मिनट", "allowance_annual": "हर महीने {value} मिनट",
        "validity_value": "{days} दिन, {end} तक", "starts": "शुरुआत",
        "fixed_term": "यह एक बार के भुगतान से खरीदा गया {days} दिन की निश्चित अवधि वाला पैकेज है। यह अपने आप रिन्यू नहीं होता और आपसे दोबारा शुल्क नहीं लिया जाएगा।",
        "one_time": "यह एक बार की खरीद है, सदस्यता नहीं, और यह अपने आप रिन्यू नहीं होती।",
        "account_text": "आप अपना पैकेज, बचा हुआ कोटा और ऑर्डर अपने खाते के पेज पर देख सकते हैं।",
        "account": "मेरा खाता खोलें",
        "legal_text": "यह ऑर्डर दूरस्थ बिक्री समझौते और डिलीवरी, रद्दीकरण और रिफंड की शर्तों के अधीन है।",
        "distance_sales": "दूरस्थ बिक्री समझौता", "refund": "डिलीवरी, रद्दीकरण और रिफंड की शर्तें",
        "support_text": "अगर कुछ गलत हो, तो इस ईमेल का जवाब दें या अपने ऑर्डर नंबर के साथ सहायता टीम से संपर्क करें।",
        "support": "सहायता से संपर्क करें", "seller": "सेवा प्रदाता विवरण",
        "plan_credit": "मिनट पैक", "plan_ad_free": "स्थायी विज्ञापन-मुक्त उपयोग",
        "plan_test": "1 ₺ भुगतान परीक्षण", "plan_assistant": "सहायक क्रेडिट",
    },
}

# Labels copied from the legal.* keys in frontend/i18n.js, in SELLER_FIELDS order.
_SELLER_LABELS = {
    "tr": ("Unvan / ad", "Açık adres", "Ülke", "Telefon", "E-posta", "Vergi kimliği", "Vergi dairesi",
           "Sicil / kayıt", "MERSİS numarası", "Ticaret sicili", "KEP adresi", "Meslek odası"),
    "en": ("Legal name", "Registered address", "Country", "Phone", "Email", "Tax ID", "Tax office",
           "Registration ID", "MERSIS number", "Trade registry", "Registered e-delivery address (KEP)",
           "Professional chamber"),
    "de": ("Name / Firma", "Anschrift", "Land", "Telefon", "E-Mail", "Steuernummer", "Finanzamt",
           "Registerangabe", "MERSIS-Nummer", "Handelsregister", "Registrierte E-Zustelladresse (KEP)",
           "Berufskammer"),
    "fr": ("Nom légal", "Adresse", "Pays", "Téléphone", "E-mail", "Identifiant fiscal", "Centre des impôts",
           "Numéro d’immatriculation", "Numéro MERSIS", "Registre du commerce",
           "Adresse électronique enregistrée (KEP)", "Chambre professionnelle"),
    "es": ("Nombre legal", "Domicilio", "País", "Teléfono", "Correo electrónico", "Identificación fiscal",
           "Oficina tributaria", "Registro", "Número MERSIS", "Registro mercantil",
           "Dirección electrónica registrada (KEP)", "Cámara profesional"),
    "it": ("Denominazione legale", "Indirizzo", "Paese", "Telefono", "E-mail", "Codice fiscale",
           "Ufficio delle imposte", "Numero di registrazione", "Numero MERSIS", "Registro delle imprese",
           "Indirizzo elettronico registrato (KEP)", "Ordine professionale"),
    "pt": ("Nome legal", "Endereço", "País", "Telefone", "E-mail", "Identificação fiscal", "Repartição fiscal",
           "Registro", "Número MERSIS", "Registro comercial", "Endereço eletrônico registrado (KEP)",
           "Entidade profissional"),
    "ru": ("Юридическое имя", "Адрес", "Страна", "Телефон", "Эл. почта", "Налоговый номер",
           "Налоговая инспекция", "Регистрационный номер", "Номер MERSIS", "Торговый реестр",
           "Зарегистрированный электронный адрес (KEP)", "Профессиональная палата"),
    "ar": ("الاسم القانوني", "العنوان", "الدولة", "الهاتف", "البريد الإلكتروني", "الرقم الضريبي", "مكتب الضرائب",
           "رقم التسجيل", "رقم MERSIS", "السجل التجاري", "عنوان التسليم الإلكتروني المسجل (KEP)", "الغرفة المهنية"),
    "zh": ("法定名称", "注册地址", "国家/地区", "电话", "电子邮箱", "税号", "税务机关",
           "注册号", "MERSIS 编号", "商业登记", "注册电子送达地址（KEP）", "行业协会"),
    "ja": ("事業者名", "所在地", "国", "電話", "メール", "税務番号", "税務署",
           "登録番号", "MERSIS番号", "商業登記", "登録電子送達アドレス（KEP）", "職能団体"),
    "ko": ("법적 명칭", "주소", "국가", "전화", "이메일", "세금 식별번호", "세무서",
           "등록번호", "MERSIS 번호", "상업 등기", "등록 전자송달 주소(KEP)", "직능 단체"),
    "hi": ("कानूनी नाम", "पता", "देश", "फ़ोन", "ईमेल", "कर पहचान", "कर कार्यालय",
           "पंजीकरण संख्या", "MERSIS संख्या", "व्यापार रजिस्ट्री", "पंजीकृत ई-डिलीवरी पता (KEP)", "पेशेवर चैंबर"),
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


def _count(value: object) -> str:
    return str(max(0, int(value or 0)))


def _amount(amount_minor: int, currency: str, locale: str) -> str:
    code = _safe_line(currency, 3).upper()
    minor = max(0, int(amount_minor))
    if code in _ZERO_DECIMAL_CURRENCIES:
        return f"{minor} {code}"
    separator = "," if locale in _DECIMAL_COMMA else "."
    return f"{minor // 100}{separator}{minor % 100:02d} {code}"


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _when(value: datetime) -> str:
    moment = _utc(value)
    return moment.astimezone(_SELLER_TIME).strftime("%Y-%m-%d %H:%M") + " (UTC+03:00)"


def _package_name(copy: dict, plan_code: str, assistant_credits: int) -> str:
    if plan_code in {"credit", "ad_free", "test"}:
        return copy[f"plan_{plan_code}"]
    if plan_code.startswith("ai_"):
        return f'{copy["plan_assistant"]} ({_count(assistant_credits)})'
    return _safe_line(plan_code, 32).replace("_", " ").title()


def render_order_confirmation_email(
    language: str,
    *,
    reference: str,
    plan_code: str,
    plan_kind: str,
    interval: str,
    amount_minor: int,
    currency: str,
    purchased_at: datetime,
    minutes: int | None = None,
    assistant_credits: int = 0,
    ends_at: datetime | None = None,
    starts_at: datetime | None = None,
    first_name: str = "",
    seller: dict | None = None,
    site_url: str = "https://lecturesift.com",
) -> dict[str, str]:
    """Render one order confirmation without side effects.

    Unknown locales use Turkish, the site's default. Regional variants resolve
    to their supported base language. Fixed-term packages state their 30 or
    365 days and that they never renew; other purchases are one-time.
    """
    locale = _language(language)
    copy = _COPY[locale]
    name = _safe_line(first_name, 80)
    rows = [
        (copy["order_number"], _safe_line(reference, 64)),
        (copy["package"], _package_name(copy, plan_code, assistant_credits)),
        (copy["amount"], _amount(amount_minor, currency, locale)),
        (copy["date"], _when(purchased_at)),
    ]
    days = 365 if interval == "annual" else 30
    note = ""
    if plan_kind == "subscription":
        if minutes:
            allowance = copy["allowance_annual" if interval == "annual" else "allowance_monthly"]
            rows.append((copy["allowance"], allowance.format(value=_count(minutes))))
        if starts_at is not None and _utc(starts_at) > _utc(purchased_at) + timedelta(minutes=1):
            # An early renewal of the same package starts when the current period ends.
            rows.append((copy["starts"], _when(starts_at)))
        if ends_at is not None:
            rows.append((copy["validity"], copy["validity_value"].format(days=days, end=_when(ends_at))))
        note = copy["fixed_term"].format(days=days)
    elif plan_kind == "one_time":
        if plan_code == "ad_free":
            rows.append((copy["added"], copy["plan_ad_free"]))
        elif minutes:
            rows.append((copy["added"], copy["minutes"].format(value=_count(minutes))))
        elif assistant_credits:
            rows.append((copy["added"], copy["credits"].format(value=_count(assistant_credits))))
        note = copy["one_time"]

    identity = seller or {}
    seller_rows = [
        (label, _safe_line(identity.get(field), 300))
        for field, label in zip(SELLER_FIELDS, _SELLER_LABELS[locale])
        if identity.get("configured") and _safe_line(identity.get(field), 300)
    ]

    base = (site_url or "https://lecturesift.com").rstrip("/")
    prefix = "" if locale == "tr" else f"/{locale}"
    account_url = f"{base}{prefix}/account.html"
    legal_links = [
        (copy["distance_sales"], f"{base}{prefix}/distance-sales"),
        (copy["refund"], f"{base}{prefix}/refund"),
    ]
    support_url = f"{base}{prefix}/contact"
    greeting = copy["greeting"].format(name=f" {name}" if name else "")

    direction = "rtl" if locale == "ar" else "ltr"
    align = "right" if locale == "ar" else "left"

    def paragraph(text: str) -> str:
        return f'<p style="margin:0 0 16px;line-height:1.65">{escape(text)}</p>'

    def link(label: str, url: str) -> str:
        return (f'<p style="margin:10px 0"><a href="{escape(url, quote=True)}" '
                f'style="color:#264e8a;font-weight:600">{escape(label)}</a></p>')

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

    seller_html = ""
    if seller_rows:
        seller_html = (f'<h2 style="margin:24px 0 10px;font-size:17px">{escape(copy["seller"])}</h2>'
                       f'{table(seller_rows)}')
    html = (
        f'<!doctype html><html lang="{locale}" dir="{direction}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
        '<body style="margin:0;background:#f4f6fa;color:#18263a;font-family:Arial,sans-serif">'
        f'<table role="presentation" width="100%" dir="{direction}" cellpadding="0" cellspacing="0">'
        '<tr><td style="padding:24px 12px">'
        f'<div style="max-width:580px;margin:0 auto;background:#fff;border-radius:12px;padding:28px;text-align:{align}">'
        '<p style="margin:0 0 20px;color:#264e8a;font-size:22px;font-weight:700">LectureSift</p>'
        f'<h1 style="margin:0 0 22px;font-size:22px;line-height:1.4">{escape(copy["title"])}</h1>'
        f'{paragraph(greeting)}{paragraph(copy["intro"])}{table(rows)}'
        f'{paragraph(note) if note else ""}{paragraph(copy["account_text"])}{link(copy["account"], account_url)}'
        f'{seller_html}'
        f'<div style="margin-top:24px">{paragraph(copy["legal_text"])}'
        f'{"".join(link(label, url) for label, url in legal_links)}</div>'
        f'<div style="margin-top:24px">{paragraph(copy["support_text"])}{link(copy["support"], support_url)}</div>'
        '</div></td></tr></table></body></html>'
    )
    sections = [
        "LectureSift", copy["title"], greeting, copy["intro"],
        "\n".join(f"{label}: {value}" for label, value in rows),
        *([note] if note else []),
        f'{copy["account_text"]}\n{copy["account"]}: {account_url}',
        *([copy["seller"] + "\n" + "\n".join(f"{label}: {value}" for label, value in seller_rows)]
          if seller_rows else []),
        copy["legal_text"] + "\n" + "\n".join(f"{label}: {url}" for label, url in legal_links),
        f'{copy["support_text"]}\n{copy["support"]}: {support_url}',
    ]
    subject = f'LectureSift · {copy["title"]} · {_safe_line(reference, 64)}'
    return {"subject": subject, "html": html, "text": "\n\n".join(sections)}
