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

REFUND_REQUEST_COPY = (
    "“Ödeme, iptal veya iade” konusunu seç; hesap e-postanı ve varsa sipariş referansını yaz. "
    "Kart numarası, parola veya doğrulama kodu gönderme."
)
# Reviewed safety instructions take precedence over cached machine translations
# on every catalog write, including normalization of an existing catalog.
SENSITIVE_TRANSLATIONS = {
    REFUND_REQUEST_COPY: [
        REFUND_REQUEST_COPY,
        "Select “Payment, cancellation or refund”; enter your account email and order reference, if available. Do not send card numbers, passwords or verification codes.",
        "Wähle „Zahlung, Stornierung oder Rückerstattung“; gib deine Konto-E-Mail-Adresse und, falls vorhanden, die Bestellreferenz an. Sende keine Kartennummern, Passwörter oder Bestätigungscodes.",
        "Sélectionnez « Paiement, annulation ou remboursement » ; indiquez l’adresse e-mail de votre compte et, si disponible, la référence de commande. N’envoyez pas de numéros de carte, de mots de passe ni de codes de vérification.",
        "Selecciona «Pago, cancelación o reembolso»; indica el correo electrónico de tu cuenta y la referencia del pedido, si la tienes. No envíes números de tarjeta, contraseñas ni códigos de verificación.",
        "Seleziona «Pagamento, cancellazione o rimborso»; indica l’e-mail del tuo account e il riferimento dell’ordine, se disponibile. Non inviare numeri di carta, password o codici di verifica.",
        "Selecione «Pagamento, cancelamento ou reembolso»; informe o e-mail da sua conta e a referência do pedido, se disponível. Não envie números de cartão, senhas ou códigos de verificação.",
        "Выберите «Оплата, отмена или возврат»; укажите электронную почту аккаунта и номер заказа, если он есть. Не отправляйте номера карт, пароли или коды подтверждения.",
        "اختر «الدفع أو الإلغاء أو استرداد الأموال»؛ اكتب البريد الإلكتروني لحسابك ورقم الطلب إن توفر. لا ترسل أرقام البطاقات أو كلمات المرور أو رموز التحقق.",
        "选择“付款、取消或退款”；填写账户邮箱和订单编号（如有）。请勿发送卡号、密码或验证码。",
        "「お支払い、キャンセル、または返金」を選び、アカウントのメールアドレスと注文番号（ある場合）を入力してください。カード番号、パスワード、認証コードは送信しないでください。",
        "‘결제, 취소 또는 환불’을 선택하고 계정 이메일과 주문 번호가 있다면 입력하세요. 카드 번호, 비밀번호 또는 인증 코드는 보내지 마세요.",
        "“भुगतान, रद्द करना या रिफ़ंड” चुनें; अपने खाते का ईमेल और उपलब्ध होने पर ऑर्डर संदर्भ लिखें। कार्ड नंबर, पासवर्ड या सत्यापन कोड न भेजें।",
    ],
}

