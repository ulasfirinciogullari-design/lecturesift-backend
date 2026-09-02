(function () {
  const API = "https://api.lecturesift.com";
  const TOKEN_KEY = "lecturesift-billing-token";
  const CODES = ["tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi"];
  const COPY = {
    menu: ["Ana menü", "Main menu", "Hauptmenü", "Menu principal", "Menú principal", "Menu principale", "Menu principal", "Главное меню", "القائمة الرئيسية", "主菜单", "メインメニュー", "기본 메뉴", "मुख्य मेन्यू"],
    home: ["Ana sayfa", "Home", "Startseite", "Accueil", "Inicio", "Home", "Início", "Главная", "الرئيسية", "首页", "ホーム", "홈", "होम"],
    workspace: ["Çalışma Alanı", "Workspace", "Arbeitsbereich", "Espace de travail", "Área de trabajo", "Area di lavoro", "Área de trabalho", "Рабочая область", "مساحة العمل", "工作区", "ワークスペース", "작업 공간", "कार्य क्षेत्र"],
    features: ["Özellikler", "Features", "Funktionen", "Fonctionnalités", "Funciones", "Funzionalità", "Recursos", "Возможности", "الميزات", "功能", "機能", "기능", "विशेषताएँ"],
    plans: ["Planlar", "Plans", "Tarife", "Forfaits", "Planes", "Piani", "Planos", "Тарифы", "الخطط", "套餐", "プラン", "요금제", "प्लान"],
    about: ["Hakkımızda", "About", "Über uns", "À propos", "Nosotros", "Chi siamo", "Sobre", "О нас", "من نحن", "关于我们", "私たちについて", "소개", "हमारे बारे में"],
    login: ["Giriş", "Sign in", "Anmelden", "Connexion", "Iniciar sesión", "Accedi", "Entrar", "Войти", "تسجيل الدخول", "登录", "ログイン", "로그인", "साइन इन"],
    account: ["Hesabım", "My account", "Mein Konto", "Mon compte", "Mi cuenta", "Il mio account", "Minha conta", "Мой аккаунт", "حسابي", "我的账户", "マイアカウント", "내 계정", "मेरा खाता"],
    instagram: ["LectureSift Instagram hesabı", "LectureSift on Instagram", "LectureSift auf Instagram", "LectureSift sur Instagram", "LectureSift en Instagram", "LectureSift su Instagram", "LectureSift no Instagram", "LectureSift в Instagram", "حساب LectureSift على Instagram", "LectureSift 的 Instagram 账户", "LectureSiftのInstagram", "LectureSift Instagram 계정", "Instagram पर LectureSift"],
  };

  const i18n = window.LectureSiftI18n;
  const language = i18n?.language || document.documentElement.lang || "tr";
  const languageIndex = Math.max(0, CODES.indexOf(language));
  const label = key => COPY[key]?.[languageIndex] || COPY[key]?.[1] || key;
  const pathFor = path => i18n?.localizedPath ? i18n.localizedPath(language, path) : path;
  const exact = text => i18n?.exact?.(text) || text;
  const header = document.querySelector(".topbar,.legal-topbar");
  if (!header) return;
  const brand = header.querySelector(".brand");
  if (brand) brand.href = pathFor("/");

  const currentBasePath = (() => {
    const parts = location.pathname.split("/").filter(Boolean);
    if (CODES.includes(parts[0])) parts.shift();
    const pathname = parts.length ? `/${parts.join("/")}` : "/";
    return pathname === "/index.html" ? "/" : pathname;
  })();

  const existingNav = header.querySelector("nav");
  const existingPicker = header.querySelector(".language-picker,.language-switcher");
  const tools = document.createElement("div");
  tools.className = "public-header-tools";
  const nav = existingNav || document.createElement("nav");
  nav.className = "public-nav";
  nav.setAttribute("aria-label", label("menu"));
  nav.replaceChildren();

  const links = [
    ["home", "/"],
    ["workspace", "/workspace.html"],
    ["features", "/features.html"],
    ["plans", "/plans.html"],
    ["about", "/about.html"],
  ];
  links.forEach(([key, path]) => {
    const anchor = document.createElement("a");
    anchor.className = "public-nav-link";
    anchor.href = pathFor(path);
    anchor.textContent = label(key);
    if (currentBasePath === path) {
      anchor.classList.add("active");
      anchor.setAttribute("aria-current", "page");
    }
    nav.append(anchor);
  });

  const instagram = document.createElement("a");
  instagram.className = "public-social-link";
  instagram.href = "https://www.instagram.com/lecturesift/";
  instagram.target = "_blank";
  instagram.rel = "noopener noreferrer";
  instagram.setAttribute("aria-label", label("instagram"));
  instagram.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="5"></rect><circle cx="12" cy="12" r="4"></circle><circle cx="17.5" cy="6.5" r=".8"></circle></svg>';
  nav.append(instagram);

  const account = document.createElement("a");
  account.id = "accountButton";
  account.className = "public-account-link";
  account.dataset.sessionState = "loading";
  nav.append(account);

  if (existingPicker) tools.append(existingPicker);
  tools.append(nav);
  header.append(tools);

  const setSessionState = signedIn => {
    account.dataset.sessionState = signedIn ? "signed-in" : "signed-out";
    account.textContent = label(signedIn ? "account" : "login");
    account.href = pathFor(signedIn ? "/account.html" : "/login.html");
  };

  const rebuildInformationNavigation = () => {
    const aside = document.querySelector(".legal-nav");
    if (!aside) return;
    const corporatePages = ["/about.html", "/contact.html"];
    const legalPages = ["/privacy.html", "/terms.html", "/cookies.html", "/refund.html", "/distance-sales.html"];
    if (![...corporatePages, ...legalPages].includes(currentBasePath)) return;
    const groups = [
      ["LectureSift", [["Hakkımızda", "/about.html"], ["İletişim", "/contact.html"]]],
      ["Ürün", [["Çalışma Alanı", "/workspace.html"], ["Özellikler", "/features.html"], ["Planlar", "/plans.html"]]],
      ["Yasal belgeler", [
        ["Gizlilik ve KVKK", "/privacy.html"], ["Kullanım koşulları", "/terms.html"],
        ["Çerez ve depolama", "/cookies.html"], ["Teslimat, iptal ve iade", "/refund.html"],
        ["Mesafeli Satış Sözleşmesi", "/distance-sales.html"],
      ]],
    ];
    aside.replaceChildren();
    groups.forEach(([headingText, entries], groupIndex) => {
      const heading = document.createElement(groupIndex === 0 ? "strong" : "span");
      if (groupIndex > 0) heading.className = "legal-nav-divider";
      heading.textContent = groupIndex === 0 ? headingText : exact(headingText);
      aside.append(heading);
      entries.forEach(([text, path]) => {
        const anchor = document.createElement("a");
        anchor.href = pathFor(path);
        anchor.textContent = exact(text);
        if (currentBasePath === path) {
          anchor.className = "active";
          anchor.setAttribute("aria-current", "page");
        }
        aside.append(anchor);
      });
    });

    const footerNav = document.querySelector(".legal-footer nav");
    if (footerNav) {
      footerNav.replaceChildren();
      [
        ["İletişim", "/contact.html"], ["Gizlilik", "/privacy.html"],
        ["Koşullar", "/terms.html"], ["İptal ve iade", "/refund.html"],
        ["Mesafeli satış", "/distance-sales.html"], ["Çerezler", "/cookies.html"],
      ].forEach(([text, path]) => {
        const anchor = document.createElement("a");
        anchor.href = pathFor(path);
        anchor.textContent = exact(text);
        footerNav.append(anchor);
      });
    }
  };

  rebuildInformationNavigation();
  if (currentBasePath === "/terms.html") {
    const notice = document.querySelector(".legal-card .notice");
    if (notice) notice.textContent = exact(
      "Satıcı/hizmet sağlayıcı kimliği, siparişe özgü toplam fiyat, vergi, dönem, ödeme yöntemi ve dijital hizmet başlangıcı; kullanıcı onayından hemen önce sipariş özetinde ve Mesafeli Satış Sözleşmesi'nde gösterilir."
    );
  }
  const token = localStorage.getItem(TOKEN_KEY) || "";
  setSessionState(Boolean(token));
  if (!token) return;

  fetch(`${API}/billing/me`, {
    cache: "no-store",
    headers: {Authorization: `Bearer ${token}`},
  }).then(response => {
    if (response.ok) return response.json();
    if (response.status === 401 || response.status === 403) {
      localStorage.removeItem(TOKEN_KEY);
      setSessionState(false);
    }
    throw new Error(`account-status-${response.status}`);
  }).then(body => {
    setSessionState(Boolean(body?.account));
    window.dispatchEvent(new CustomEvent("lecturesift:account-state", {detail: {signedIn: Boolean(body?.account)}}));
  }).catch(() => {
    // Temporary network failures must not make a valid local session look signed out.
  });
})();
