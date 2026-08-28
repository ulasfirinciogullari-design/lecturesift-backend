(() => {
  "use strict";

  const STORAGE_KEY = "lecturesift-consent-v1";
  const language = window.LectureSiftI18n?.language || document.documentElement.lang || "tr";
  const copy = {
    tr: ["Gizlilik tercihlerin", "Zorunlu kayıtlar siteyi çalıştırır. İstatistik ve reklam teknolojileri yalnızca izin verirsen ve sağlayıcılar etkinse çalışır.", "Yalnızca zorunlu", "Tümüne izin ver", "Tercihleri yönet", "Zorunlu", "Giriş, güvenlik, dil ve ödeme akışları için gereklidir.", "İstatistik", "Siteyi nasıl kullandığını toplu olarak anlamamıza yardım eder.", "Reklam", "Reklam gösterimi ve dönüşüm ölçümü için kullanılır.", "Tercihleri kaydet", "Kapat", "Çerez ve depolama politikasını aç"],
    en: ["Your privacy choices", "Essential storage keeps the site working. Analytics and advertising technologies run only if you allow them and the providers are enabled.", "Essential only", "Allow all", "Manage choices", "Essential", "Required for sign-in, security, language, and payment flows.", "Analytics", "Helps us understand site usage in aggregate.", "Advertising", "Used for ad delivery and conversion measurement.", "Save choices", "Close", "Open the cookie and storage policy"],
    de: ["Deine Datenschutzauswahl", "Notwendige Speicherungen halten die Website funktionsfähig. Statistik- und Werbetechnologien laufen nur mit deiner Erlaubnis und wenn die Anbieter aktiviert sind.", "Nur notwendige", "Alle erlauben", "Auswahl verwalten", "Notwendig", "Für Anmeldung, Sicherheit, Sprache und Zahlungsabläufe erforderlich.", "Statistik", "Hilft uns, die Nutzung der Website zusammengefasst zu verstehen.", "Werbung", "Wird für Werbeanzeigen und Conversion-Messung verwendet.", "Auswahl speichern", "Schließen", "Cookie- und Speicherrichtlinie öffnen"],
    fr: ["Vos choix de confidentialité", "Le stockage essentiel assure le fonctionnement du site. Les technologies de mesure et de publicité ne fonctionnent qu’avec votre accord et lorsque les fournisseurs sont activés.", "Essentiel uniquement", "Tout autoriser", "Gérer les choix", "Essentiel", "Nécessaire pour la connexion, la sécurité, la langue et les paiements.", "Statistiques", "Nous aide à comprendre globalement l’utilisation du site.", "Publicité", "Utilisé pour l’affichage publicitaire et la mesure des conversions.", "Enregistrer", "Fermer", "Ouvrir la politique relative aux cookies et au stockage"],
    es: ["Tus opciones de privacidad", "El almacenamiento esencial mantiene el sitio en funcionamiento. Las tecnologías de análisis y publicidad solo funcionan si das permiso y los proveedores están activos.", "Solo esenciales", "Permitir todo", "Gestionar opciones", "Esenciales", "Necesario para acceso, seguridad, idioma y pagos.", "Analítica", "Nos ayuda a entender de forma agregada cómo se usa el sitio.", "Publicidad", "Se usa para mostrar anuncios y medir conversiones.", "Guardar opciones", "Cerrar", "Abrir la política de cookies y almacenamiento"],
    it: ["Le tue scelte sulla privacy", "L’archiviazione essenziale mantiene il sito funzionante. Le tecnologie statistiche e pubblicitarie operano solo con il tuo consenso e quando i fornitori sono attivi.", "Solo essenziali", "Consenti tutto", "Gestisci scelte", "Essenziali", "Necessario per accesso, sicurezza, lingua e pagamenti.", "Statistiche", "Ci aiuta a capire in modo aggregato l’uso del sito.", "Pubblicità", "Usato per annunci e misurazione delle conversioni.", "Salva scelte", "Chiudi", "Apri la politica su cookie e archiviazione"],
    pt: ["Suas escolhas de privacidade", "O armazenamento essencial mantém o site funcionando. Tecnologias de análise e publicidade só funcionam com sua permissão e quando os provedores estão ativos.", "Somente essenciais", "Permitir tudo", "Gerenciar escolhas", "Essenciais", "Necessário para login, segurança, idioma e pagamentos.", "Análise", "Ajuda a entender de forma agregada como o site é usado.", "Publicidade", "Usado para anúncios e medição de conversões.", "Salvar escolhas", "Fechar", "Abrir a política de cookies e armazenamento"],
    ru: ["Настройки конфиденциальности", "Необходимое хранилище обеспечивает работу сайта. Аналитика и рекламные технологии работают только с вашего разрешения и после подключения поставщиков.", "Только необходимые", "Разрешить все", "Настроить", "Необходимые", "Нужны для входа, безопасности, языка и платежей.", "Аналитика", "Помогает в совокупности понимать использование сайта.", "Реклама", "Используется для показа рекламы и измерения конверсий.", "Сохранить", "Закрыть", "Открыть политику файлов cookie и хранилища"],
    ar: ["خيارات الخصوصية", "يحافظ التخزين الضروري على عمل الموقع. لا تعمل تقنيات الإحصاءات والإعلانات إلا بإذنك وعند تفعيل مزودي الخدمة.", "الضروري فقط", "السماح بالكل", "إدارة الخيارات", "ضروري", "مطلوب لتسجيل الدخول والأمان واللغة والدفع.", "الإحصاءات", "يساعدنا على فهم استخدام الموقع بصورة مجمعة.", "الإعلانات", "يُستخدم لعرض الإعلانات وقياس التحويلات.", "حفظ الخيارات", "إغلاق", "فتح سياسة ملفات تعريف الارتباط والتخزين"],
    zh: ["隐私偏好", "必要存储用于维持网站运行。分析和广告技术仅在你允许且相关服务已启用时运行。", "仅必要项", "全部允许", "管理偏好", "必要项", "登录、安全、语言和支付流程所必需。", "分析", "帮助我们汇总了解网站使用情况。", "广告", "用于广告展示和转化衡量。", "保存偏好", "关闭", "打开 Cookie 与存储政策"],
    ja: ["プライバシー設定", "必須ストレージはサイトの動作に必要です。分析・広告技術は、許可され、かつ事業者が有効な場合のみ動作します。", "必須のみ", "すべて許可", "設定を管理", "必須", "ログイン、セキュリティ、言語、決済に必要です。", "分析", "サイト利用を集計して理解するために役立ちます。", "広告", "広告配信とコンバージョン測定に使用します。", "設定を保存", "閉じる", "Cookieと保存ポリシーを開く"],
    ko: ["개인정보 선택", "필수 저장소는 사이트 작동에 필요합니다. 분석 및 광고 기술은 동의하고 제공자가 활성화된 경우에만 작동합니다.", "필수 항목만", "모두 허용", "선택 관리", "필수", "로그인, 보안, 언어 및 결제 흐름에 필요합니다.", "분석", "사이트 이용을 종합적으로 이해하는 데 도움을 줍니다.", "광고", "광고 게재 및 전환 측정에 사용됩니다.", "선택 저장", "닫기", "쿠키 및 저장 정책 열기"],
    hi: ["आपकी गोपनीयता पसंद", "ज़रूरी स्टोरेज साइट को चलाता है। एनालिटिक्स और विज्ञापन तकनीकें केवल आपकी अनुमति और प्रदाता सक्रिय होने पर चलती हैं।", "केवल ज़रूरी", "सभी की अनुमति", "पसंद प्रबंधित करें", "ज़रूरी", "साइन-इन, सुरक्षा, भाषा और भुगतान के लिए आवश्यक।", "एनालिटिक्स", "साइट उपयोग को समग्र रूप से समझने में मदद करता है।", "विज्ञापन", "विज्ञापन दिखाने और कन्वर्ज़न मापने के लिए।", "पसंद सहेजें", "बंद करें", "कुकी और स्टोरेज नीति खोलें"],
  }[language] || null;
  const text = copy || ["Your privacy choices", "Essential storage keeps the site working. Analytics and advertising technologies run only if you allow them and the providers are enabled.", "Essential only", "Allow all", "Manage choices", "Essential", "Required for sign-in, security, language, and payment flows.", "Analytics", "Helps us understand site usage in aggregate.", "Advertising", "Used for ad delivery and conversion measurement.", "Save choices", "Close", "Open the cookie and storage policy"];
  const cookiesPath = window.LectureSiftI18n?.localizedPath?.(language, "/cookies.html") || "/cookies.html";

  const read = () => {
    try {
      const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      return value && value.version === 1 ? value : null;
    } catch (_) {
      return null;
    }
  };
  const write = choices => {
    const value = {version: 1, necessary: true, analytics: !!choices.analytics, advertising: !!choices.advertising, updated_at: new Date().toISOString()};
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(value)); } catch (_) {}
    document.dispatchEvent(new CustomEvent("lecturesift:consent", {detail: value}));
    return value;
  };

  const root = document.createElement("div");
  root.className = "consent-root";
  root.innerHTML = `
    <section class="consent-banner" role="region" aria-label="${text[0]}">
      <div><strong>${text[0]}</strong><p>${text[1]} <a href="${cookiesPath}">${text[13]}</a>.</p></div>
      <div class="consent-actions"><button type="button" data-consent="essential">${text[2]}</button><button type="button" data-consent="manage">${text[4]}</button><button class="primary" type="button" data-consent="all">${text[3]}</button></div>
    </section>
    <button class="consent-manage" type="button" data-consent="manage" aria-label="${text[4]}">${text[4]}</button>
    <section class="consent-modal" role="dialog" aria-modal="true" aria-labelledby="consentTitle" hidden>
      <div class="consent-card">
        <div class="consent-heading"><div><strong id="consentTitle">${text[0]}</strong><p>${text[1]}</p></div><button type="button" data-consent="close" aria-label="${text[12]}">×</button></div>
        <label><span><b>${text[5]}</b><small>${text[6]}</small></span><input type="checkbox" checked disabled></label>
        <label><span><b>${text[7]}</b><small>${text[8]}</small></span><input id="consentAnalytics" type="checkbox"></label>
        <label><span><b>${text[9]}</b><small>${text[10]}</small></span><input id="consentAdvertising" type="checkbox"></label>
        <div class="consent-actions"><a href="${cookiesPath}">${text[13]}</a><button class="primary" type="button" data-consent="save">${text[11]}</button></div>
      </div>
    </section>`;
  document.body.append(root);

  const banner = root.querySelector(".consent-banner");
  const modal = root.querySelector(".consent-modal");
  const analytics = root.querySelector("#consentAnalytics");
  const advertising = root.querySelector("#consentAdvertising");
  const current = read();
  banner.hidden = !!current;
  analytics.checked = !!current?.analytics;
  advertising.checked = !!current?.advertising;

  const close = () => { modal.hidden = true; };
  root.addEventListener("click", event => {
    const action = event.target.closest("[data-consent]")?.dataset.consent;
    if (!action) return;
    if (action === "manage") {
      const saved = read();
      analytics.checked = !!saved?.analytics;
      advertising.checked = !!saved?.advertising;
      modal.hidden = false;
      modal.querySelector("button")?.focus();
    }
    if (action === "close") close();
    if (action === "essential") { write({analytics: false, advertising: false}); banner.hidden = true; close(); }
    if (action === "all") { write({analytics: true, advertising: true}); banner.hidden = true; close(); }
    if (action === "save") { write({analytics: analytics.checked, advertising: advertising.checked}); banner.hidden = true; close(); }
  });
  document.addEventListener("keydown", event => { if (event.key === "Escape" && !modal.hidden) close(); });

  window.LectureSiftConsent = Object.freeze({
    get: () => read() || {version: 1, necessary: true, analytics: false, advertising: false},
    allows: category => category === "necessary" || !!read()?.[category],
    open: () => root.querySelector('[data-consent="manage"]').click(),
  });
})();
