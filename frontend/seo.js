(() => {
  "use strict";

  const PRODUCTION_ORIGIN = "https://lecturesift.com";
  const LANGUAGES = ["tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi"];
  const OG_LOCALES = {tr:"tr_TR",en:"en_US",de:"de_DE",fr:"fr_FR",es:"es_ES",it:"it_IT",pt:"pt_BR",ru:"ru_RU",ar:"ar_SA",zh:"zh_CN",ja:"ja_JP",ko:"ko_KR",hi:"hi_IN"};
  const PUBLIC_PATHS = new Set([
    "/",
    "/index.html",
    "/features.html",
    "/plans.html",
    "/about.html",
    "/contact.html",
    "/privacy.html",
    "/terms.html",
    "/cookies.html",
    "/refund.html",
  ]);
  const PAGE_TYPES = {
    "/": "website",
    "/features.html": "article",
    "/plans.html": "website",
    "/about.html": "article",
    "/contact.html": "website",
    "/privacy.html": "article",
    "/terms.html": "article",
    "/cookies.html": "article",
    "/refund.html": "article",
  };

  const parts = location.pathname.split("/").filter(Boolean);
  const language = LANGUAGES.includes(parts[0]) ? parts.shift() : (document.documentElement.lang || "tr");
  const unlocalizedPath = `/${parts.join("/")}` || "/";
  const path = unlocalizedPath === "/index.html" ? "/" : unlocalizedPath;
  if (!PUBLIC_PATHS.has(path)) return;

  const localizedPath = locale => locale === "tr" ? path : `/${locale}${path === "/" ? "/" : path}`;
  const canonicalUrl = `${PRODUCTION_ORIGIN}${localizedPath(language)}`;
  const description = document.querySelector('meta[name="description"]')?.content
    || document.querySelector(".lead,.detail-hero > p:not(.eyebrow)")?.textContent?.trim()
    || "LectureSift ders videolarını transkript, özet, quiz ve bilgi kartlarına dönüştürür.";
  const imageUrl = `${PRODUCTION_ORIGIN}/og-image.png`;

  const setMeta = (selector, attributes) => {
    let node = document.head.querySelector(selector);
    if (!node) {
      node = document.createElement("meta");
      document.head.append(node);
    }
    Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, value));
  };
  const setLink = (selector, attributes) => {
    let node = document.head.querySelector(selector);
    if (!node) {
      node = document.createElement("link");
      document.head.append(node);
    }
    Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, value));
  };

  setLink('link[rel="canonical"]', {rel: "canonical", href: canonicalUrl});
  LANGUAGES.forEach(locale => setLink(`link[rel="alternate"][hreflang="${locale}"]`, {
    rel: "alternate", hreflang: locale, href: `${PRODUCTION_ORIGIN}${localizedPath(locale)}`,
  }));
  setLink('link[rel="alternate"][hreflang="x-default"]', {rel: "alternate", hreflang: "x-default", href: `${PRODUCTION_ORIGIN}${path}`});
  setMeta('meta[property="og:type"]', {property: "og:type", content: PAGE_TYPES[path] || "website"});
  setMeta('meta[property="og:site_name"]', {property: "og:site_name", content: "LectureSift"});
  setMeta('meta[property="og:title"]', {property: "og:title", content: document.title});
  setMeta('meta[property="og:description"]', {property: "og:description", content: description});
  setMeta('meta[property="og:url"]', {property: "og:url", content: canonicalUrl});
  setMeta('meta[property="og:locale"]', {property: "og:locale", content: OG_LOCALES[language] || "tr_TR"});
  setMeta('meta[property="og:image"]', {property: "og:image", content: imageUrl});
  setMeta('meta[property="og:image:alt"]', {property: "og:image:alt", content: "LectureSift yapay zekâ destekli ders çalışma platformu"});
  setMeta('meta[name="twitter:card"]', {name: "twitter:card", content: "summary_large_image"});
  setMeta('meta[name="twitter:title"]', {name: "twitter:title", content: document.title});
  setMeta('meta[name="twitter:description"]', {name: "twitter:description", content: description});
  setMeta('meta[name="twitter:image"]', {name: "twitter:image", content: imageUrl});

  if (!new Set(["lecturesift.com", "www.lecturesift.com", "localhost", "127.0.0.1"]).has(location.hostname)) {
    setMeta('meta[name="robots"]', {name: "robots", content: "noindex,nofollow,noarchive"});
  }

  const schema = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Organization",
        "@id": `${PRODUCTION_ORIGIN}/#organization`,
        name: "LectureSift",
        url: canonicalUrl,
        logo: `${PRODUCTION_ORIGIN}/favicon.svg`,
        sameAs: ["https://www.instagram.com/lecturesift/"],
      },
      {
        "@type": "WebSite",
        "@id": `${PRODUCTION_ORIGIN}/#website`,
        name: "LectureSift",
        url: canonicalUrl,
        inLanguage: document.documentElement.lang || "tr",
        publisher: {"@id": `${PRODUCTION_ORIGIN}/#organization`},
      },
      {
        "@type": "SoftwareApplication",
        "@id": `${PRODUCTION_ORIGIN}/#application`,
        name: "LectureSift",
        url: `${PRODUCTION_ORIGIN}/`,
        applicationCategory: "EducationalApplication",
        operatingSystem: "Web",
        description,
        image: imageUrl,
        offers: {"@type": "Offer", price: "0", priceCurrency: "TRY"},
        publisher: {"@id": `${PRODUCTION_ORIGIN}/#organization`},
      },
    ],
  };
  const jsonLd = document.createElement("script");
  jsonLd.type = "application/ld+json";
  jsonLd.textContent = JSON.stringify(schema);
  document.head.append(jsonLd);
})();