# Curated admin/accounting copy remains available when the development-time
# translation endpoint is unavailable or rate-limited. Keep the same language
# order as LANGUAGES and review financial terminology rather than accepting a
# blind machine translation.
CURATED_TRANSLATIONS = {
    **SENSITIVE_TRANSLATIONS,
    "BEKLEYEN ÖDÜLLER": ["BEKLEYEN ÖDÜLLER", "PENDING REWARDS", "AUSSTEHENDE BELOHNUNGEN", "RÉCOMPENSES EN ATTENTE", "RECOMPENSAS PENDIENTES", "RICOMPENSE IN ATTESA", "RECOMPENSAS PENDENTES", "ОЖИДАЮЩИЕ НАГРАДЫ", "المكافآت المعلقة", "待处理奖励", "保留中の報酬", "대기 중인 보상", "लंबित पुरस्कार"],
    "Bu işlem yalnız LectureSift içindeki davet kancasını yeniden çalıştırır; ödeme sağlayıcısı mutabakatını yapmaz ve tek başına ödül vermez.": ["Bu işlem yalnız LectureSift içindeki davet kancasını yeniden çalıştırır; ödeme sağlayıcısı mutabakatını yapmaz ve tek başına ödül vermez.", "This action reruns only the referral hook inside LectureSift; it does not reconcile the payment provider or grant a reward by itself.", "Diese Aktion führt nur den Empfehlungs-Hook innerhalb von LectureSift erneut aus; sie gleicht den Zahlungsanbieter nicht ab und gewährt allein keine Belohnung.", "Cette action relance uniquement le traitement de parrainage interne à LectureSift ; elle ne rapproche pas le prestataire de paiement et n’accorde aucune récompense à elle seule.", "Esta acción solo vuelve a ejecutar el proceso de referidos interno de LectureSift; no concilia el proveedor de pagos ni concede una recompensa por sí sola.", "Questa azione riesegue solo il processo di invito interno di LectureSift; non riconcilia il fornitore di pagamento e non assegna da sola alcuna ricompensa.", "Esta ação apenas executa novamente o processo de indicação interno do LectureSift; não concilia o provedor de pagamento nem concede uma recompensa por si só.", "Это действие только повторно запускает внутреннюю обработку приглашения LectureSift; оно не сверяет данные с платёжным провайдером и само по себе не выдаёт награду.", "يعيد هذا الإجراء تشغيل معالجة الدعوة الداخلية في LectureSift فقط؛ ولا يُجري مطابقة مع مزوّد الدفع ولا يمنح مكافأة بمفرده.", "此操作只会重新运行 LectureSift 内部的邀请处理；不会与支付服务商对账，也不会单独发放奖励。", "この操作は LectureSift 内部の招待処理だけを再実行します。決済事業者との照合は行わず、この操作だけで報酬を付与することもありません。", "이 작업은 LectureSift 내부 추천 처리만 다시 실행합니다. 결제 제공업체와 대사하지 않으며 이 작업만으로 보상을 지급하지 않습니다.", "यह कार्रवाई केवल LectureSift के अंदर रेफ़रल प्रक्रिया को दोबारा चलाती है; यह भुगतान प्रदाता से मिलान नहीं करती और अपने आप कोई पुरस्कार नहीं देती।"],
    "DAVET PROGRAMI": ["DAVET PROGRAMI", "REFERRAL PROGRAM", "EMPFEHLUNGSPROGRAMM", "PROGRAMME DE PARRAINAGE", "PROGRAMA DE INVITACIONES", "PROGRAMMA INVITI", "PROGRAMA DE INDICAÇÕES", "РЕФЕРАЛЬНАЯ ПРОГРАММА", "برنامج الدعوات", "邀请计划", "招待プログラム", "추천 프로그램", "रेफ़रल प्रोग्राम"],
    "Eksik davet kaydını yeniden işle": ["Eksik davet kaydını yeniden işle", "Reprocess a missing referral record", "Fehlenden Empfehlungsdatensatz erneut verarbeiten", "Retraiter un enregistrement de parrainage manquant", "Volver a procesar un registro de invitación faltante", "Rielabora un record di invito mancante", "Reprocessar um registro de indicação ausente", "Повторно обработать отсутствующую запись о приглашении", "إعادة معالجة سجل دعوة مفقود", "重新处理缺失的邀请记录", "欠落している招待記録を再処理", "누락된 추천 기록 다시 처리", "गुम रेफ़रल रिकॉर्ड को दोबारा प्रोसेस करें"],
    "İç davet kaydını uzlaştır": ["İç davet kaydını uzlaştır", "Reconcile internal referral record", "Internen Empfehlungsdatensatz abgleichen", "Rapprocher l’enregistrement de parrainage interne", "Conciliar el registro interno de invitación", "Riconcilia il record di invito interno", "Conciliar o registro interno de indicação", "Сверить внутреннюю запись о приглашении", "مطابقة سجل الدعوة الداخلي", "核对内部邀请记录", "内部の招待記録を照合", "내부 추천 기록 대사", "अंदरूनी रेफ़रल रिकॉर्ड का मिलान करें"],
    "İÇ KAYIT UZLAŞTIRMASI": ["İÇ KAYIT UZLAŞTIRMASI", "INTERNAL RECORD RECONCILIATION", "INTERNE DATENABSTIMMUNG", "RAPPROCHEMENT DES ENREGISTREMENTS INTERNES", "CONCILIACIÓN DE REGISTROS INTERNOS", "RICONCILIAZIONE DEI RECORD INTERNI", "CONCILIAÇÃO DE REGISTROS INTERNOS", "СВЕРКА ВНУТРЕННИХ ЗАПИСЕЙ", "مطابقة السجلات الداخلية", "内部记录核对", "内部記録の照合", "내부 기록 대사", "अंदरूनी रिकॉर्ड मिलान"],
    "İşlem amacı": ["İşlem amacı", "Operation purpose", "Zweck des Vorgangs", "Objet de l’opération", "Finalidad de la operación", "Scopo dell’operazione", "Finalidade da operação", "Назначение операции", "غرض العملية", "操作目的", "操作の目的", "작업 목적", "कार्रवाई का उद्देश्य"],
    "Mutabakat ve ödüller": ["Mutabakat ve ödüller", "Reconciliation and rewards", "Abgleich und Belohnungen", "Rapprochement et récompenses", "Conciliación y recompensas", "Riconciliazione e ricompense", "Conciliação e recompensas", "Сверка и награды", "المطابقة والمكافآت", "对账与奖励", "照合と報酬", "대사 및 보상", "मिलान और पुरस्कार"],
    "Serbest bırakma kuyruğu": ["Serbest bırakma kuyruğu", "Release queue", "Freigabewarteschlange", "File de déblocage", "Cola de liberación", "Coda di rilascio", "Fila de liberação", "Очередь на выдачу", "قائمة انتظار صرف المكافآت", "发放队列", "報酬付与キュー", "보상 지급 대기열", "पुरस्कार जारी करने की कतार"],
    "Yalnız LectureSift sipariş referansını gir. Kart bilgisi, erişim anahtarı veya müşteri verisi yazma.": ["Yalnız LectureSift sipariş referansını gir. Kart bilgisi, erişim anahtarı veya müşteri verisi yazma.", "Enter only the LectureSift order reference. Do not enter card details, access keys, or customer data.", "Gib nur die LectureSift-Bestellreferenz ein. Keine Kartendaten, Zugriffsschlüssel oder Kundendaten eingeben.", "Saisissez uniquement la référence de commande LectureSift. Ne saisissez ni données de carte, ni clé d’accès, ni données client.", "Introduce solo la referencia del pedido de LectureSift. No introduzcas datos de tarjetas, claves de acceso ni datos de clientes.", "Inserisci solo il riferimento dell’ordine LectureSift. Non inserire dati della carta, chiavi di accesso o dati del cliente.", "Insira apenas a referência do pedido LectureSift. Não insira dados do cartão, chaves de acesso ou dados do cliente.", "Введите только номер заказа LectureSift. Не вводите данные карты, ключи доступа или данные клиента.", "أدخل مرجع طلب LectureSift فقط. لا تُدخل بيانات البطاقة أو مفاتيح الوصول أو بيانات العميل.", "仅输入 LectureSift 订单编号。请勿输入银行卡信息、访问密钥或客户数据。", "LectureSift の注文参照番号だけを入力してください。カード情報、アクセスキー、顧客データは入力しないでください。", "LectureSift 주문 참조 번호만 입력하세요. 카드 정보, 액세스 키 또는 고객 데이터를 입력하지 마세요.", "केवल LectureSift ऑर्डर रेफ़रेंस दर्ज करें। कार्ड की जानकारी, एक्सेस कुंजी या ग्राहक डेटा दर्ज न करें।"],
    "Ödeme kaydını yeniden işle, bekleme süresini doğrula ve yalnız sağlayıcı mutabakatından sonra ödülü kullanıma aç.": ["Ödeme kaydını yeniden işle, bekleme süresini doğrula ve yalnız sağlayıcı mutabakatından sonra ödülü kullanıma aç.", "Reprocess the payment record, verify the waiting period, and make the reward available only after provider reconciliation.", "Zahlungsdatensatz erneut verarbeiten, Wartefrist prüfen und die Belohnung erst nach dem Abgleich mit dem Anbieter freigeben.", "Retraitez l’enregistrement de paiement, vérifiez le délai d’attente et ne rendez la récompense disponible qu’après le rapprochement avec le prestataire.", "Vuelve a procesar el registro de pago, verifica el período de espera y habilita la recompensa solo después de conciliarla con el proveedor.", "Rielabora il record di pagamento, verifica il periodo di attesa e rendi disponibile la ricompensa solo dopo la riconciliazione con il fornitore.", "Reprocesse o registro de pagamento, verifique o período de espera e libere a recompensa somente após a conciliação com o provedor.", "Повторно обработайте платёжную запись, проверьте срок ожидания и откройте награду только после сверки с провайдером.", "أعد معالجة سجل الدفع، وتحقق من مدة الانتظار، ولا تُتح المكافأة إلا بعد المطابقة مع مزوّد الدفع.", "重新处理支付记录，核实等待期，并仅在与支付服务商完成对账后开放奖励。", "決済記録を再処理し、保留期間を確認したうえで、決済事業者との照合後にのみ報酬を利用可能にします。", "결제 기록을 다시 처리하고 대기 기간을 확인한 뒤 결제 제공업체와 대사를 마친 경우에만 보상을 사용할 수 있게 합니다.", "भुगतान रिकॉर्ड को दोबारा प्रोसेस करें, प्रतीक्षा अवधि जाँचें और भुगतान प्रदाता से मिलान के बाद ही पुरस्कार उपलब्ध करें।"],
    "Önce vadenin dolduğunu ve kullanıcının ödül seçtiğini doğrula. Ardından ödeme sağlayıcısında ödeme, tutar ve iade durumunu incele; kanıt alanına yalnız gizli veri içermeyen işlem veya ekstre referansı yaz.": ["Önce vadenin dolduğunu ve kullanıcının ödül seçtiğini doğrula. Ardından ödeme sağlayıcısında ödeme, tutar ve iade durumunu incele; kanıt alanına yalnız gizli veri içermeyen işlem veya ekstre referansı yaz.", "First verify that the hold period has ended and the user has selected a reward. Then review the payment, amount, and refund status with the payment provider; enter only a transaction or statement reference that contains no sensitive data in the evidence field.", "Prüfe zuerst, ob die Wartefrist abgelaufen ist und der Nutzer eine Belohnung gewählt hat. Prüfe dann Zahlung, Betrag und Erstattungsstatus beim Zahlungsanbieter; trage im Nachweisfeld nur eine Vorgangs- oder Abrechnungsreferenz ohne vertrauliche Daten ein.", "Vérifiez d’abord que le délai d’attente est écoulé et que l’utilisateur a choisi une récompense. Vérifiez ensuite le paiement, le montant et l’état du remboursement auprès du prestataire ; saisissez dans le champ de preuve uniquement une référence de transaction ou de relevé sans donnée sensible.", "Primero verifica que haya finalizado el período de espera y que el usuario haya elegido una recompensa. Después revisa con el proveedor el pago, el importe y el estado del reembolso; escribe en el campo de prueba solo una referencia de transacción o extracto sin datos sensibles.", "Verifica prima che il periodo di attesa sia terminato e che l’utente abbia scelto una ricompensa. Poi controlla con il fornitore il pagamento, l’importo e lo stato del rimborso; nel campo della prova inserisci solo un riferimento di transazione o estratto privo di dati sensibili.", "Primeiro, verifique se o período de espera terminou e se o usuário escolheu uma recompensa. Depois, confira com o provedor o pagamento, o valor e o status do reembolso; no campo de prova, insira apenas uma referência de transação ou extrato sem dados sensíveis.", "Сначала убедитесь, что срок ожидания истёк и пользователь выбрал награду. Затем проверьте у платёжного провайдера платёж, сумму и статус возврата; в поле подтверждения укажите только номер операции или выписки без конфиденциальных данных.", "تحقق أولًا من انتهاء مدة الانتظار ومن اختيار المستخدم للمكافأة. ثم راجع مع مزوّد الدفع حالة الدفع والمبلغ والاسترداد؛ ولا تُدخل في حقل الإثبات إلا مرجع معاملة أو كشف لا يتضمن بيانات حساسة.", "首先确认等待期已结束且用户已选择奖励。然后在支付服务商处核查付款、金额和退款状态；证据字段中仅填写不含敏感数据的交易编号或账单编号。", "まず保留期間が終了し、ユーザーが報酬を選択していることを確認します。次に決済事業者で支払い、金額、返金状況を確認し、証跡欄には機密情報を含まない取引番号または明細参照番号だけを入力してください。", "먼저 대기 기간이 끝났고 사용자가 보상을 선택했는지 확인하세요. 그런 다음 결제 제공업체에서 결제, 금액, 환불 상태를 확인하고 증빙 입력란에는 민감한 정보가 없는 거래 또는 명세서 참조 번호만 입력하세요.", "पहले पुष्टि करें कि प्रतीक्षा अवधि पूरी हो गई है और उपयोगकर्ता ने पुरस्कार चुना है। फिर भुगतान प्रदाता के पास भुगतान, राशि और रिफ़ंड स्थिति जाँचें; प्रमाण फ़ील्ड में केवल ऐसा लेन-देन या स्टेटमेंट रेफ़रेंस दर्ज करें जिसमें संवेदनशील डेटा न हो।"],
    "%0 doğrulandı": ["%0 doğrulandı", "%0 verified", "%0 bestätigt", "%0 vérifié", "%0 verificado", "%0 verificato", "%0 verificado", "%0 подтверждено", "تم التحقق من %0", "已核对 %0", "%0 確認済み", "%0 확인됨", "%0 सत्यापित"],
    "API anahtarı, kart bilgisi veya fatura içeriği yükleme. Yalnızca toplam tutarı ve fatura/ekstre referansını kaydet.": ["API anahtarı, kart bilgisi veya fatura içeriği yükleme. Yalnızca toplam tutarı ve fatura/ekstre referansını kaydet.", "Do not upload API keys, card details, or invoice content. Record only the total and invoice or statement reference.", "Keine API-Schlüssel, Kartendaten oder Rechnungsinhalte hochladen. Nur Gesamtbetrag und Rechnungs- oder Abrechnungsreferenz erfassen.", "Ne téléversez pas de clé API, de données de carte ni de contenu de facture. Enregistrez uniquement le total et la référence de facture ou de relevé.", "No subas claves API, datos de tarjeta ni contenido de facturas. Registra solo el total y la referencia de factura o extracto.", "Non caricare chiavi API, dati della carta o contenuti della fattura. Registra solo il totale e il riferimento della fattura o dell'estratto.", "Não envie chaves de API, dados de cartão ou conteúdo da fatura. Registre apenas o total e a referência da fatura ou do extrato.", "Не загружайте ключи API, данные карт или содержимое счетов. Укажите только итоговую сумму и номер счёта или выписки.", "لا ترفع مفاتيح API أو بيانات البطاقة أو محتوى الفاتورة. سجّل فقط الإجمالي ومرجع الفاتورة أو الكشف.", "请勿上传 API 密钥、银行卡信息或发票内容。仅记录总额以及发票或对账单编号。", "APIキー、カード情報、請求書の内容はアップロードしないでください。合計額と請求書または明細の参照番号のみを記録します。", "API 키, 카드 정보 또는 청구서 내용을 업로드하지 마세요. 총액과 청구서 또는 명세서 참조 번호만 기록하세요.", "API कुंजी, कार्ड विवरण या चालान की सामग्री अपलोड न करें। केवल कुल राशि और चालान या विवरण संदर्भ दर्ज करें।"],
    "Ara toplam": ["Ara toplam", "Subtotal", "Zwischensumme", "Sous-total", "Subtotal", "Subtotale", "Subtotal", "Промежуточный итог", "المجموع الفرعي", "小计", "小計", "소계", "उप-योग"],
    "Açıklama": ["Açıklama", "Description", "Beschreibung", "Description", "Descripción", "Descrizione", "Descrição", "Описание", "الوصف", "说明", "説明", "설명", "विवरण"],
    "Ağustos 2026 faturası": ["Ağustos 2026 faturası", "August 2026 invoice", "Rechnung August 2026", "Facture d’août 2026", "Factura de agosto de 2026", "Fattura di agosto 2026", "Fatura de agosto de 2026", "Счёт за август 2026 г.", "فاتورة أغسطس 2026", "2026 年 8 月发票", "2026年8月の請求書", "2026년 8월 청구서", "अगस्त 2026 का चालान"],
    "BİRİM EKONOMİ": ["BİRİM EKONOMİ", "UNIT ECONOMICS", "STÜCKÖKONOMIE", "ÉCONOMIE UNITAIRE", "ECONOMÍA UNITARIA", "ECONOMIA UNITARIA", "ECONOMIA UNITÁRIA", "ЮНИТ-ЭКОНОМИКА", "اقتصاديات الوحدة", "单位经济", "ユニットエコノミクス", "단위 경제성", "इकाई अर्थशास्त्र"],
    "Dakika, iş ve gelir karşılığı": ["Dakika, iş ve gelir karşılığı", "Minutes, jobs, and revenue comparison", "Vergleich von Minuten, Aufträgen und Umsatz", "Comparaison des minutes, tâches et revenus", "Comparación de minutos, tareas e ingresos", "Confronto tra minuti, attività e ricavi", "Comparação de minutos, trabalhos e receita", "Сопоставление минут, задач и выручки", "مقارنة الدقائق والمهام والإيرادات", "分钟、任务和收入对比", "処理時間・ジョブ・売上の比較", "시간·작업·수익 비교", "मिनट, कार्य और आय की तुलना"],
    "Bu sözleşme, LectureSift üzerinden satın alınan abonelik ve tek kullanımlık dakika paketlerinin uzaktan satış koşullarını düzenler. Sipariş ekranında gösterilen plan, dönem, toplam tutar ve kullanıcı bilgileri bu sözleşmenin ayrılmaz parçasıdır.": ["Bu sözleşme, LectureSift üzerinden satın alınan abonelik ve tek kullanımlık dakika paketlerinin uzaktan satış koşullarını düzenler. Sipariş ekranında gösterilen plan, dönem, toplam tutar ve kullanıcı bilgileri bu sözleşmenin ayrılmaz parçasıdır.", "This agreement governs the terms of remote sales of subscription and single-use minute packages purchased through LectureSift. The plan, period, total amount and user information shown on the order screen are an integral part of this agreement.", "Diese Vereinbarung regelt die Bedingungen für den Fernverkauf von Abonnements und Einzelminutenpaketen, die über LectureSift erworben wurden. Der auf der Bestellmaske angezeigte Plan, Zeitraum, Gesamtbetrag und Benutzerinformationen sind integraler Bestandteil dieser Vereinbarung.", "Cet accord régit les conditions de vente à distance d'abonnements et de forfaits de minutes à usage unique achetés via LectureSift. Le forfait, la période, le montant total et les informations utilisateur affichés sur l'écran de commande font partie intégrante de cet accord.", "Este acuerdo rige los términos de ventas remotas de suscripción y paquetes de minutos de un solo uso comprados a través de LectureSift. El plan, el período, el monto total y la información del usuario que se muestran en la pantalla del pedido son parte integral de este acuerdo.", "Il presente accordo regola i termini della vendita a distanza di abbonamenti e pacchetti di minuti monouso acquistati tramite LectureSift. Il piano, il periodo, l'importo totale e le informazioni sull'utente mostrate nella schermata dell'ordine sono parte integrante del presente accordo.", "Este acordo rege os termos de vendas remotas de assinaturas e pacotes de minutos de uso único adquiridos através do LectureSift. O plano, período, valor total e informações do usuário mostradas na tela do pedido são parte integrante deste contrato.", "Настоящее соглашение регулирует условия удаленной продажи подписки и одноразовых пакетов минут, приобретенных через LectureSift. План, период, общая сумма и информация о пользователе, отображаемые на экране заказа, являются неотъемлемой частью настоящего соглашения.", "تحكم هذه الاتفاقية شروط البيع عن بعد للاشتراك وحزم الدقائق ذات الاستخدام الواحد التي تم شراؤها من خلال LectureSift. تعد الخطة والفترة والمبلغ الإجمالي ومعلومات المستخدم المعروضة على شاشة الطلب جزءًا لا يتجزأ من هذه الاتفاقية.", "本协议管辖通过 LectureSift 购买的订阅和一次性分钟套餐的远程销售条款。订单屏幕上显示的计划、期限、总额和用户信息是本协议不可分割的一部分。", "本契約は、LectureSiftを通じて購入されるサブスクリプションおよび一回払いの処理時間パックに関する通信販売条件を定めます。注文画面に表示されるプラン、期間、合計金額、ユーザー情報は、本契約の不可分の一部を構成します。", "본 계약은 LectureSift를 통해 구매한 구독 및 일회용 패키지의 원격 판매 조건에 적용됩니다. 주문 화면에 표시되는 요금제, 기간, 총액 및 사용자 정보는 본 계약의 필수적인 부분입니다.", "यह समझौता LectureSift के माध्यम से खरीदी गई सदस्यता और एकल-उपयोग मिनट पैकेज की दूरस्थ बिक्री की शर्तों को नियंत्रित करता है। ऑर्डर स्क्रीन पर दिखाई गई योजना, अवधि, कुल राशि और उपयोगकर्ता की जानकारी इस समझौते का एक अभिन्न अंग हैं।"],
    "Ek dakika satın al": ["Ek dakika satın al", "Buy additional minutes", "Kaufen Sie zusätzliche Minuten", "Acheter des minutes supplémentaires", "Comprar minutos adicionales", "Acquista minuti aggiuntivi", "Compre minutos adicionais", "Купить дополнительные минуты", "شراء دقائق إضافية", "购买额外通话时长", "追加の処理時間を購入", "추가 분 구매", "एक्स्ट्रा मिनट खरीदें"],
    "İç kampanyayı, banner reklamları, reklam karşılığı dakikayı ve Google dönüşümlerini tek yerden izle.": ["İç kampanyayı, banner reklamları, reklam karşılığı dakikayı ve Google dönüşümlerini tek yerden izle.", "Monitor internal campaign, banner ads, minutes in return for advertising and Google conversions in one place.", "Überwachen Sie interne Kampagnen, Bannerwerbung, Werbeminuten und Google-Conversions an einem Ort.", "Surveillez la campagne interne, les bannières publicitaires, les minutes en échange de publicité et les conversions Google en un seul endroit.", "Supervise la campaña interna, los anuncios publicitarios, los minutos a cambio de publicidad y las conversiones de Google en un solo lugar.", "Monitora campagne interne, banner pubblicitari, minuti in cambio di pubblicità e conversioni di Google in un unico posto.", "Monitore campanhas internas, banners, minutos de retorno de publicidade e conversões do Google em um só lugar.", "Контролируйте внутреннюю кампанию, баннерную рекламу, минуты в обмен на рекламу и конверсии Google в одном месте.", "مراقبة الحملة الداخلية والإعلانات والدقائق مقابل الإعلانات وتحويلات Google في مكان واحد.", "在一处监控内部活动、横幅广告、广告回报时间和 Google 转化。", "自社キャンペーン、バナー広告、広告視聴で付与される処理時間（分）、Googleコンバージョンを一か所で確認します。", "내부 캠페인, 배너 광고, 광고에 대한 대가(분) 및 Google 전환을 한 곳에서 모니터링하세요.", "एक ही स्थान पर आंतरिक अभियान, बैनर विज्ञापन, विज्ञापन के बदले में मिनट और Google रूपांतरण की निगरानी करें।"],
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
    for payload in re.findall(r'^\s*(?:"[^"]+"|[a-z]+):\s*\[(.*?)\],?$', source, re.MULTILINE):
        try:
            row = json.loads(f"[{payload}]")
        except json.JSONDecodeError:
            continue
        if len(row) == len(LANGUAGES) and all(isinstance(value, str) and value.strip() for value in row):
            values.add(normalize(str(row[0])))
    return values


def write_catalog(path: Path, catalog: dict[str, list[str]]) -> None:
    ordered = {}
    for key in sorted(catalog, key=str.casefold):
        values = [restore_brand(str(value)) for value in CURATED_TRANSLATIONS.get(key, catalog[key])]
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
    central_sources.update(read_central_sources(frontend / "referral-i18n.js"))
    central_sources.update(read_central_sources(frontend / "assistant-i18n.js"))
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
