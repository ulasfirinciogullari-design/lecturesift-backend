(() => {
  "use strict";

  const PRODUCTION_ORIGIN = "https://lecturesift.com";
  const ADSENSE_ACCOUNT = "ca-pub-7608481350058806";
  const LANGUAGES = ["tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi"];
  const OG_LOCALES = {tr:"tr_TR",en:"en_US",de:"de_DE",fr:"fr_FR",es:"es_ES",it:"it_IT",pt:"pt_BR",ru:"ru_RU",ar:"ar_SA",zh:"zh_CN",ja:"ja_JP",ko:"ko_KR",hi:"hi_IN"};
  const PUBLIC_PATHS = new Set([
    "/",
    "/index.html",
    "/features.html",
    "/document-summary.html",
    "/lecture-video-summary.html",
    "/quiz-flashcards.html",
    "/plans.html",
    "/about.html",
    "/contact.html",
    "/privacy.html",
    "/terms.html",
    "/cookies.html",
    "/refund.html",
    "/distance-sales.html",
  ]);
  const PAGE_TYPES = {
    "/": "website",
    "/features.html": "article",
    "/document-summary.html": "article",
    "/lecture-video-summary.html": "article",
    "/quiz-flashcards.html": "article",
    "/plans.html": "website",
    "/about.html": "article",
    "/contact.html": "website",
    "/privacy.html": "article",
    "/terms.html": "article",
    "/cookies.html": "article",
    "/refund.html": "article",
    "/distance-sales.html": "article",
  };

  const parts = location.pathname.split("/").filter(Boolean);
  const language = LANGUAGES.includes(parts[0]) ? parts.shift() : (document.documentElement.lang || "tr");
  const unlocalizedPath = `/${parts.join("/")}` || "/";
  const path = unlocalizedPath === "/index.html" ? "/" : unlocalizedPath;
  let adsenseAccountMeta = document.head.querySelector('meta[name="google-adsense-account"]');
  if (!adsenseAccountMeta) {
    adsenseAccountMeta = document.createElement("meta");
    adsenseAccountMeta.name = "google-adsense-account";
    document.head.append(adsenseAccountMeta);
  }
  adsenseAccountMeta.content = ADSENSE_ACCOUNT;
  if (!PUBLIC_PATHS.has(path)) return;

  const localizedPath = locale => locale === "tr" ? path : `/${locale}${path === "/" ? "/" : path}`;
  const canonicalUrl = `${PRODUCTION_ORIGIN}${localizedPath(language)}`;
  const description = document.querySelector('meta[name="description"]')?.content
    || document.querySelector(".lead,.detail-hero > p:not(.eyebrow)")?.textContent?.trim()
    || "LectureSift ders videolarını transkript, özet, quiz ve bilgi kartlarına dönüştürür.";
  const imageUrl = `${PRODUCTION_ORIGIN}/og-image.png`;
  const imageAlt = document.title;

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
  document.head.querySelectorAll('meta[property="og:locale:alternate"]').forEach(node => node.remove());
  LANGUAGES.filter(locale => locale !== language).forEach(locale => {
    const node = document.createElement("meta");
    node.setAttribute("property", "og:locale:alternate");
    node.setAttribute("content", OG_LOCALES[locale]);
    document.head.append(node);
  });
  setMeta('meta[property="og:image"]', {property: "og:image", content: imageUrl});
  setMeta('meta[property="og:image:type"]', {property: "og:image:type", content: "image/png"});
  setMeta('meta[property="og:image:width"]', {property: "og:image:width", content: "1731"});
  setMeta('meta[property="og:image:height"]', {property: "og:image:height", content: "909"});
  setMeta('meta[property="og:image:alt"]', {property: "og:image:alt", content: imageAlt});
  setMeta('meta[name="twitter:card"]', {name: "twitter:card", content: "summary_large_image"});
  setMeta('meta[name="twitter:title"]', {name: "twitter:title", content: document.title});
  setMeta('meta[name="twitter:description"]', {name: "twitter:description", content: description});
  setMeta('meta[name="twitter:image"]', {name: "twitter:image", content: imageUrl});
  setMeta('meta[name="twitter:image:alt"]', {name: "twitter:image:alt", content: imageAlt});

  const productionHost = new Set(["lecturesift.com", "www.lecturesift.com"]).has(location.hostname);
  if (!new Set(["lecturesift.com", "www.lecturesift.com", "localhost", "127.0.0.1"]).has(location.hostname)) {
    setMeta('meta[name="robots"]', {name: "robots", content: "noindex,nofollow,noarchive"});
  } else if (productionHost) {
    setMeta('meta[name="robots"]', {name: "robots", content: "index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1"});
  }

  const graph = [
    {
      "@type": "Organization",
      "@id": `${PRODUCTION_ORIGIN}/#organization`,
      name: "LectureSift",
      url: `${PRODUCTION_ORIGIN}/`,
      logo: `${PRODUCTION_ORIGIN}/favicon.svg`,
      image: imageUrl,
      sameAs: ["https://www.instagram.com/lecturesift/"],
    },
    {
      "@type": "WebSite",
      "@id": `${PRODUCTION_ORIGIN}/#website`,
      name: "LectureSift",
      url: `${PRODUCTION_ORIGIN}/`,
      inLanguage: document.documentElement.lang || "tr",
      publisher: {"@id": `${PRODUCTION_ORIGIN}/#organization`},
    },
    {
      "@type": "WebPage",
      "@id": `${canonicalUrl}#webpage`,
      name: document.title,
      url: canonicalUrl,
      description,
      inLanguage: document.documentElement.lang || "tr",
      isPartOf: {"@id": `${PRODUCTION_ORIGIN}/#website`},
      about: {"@id": `${PRODUCTION_ORIGIN}/#application`},
      primaryImageOfPage: {"@type": "ImageObject", url: imageUrl, width: 1731, height: 909},
    },
    {
      "@type": "SoftwareApplication",
      "@id": `${PRODUCTION_ORIGIN}/#application`,
      name: "LectureSift",
      url: `${PRODUCTION_ORIGIN}/`,
      applicationCategory: "EducationalApplication",
      applicationSubCategory: "Study tools and educational content summarization",
      operatingSystem: "Web",
      isAccessibleForFree: true,
      availableLanguage: LANGUAGES,
      description,
      image: imageUrl,
      featureList: [
        "Video ve ses transkripti",
        "PDF, Word, PowerPoint, TXT ve Markdown özetleme",
        "Her kaynak için ayrıntılı ve kapsamlı özet",
        "Quiz soruları ve açıklamalı cevaplar",
        "Bilgi kartları",
        "Slayt eşleştirme",
        "13 dilde çalışma paketi",
        "PDF, DOCX ve TXT dışa aktarma",
      ],
      offers: {"@type": "Offer", price: "0", priceCurrency: "TRY"},
      publisher: {"@id": `${PRODUCTION_ORIGIN}/#organization`},
    },
  ];
  if (path !== "/") {
    const pageName = document.querySelector("h1")?.textContent?.trim() || document.title;
    graph.push({
      "@type": "BreadcrumbList",
      "@id": `${canonicalUrl}#breadcrumb`,
      itemListElement: [
        {"@type": "ListItem", position: 1, name: "LectureSift", item: `${PRODUCTION_ORIGIN}/`},
        {"@type": "ListItem", position: 2, name: pageName, item: canonicalUrl},
      ],
    });
  }
  const questions = [...document.querySelectorAll(".faq-list details")].map(detail => {
    const name = detail.querySelector("summary")?.textContent?.trim();
    const answer = detail.querySelector("p")?.textContent?.trim();
    return name && answer ? {"@type": "Question", name, acceptedAnswer: {"@type": "Answer", text: answer}} : null;
  }).filter(Boolean);
  if (questions.length) graph.push({"@type": "FAQPage", "@id": `${canonicalUrl}#faq`, mainEntity: questions});

  if (new Set(["/features.html", "/document-summary.html", "/lecture-video-summary.html", "/quiz-flashcards.html"]).has(path)) {
    graph.push({
      "@type": "Article",
      "@id": `${canonicalUrl}#article`,
      headline: document.title,
      description,
      image: imageUrl,
      inLanguage: document.documentElement.lang || "tr",
      dateModified: "2026-08-29",
      mainEntityOfPage: {"@id": `${canonicalUrl}#webpage`},
      author: {"@id": `${PRODUCTION_ORIGIN}/#organization`},
      publisher: {"@id": `${PRODUCTION_ORIGIN}/#organization`},
    });
  }

  const schema = {
    "@context": "https://schema.org",
    "@graph": graph,
  };
  let jsonLd = document.head.querySelector('script[type="application/ld+json"][data-lecturesift-seo]');
  if (!jsonLd) {
    jsonLd = document.createElement("script");
    jsonLd.type = "application/ld+json";
    jsonLd.dataset.lecturesiftSeo = "";
    document.head.append(jsonLd);
  }
  jsonLd.textContent = JSON.stringify(schema);
})();
