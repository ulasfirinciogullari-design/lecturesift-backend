import {cp, mkdir, readFile, rm, writeFile} from "node:fs/promises";
import path from "node:path";
import {fileURLToPath} from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE = path.join(ROOT, "frontend");
const OUTPUT = path.join(ROOT, "dist");
const ORIGIN = "https://lecturesift.com";
const LANGUAGES = ["tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi"];
const OG_LOCALES = {tr:"tr_TR",en:"en_US",de:"de_DE",fr:"fr_FR",es:"es_ES",it:"it_IT",pt:"pt_BR",ru:"ru_RU",ar:"ar_SA",zh:"zh_CN",ja:"ja_JP",ko:"ko_KR",hi:"hi_IN"};
const PUBLIC_PATHS = [
  "/", "/features.html", "/document-summary.html", "/lecture-video-summary.html",
  "/quiz-flashcards.html", "/plans.html", "/about.html", "/contact.html",
  "/privacy.html", "/terms.html", "/distance-sales.html", "/cookies.html", "/refund.html",
];
const ARTICLE_PATHS = new Set([
  "/features.html", "/document-summary.html", "/lecture-video-summary.html", "/quiz-flashcards.html",
  "/about.html", "/privacy.html", "/terms.html", "/distance-sales.html", "/cookies.html", "/refund.html",
]);
const GUIDE_PATHS = new Set([
  "/features.html", "/document-summary.html", "/lecture-video-summary.html", "/quiz-flashcards.html",
]);
const PUBLIC_PATH_SET = new Set(PUBLIC_PATHS);
const STUDY_RESOURCES = JSON.parse(await readFile(path.join(SOURCE, "study-resources.json"), "utf8"));
const STUDY_PAGES = new Map(STUDY_RESOURCES.pages.map(page => [`/${page.slug}.html`, page]));
const pageLanguages = publicPath => STUDY_PAGES.has(publicPath) ? STUDY_RESOURCES.languages : LANGUAGES;
for (const publicPath of STUDY_PAGES.keys()) PUBLIC_PATH_SET.add(publicPath);

const pageCopySource = await readFile(path.join(SOURCE, "page-i18n.js"), "utf8");
const marker = "window.LECTURESIFT_PAGE_COPY=";
if (!pageCopySource.includes(marker)) throw new Error("Static translation catalog is unavailable");
const catalog = JSON.parse(pageCopySource.split(marker, 2)[1].trim().replace(/;\s*$/, ""));
const dynamicCopySource = await readFile(path.join(SOURCE, "i18n.js"), "utf8");
const referralCopySource = await readFile(path.join(SOURCE, "referral-i18n.js"), "utf8");
const assistantCopySource = await readFile(path.join(SOURCE, "assistant-i18n.js"), "utf8");
const keyCatalog = {};
for (const match of dynamicCopySource.matchAll(/^\s*,?["']([^"']+)["']\s*:\s*(\[[^\r\n]+\])\s*,?$/gm)) {
  try {
    const row = JSON.parse(match[2]);
    if (Array.isArray(row) && row.length === LANGUAGES.length && row[0]) {
      keyCatalog[match[1]] = row;
      catalog[row[0]] ??= row;
    }
  } catch {
    // A malformed catalog row must not make unrelated static pages undeployable.
    // Runtime coverage tests report the exact source row separately.
  }
}

// Referral copy has its own runtime keys; also translate its static fallback
// text during the public-page build without mixing those keys into data-i18n.
for (const match of referralCopySource.matchAll(/^\s*"[^"]+":(\[[^\r\n]+\]),?$/gm)) {
  const row = JSON.parse(match[1]);
  if (row.length !== LANGUAGES.length || row.some(value => typeof value !== "string" || !value.trim())) {
    throw new Error("Incomplete referral translation row");
  }
  catalog[row[0]] = row;
}

// Assistant and invitation cards are visible before the chat script loads.
for (const match of assistantCopySource.matchAll(/^\s+[a-z]+: (\[[^\r\n]+\]),?$/gm)) {
  const row = JSON.parse(match[1]);
  if (row.length !== LANGUAGES.length || row.some(value => typeof value !== "string" || !value.trim())) {
    throw new Error("Incomplete assistant translation row");
  }
  catalog[row[0]] = row;
}

function canonicalPublicPath(publicPath) {
  if (publicPath === "/" || publicPath === "/index.html") return "/";
  return publicPath.endsWith(".html") ? publicPath.slice(0, -5) : publicPath;
}

function localizedPath(language, publicPath) {
  const canonicalPath = canonicalPublicPath(publicPath);
  if (language === "tr") return canonicalPath;
  return canonicalPath === "/" ? `/${language}/` : `/${language}${canonicalPath}`;
}

function canonicalizePublicLinks(html) {
  return html.replace(/<a\b[^>]*>/gi, tag => tag.replace(/\bhref="(\/[^\"]*)"/i, (match, href) => {
    const boundary = href.search(/[?#]/);
    const pathname = boundary >= 0 ? href.slice(0, boundary) : href;
    const suffix = boundary >= 0 ? href.slice(boundary) : "";
    const segments = pathname.split("/").filter(Boolean);
    const language = LANGUAGES.includes(segments[0]) ? segments.shift() : null;
    const route = segments.length ? `/${segments.join("/")}` : "/";
    const physicalRoute = route === "/index.html" ? "/" : route;
    if (!PUBLIC_PATH_SET.has(physicalRoute)) return match;
    const canonicalRoute = canonicalPublicPath(physicalRoute);
    const localized = language && language !== "tr"
      ? (canonicalRoute === "/" ? `/${language}/` : `/${language}${canonicalRoute}`)
      : canonicalRoute;
    return `href="${localized}${suffix}"`;
  }));
}

function translate(source, language) {
  if (language === "tr") return source;
  const index = LANGUAGES.indexOf(language);
  const row = catalog[String(source || "").trim()];
  return row?.[index] || row?.[1] || source;
}

function escapeAttribute(value) {
  return String(value).replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function translateDocument(html, language) {
  if (language === "tr") return html;
  const languageIndex = LANGUAGES.indexOf(language);
  html = html.replace(/<html\b[^>]*>/i, `<html lang="${language}" dir="${language === "ar" ? "rtl" : "ltr"}">`);
  html = html.replace(/<([a-z][\w-]*)([^>]*\bdata-i18n="([^"]+)"[^>]*)>([^<>]*)<\/\1>/gi, (match, tag, attributes, key, text) => {
    const row = keyCatalog[key];
    const translated = row?.[languageIndex] || row?.[1];
    return translated ? `<${tag}${attributes}>${translated}</${tag}>` : match;
  });
  html = html.replace(/>([^<>]+)</g, (match, text) => {
    const value = text.trim();
    if (!value || !catalog[value]) return match;
    return `>${text.replace(value, translate(value, language))}<`;
  });
  html = html.replace(/\b(aria-label|title|placeholder|alt|content)="([^"]*)"/g, (match, name, value) => {
    return `${name}="${escapeAttribute(translate(value, language))}"`;
  });
  html = html.replace(/<a\b[^>]*>/gi, tag => tag.replace(/\bhref="(\/[^"]*)"/i, (match, href) => {
    if (/\bdata-study-guide\b/.test(tag)) return 'href="/en/study-guides"';
    const boundary = href.search(/[?#]/);
    const pathname = boundary >= 0 ? href.slice(0, boundary) : href;
    const suffix = boundary >= 0 ? href.slice(boundary) : "";
    if (pathname.startsWith(`/${language}/`) || pathname === `/${language}`) return match;
    const localized = pathname === "/" ? `/${language}/` : `/${language}${pathname}`;
    return `href="${localized}${suffix}"`;
  }));
  return html;
}

function deferNonCriticalScripts(html) {
  return html.replace(/<script\b([^>]*\bsrc="[^"]+"[^>]*)><\/script>/gi, (match, attributes) => {
    if (/\bsrc="\/theme\.js\b/i.test(attributes) || /\bdefer\b/i.test(attributes)) return match;
    return `<script defer${attributes}></script>`;
  });
}

function removeStaticTranslationCatalog(html) {
  // Public pages are already translated before they are written to dist. The
  // 1.1 MB all-language fallback catalog is still required by authenticated
  // and workspace screens, but downloading and parsing it again on every
  // indexable page only delays first interaction without changing the page.
  return html.replace(
    /<script\b[^>]*\bsrc="(?:\.\/|\/)?page-i18n\.js(?:\?[^\"]*)?"[^>]*><\/script>\s*/gi,
    "",
  );
}

function readHeadValue(html, pattern, label, publicPath) {
  const value = html.match(pattern)?.[1]?.trim();
  if (!value) throw new Error(`${publicPath} has no ${label}`);
  return value;
}

function plainText(value) {
  return String(value || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function structuredData(html, language, publicPath, canonical, title, description, image) {
  const organizationId = `${ORIGIN}/#organization`;
  const websiteId = `${ORIGIN}/#website`;
  const applicationId = `${ORIGIN}/#application`;
  const webpageId = `${canonical}#webpage`;
  const graph = [
    {
      "@type": "Organization", "@id": organizationId, name: "LectureSift", url: `${ORIGIN}/`,
      logo: `${ORIGIN}/favicon.svg`, image, sameAs: ["https://www.instagram.com/lecturesift/"],
    },
    {
      "@type": "WebSite", "@id": websiteId, name: "LectureSift", url: `${ORIGIN}/`,
      inLanguage: language, publisher: {"@id": organizationId},
    },
    {
      "@type": "WebPage", "@id": webpageId, name: title, url: canonical, description,
      inLanguage: language, isPartOf: {"@id": websiteId}, about: {"@id": applicationId},
      ...(publicPath !== "/" ? {breadcrumb: {"@id": `${canonical}#breadcrumb`}} : {}),
      primaryImageOfPage: {"@type": "ImageObject", url: image, width: 1731, height: 909},
    },
    {
      "@type": "SoftwareApplication", "@id": applicationId, name: "LectureSift", url: `${ORIGIN}/`,
      applicationCategory: "EducationalApplication", operatingSystem: "Web", description, image,
      isAccessibleForFree: true, availableLanguage: LANGUAGES,
      offers: {"@type": "Offer", price: "0", priceCurrency: "TRY"},
      publisher: {"@id": organizationId},
    },
  ];
  if (publicPath !== "/") {
    const items = [
      {"@type": "ListItem", position: 1, name: "LectureSift", item: `${ORIGIN}${localizedPath(language, "/")}`},
    ];
    if (STUDY_PAGES.has(publicPath) && publicPath !== "/study-guides.html") {
      items.push({
        "@type": "ListItem", position: 2,
        name: STUDY_PAGES.get("/study-guides.html")[language].title,
        item: `${ORIGIN}${localizedPath(language, "/study-guides.html")}`,
      });
    }
    items.push({"@type": "ListItem", position: items.length + 1, name: title, item: canonical});
    graph.push({
      "@type": "BreadcrumbList", "@id": `${canonical}#breadcrumb`,
      itemListElement: items,
    });
  }
  if (GUIDE_PATHS.has(publicPath) || STUDY_PAGES.get(publicPath)?.kind === "article") {
    graph.push({
      "@type": "Article", "@id": `${canonical}#article`, headline: title, description, image,
      inLanguage: language, dateModified: STUDY_PAGES.has(publicPath) ? STUDY_RESOURCES.updated : "2026-08-29", mainEntityOfPage: {"@id": webpageId},
      author: {"@id": organizationId}, publisher: {"@id": organizationId},
    });
  }
  const questions = [...html.matchAll(/<details\b[^>]*>[\s\S]*?<summary\b[^>]*>([\s\S]*?)<\/summary>[\s\S]*?<p\b[^>]*>([\s\S]*?)<\/p>[\s\S]*?<\/details>/gi)]
    .map(match => ({
      "@type": "Question", name: plainText(match[1]),
      acceptedAnswer: {"@type": "Answer", text: plainText(match[2])},
    }))
    .filter(item => item.name && item.acceptedAnswer.text);
  if (questions.length && !STUDY_PAGES.has(publicPath)) {
    graph.push({"@type": "FAQPage", "@id": `${canonical}#faq`, mainEntity: questions});
  }
  return JSON.stringify({"@context": "https://schema.org", "@graph": graph}).replaceAll("</", "<\\/");
}

function staticSeo(html, language, publicPath) {
  const canonical = `${ORIGIN}${localizedPath(language, publicPath)}`;
  const title = readHeadValue(html, /<title(?:\s[^>]*)?>(.*?)<\/title>/is, "title", publicPath);
  const description = html.match(/<meta\s+name="description"\s+content="([^"]+)"\s*\/?\s*>/i)?.[1]?.trim()
    || html.match(/<p\b[^>]*class="[^"]*\blead\b[^"]*"[^>]*>([^<]+)<\/p>/i)?.[1]?.trim();
  if (!description) throw new Error(`${publicPath} has no meta description or introductory lead`);
  const alternates = pageLanguages(publicPath).map(alternate =>
    `  <link rel="alternate" hreflang="${alternate}" href="${ORIGIN}${localizedPath(alternate, publicPath)}">`
  ).join("\n");
  const image = `${ORIGIN}/og-image.png`;
  const alt = escapeAttribute(title);
  const schema = structuredData(html, language, publicPath, canonical, title, description, image);
  const staticDescription = /<meta\s+name="description"/i.test(html)
    ? ""
    : `  <meta name="description" content="${escapeAttribute(description)}">\n`;
  const metadata = `
${staticDescription}
  <link rel="canonical" href="${canonical}">
${alternates}
  <link rel="alternate" hreflang="x-default" href="${ORIGIN}${localizedPath("tr", publicPath)}">
  <meta property="og:type" content="${ARTICLE_PATHS.has(publicPath) || STUDY_PAGES.get(publicPath)?.kind === "article" ? "article" : "website"}">
  <meta property="og:site_name" content="LectureSift">
  <meta property="og:title" content="${escapeAttribute(title)}">
  <meta property="og:description" content="${escapeAttribute(description)}">
  <meta property="og:url" content="${canonical}">
  <meta property="og:locale" content="${OG_LOCALES[language]}">
  <meta property="og:image" content="${image}">
  <meta property="og:image:type" content="image/png">
  <meta property="og:image:width" content="1731">
  <meta property="og:image:height" content="909">
  <meta property="og:image:alt" content="${alt}">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="${escapeAttribute(title)}">
  <meta name="twitter:description" content="${escapeAttribute(description)}">
  <meta name="twitter:image" content="${image}">
  <meta name="twitter:image:alt" content="${alt}">
  <script type="application/ld+json" data-lecturesift-seo>${schema}</script>
`;
  return html.replace(/<\/head>/i, `${metadata}</head>`);
}

function validateLocalizedPage(html, language, publicPath) {
  const expectedCanonical = `${ORIGIN}${localizedPath(language, publicPath)}`;
  const requirements = [
    [new RegExp(`<html\\s+lang="${language}"`, "i"), "document language"],
    [/<meta\s+name="description"\s+content="[^"]+"/i, "meta description"],
    [new RegExp(`<link\\s+rel="canonical"\\s+href="${expectedCanonical.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"`, "i"), "canonical URL"],
    [/<h1\b[^>]*>[\s\S]*?<\/h1>/i, "visible H1"],
    [/<script\s+type="application\/ld\+json"\s+data-lecturesift-seo>/i, "JSON-LD graph"],
  ];
  for (const [pattern, label] of requirements) {
    if (!pattern.test(html)) throw new Error(`${localizedPath(language, publicPath)} has no ${label}`);
  }
  const alternateCount = (html.match(/<link\s+rel="alternate"\s+hreflang=/gi) || []).length;
  if (alternateCount !== pageLanguages(publicPath).length + 1) {
    throw new Error(`${localizedPath(language, publicPath)} has ${alternateCount} hreflang links`);
  }
  const schemaSource = html.match(/<script\s+type="application\/ld\+json"\s+data-lecturesift-seo>([\s\S]*?)<\/script>/i)?.[1];
  JSON.parse(schemaSource || "");
  if (/\bsrc="(?:\.\/|\/)?page-i18n\.js\b/i.test(html)) {
    throw new Error(`${localizedPath(language, publicPath)} still loads the static translation catalog`);
  }
}

await rm(OUTPUT, {recursive: true, force: true});
await cp(SOURCE, OUTPUT, {recursive: true});
// Editorial translations are published only in the languages actually written.
// Do not index copies of Turkish content under unrelated language paths.
await rm(path.join(OUTPUT, "study-resources.json"));

for (const language of LANGUAGES) {
  for (const publicPath of PUBLIC_PATHS) {
    const sourceName = publicPath === "/" ? "index.html" : publicPath.slice(1);
    let html = await readFile(path.join(SOURCE, sourceName), "utf8");
    html = translateDocument(html, language);
    html = canonicalizePublicLinks(html);
    html = removeStaticTranslationCatalog(html);
    html = deferNonCriticalScripts(html);
    html = staticSeo(html, language, publicPath);
    validateLocalizedPage(html, language, publicPath);
    const target = language === "tr"
      ? path.join(OUTPUT, sourceName)
      : path.join(OUTPUT, language, sourceName);
    await mkdir(path.dirname(target), {recursive: true});
    await writeFile(target, html, "utf8");
  }
}

function studyDocument(page, language) {
  const copy = page[language];
  if (!copy?.title || !copy?.description || !copy?.body) throw new Error(`Missing ${language} study resource: ${page.slug}`);
  const tr = language === "tr";
  const library = page.slug === "study-guides";
  const prefix = tr ? "" : "/en";
  const alternate = tr ? `/en/${page.slug}` : `/${page.slug}`;
  const toc = [...copy.body.matchAll(/<section id="([^"]+)"[^>]*>\s*<h2>([^<]+)<\/h2>/g)]
    .map(([, id, title]) => `<li><a href="#${escapeAttribute(id)}">${title}</a></li>`).join("");
  const updated = new Intl.DateTimeFormat(tr ? "tr-TR" : "en-GB", {
    day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
  }).format(new Date(`${STUDY_RESOURCES.updated}T00:00:00Z`));
  const minutes = Math.max(2, Math.ceil(plainText(copy.body).split(/\s+/).length / 190));
  const brand = `<span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></span><span>Lecture<span>Sift</span></span>`;
  const preview = `<figure class="guide-preview"><figcaption><span class="guide-preview-dot" aria-hidden="true"></span>${tr ? "Örnek dersin içinden" : "Inside the example lesson"}</figcaption><h2>${tr ? "Aynı veri.<br>İki farklı bakış." : "Same data.<br>Two ways to see it."}</h2><p>${tr ? "Okula gidiş süresi · dakika" : "Journey to school · minutes"}</p><div class="guide-preview-data" aria-label="${tr ? "18, 20, 20, 22 ve 70 dakika" : "18, 20, 20, 22, and 70 minutes"}"><span>18</span><span>20</span><span class="guide-median">20</span><span>22</span><span>70</span></div><dl><div><dt>${tr ? "Ortalama" : "Mean"}</dt><dd>30 <small>${tr ? "dk" : "min"}</small></dd></div><div><dt>${tr ? "Medyan" : "Median"}</dt><dd>20 <small>${tr ? "dk" : "min"}</small></dd></div></dl><a href="${prefix}/study-pack-example">${tr ? "Neden farklılar? Örnekte keşfet" : "Why the difference? Explore the example"} <span aria-hidden="true">↗</span></a></figure>`;
  const hero = `<header class="guide-intro${library ? " guide-hero" : ""}"><div><p class="guide-eyebrow">${tr ? "LectureSift · Öğrenme kütüphanesi" : "LectureSift · Learning library"}</p><h1>${escapeAttribute(copy.title)}</h1><p class="guide-lead">${escapeAttribute(copy.description)}</p>${library ? `<div class="guide-hero-actions"><a class="guide-button guide-button-primary" href="${prefix}/study-pack-example">${tr ? "Örnek dersi aç" : "Try the example lesson"} <span aria-hidden="true">→</span></a><a class="guide-text-link" href="#${tr ? "basla" : "start"}">${tr ? "Rehber seç" : "Choose a guide"} ↓</a></div><p class="guide-access">${tr ? "Ücretsiz rehberler · Hesap veya kredi gerekmez" : "Free guides · No account or credits needed"}</p>` : `<p class="guide-byline"><span>LectureSift</span><span>${minutes} ${tr ? "dk okuma" : "min read"}</span><time datetime="${STUDY_RESOURCES.updated}">${updated}</time></p>`}</div>${library ? preview : ""}</header>`;
  const related = library ? "" : `<nav class="guide-related" aria-label="${tr ? "Diğer çalışma rehberleri" : "More study guides"}"><strong>${tr ? "Çalışmaya devam et" : "Keep learning"}</strong>${STUDY_RESOURCES.pages.filter(item => item.kind === "article" && item.slug !== page.slug && item.slug !== "about-study-guides").map(item => `<a href="${prefix}/${item.slug}">${escapeAttribute(item[language].title)} <span aria-hidden="true">→</span></a>`).join("")}</nav>`;
  return `<!doctype html><html lang="${language}" dir="ltr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapeAttribute(copy.title)} | LectureSift</title><meta name="description" content="${escapeAttribute(copy.description)}">
<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/styles.css?v=12"><link rel="stylesheet" href="/theme.css?v=17"><link rel="stylesheet" href="/product-layout.css?v=4"><link rel="stylesheet" href="/study-guides.css?v=2"><script src="/theme.js?v=11"></script></head>
<body class="guide-page"><a class="skip-link guide-skip" href="#content">${tr ? "İçeriğe geç" : "Skip to content"}</a>
<header class="guide-header"><div class="guide-header-inner"><a class="brand guide-brand" href="${prefix}/" aria-label="LectureSift ${tr ? "ana sayfa" : "home"}">${brand}</a><nav aria-label="${tr ? "Ana menü" : "Main navigation"}"><a href="${prefix}/study-guides" aria-label="${tr ? "Çalışma rehberleri" : "Study guides"}"${library ? ' aria-current="page"' : ""}><span class="guide-nav-full">${tr ? "Çalışma rehberleri" : "Study guides"}</span><span class="guide-nav-compact" aria-hidden="true">${tr ? "Rehberler" : "Guides"}</span></a><a href="${prefix}/about-study-guides" aria-label="${tr ? "Rehberler hakkında" : "About the guides"}"${page.slug === "about-study-guides" ? ' aria-current="page"' : ""}><span class="guide-nav-full">${tr ? "Rehberler hakkında" : "About the guides"}</span><span class="guide-nav-compact" aria-hidden="true">${tr ? "Hakkında" : "About"}</span></a><a class="guide-language" href="${alternate}" hreflang="${tr ? "en" : "tr"}" lang="${tr ? "en" : "tr"}">${tr ? "English" : "Türkçe"}</a></nav><a class="guide-button guide-button-primary guide-header-cta" href="${prefix}/workspace.html">${tr ? "Çalışma alanı" : "Workspace"} <span aria-hidden="true">↗</span></a></div></header>
<main class="guide-layout${library ? " guide-library" : ""}" id="content">${library ? "" : `<nav class="guide-breadcrumb" aria-label="${tr ? "Sayfa konumu" : "Breadcrumb"}"><a href="${prefix}/study-guides">← ${tr ? "Çalışma rehberleri" : "Study guides"}</a><span aria-hidden="true">/</span><span>${escapeAttribute(copy.title)}</span></nav>`}<article class="guide-article">${hero}${copy.body}${related}</article>${library ? "" : `<aside class="guide-toc" aria-label="${tr ? "İçindekiler" : "Contents"}"><details><summary>${tr ? "Bu sayfada" : "On this page"}</summary><ol>${toc}</ol></details><a class="guide-toc-home" href="${prefix}/study-guides">${tr ? "Tüm rehberler →" : "All guides →"}</a></aside>`}</main>
<footer class="guide-footer"><div class="guide-footer-top"><div><a class="brand guide-brand" href="${prefix}/">${brand}</a><p>${tr ? "Derslerin bir arada. Aklın öğrenmede." : "Your lessons together. Your mind on learning."}</p></div><nav aria-label="${tr ? "Alt menü" : "Footer navigation"}"><a href="${prefix}/study-guides">${tr ? "Çalışma rehberleri" : "Study guides"}</a><a data-guide-about href="${prefix}/about-study-guides">${tr ? "Rehberler hakkında" : "About these guides"}</a><a href="${prefix}/about">${tr ? "Hakkımızda" : "About LectureSift"}</a><a href="${prefix}/contact">${tr ? "Hata bildir / İletişim" : "Report an error / Contact"}</a><a href="${prefix}/privacy">${tr ? "Gizlilik" : "Privacy"}</a><a href="${prefix}/terms">${tr ? "Kullanım koşulları" : "Terms"}</a></nav></div><p class="guide-footer-note">© 2026 LectureSift · ${tr ? "Yapay zekâ yardımıyla hazırlanmış eğitim kaynakları. Örnek veriler öğretim amaçlıdır." : "Educational resources prepared with AI assistance. Example data are illustrative."}</p></footer></body></html>`;
}

for (const [publicPath, page] of STUDY_PAGES) {
  for (const language of STUDY_RESOURCES.languages) {
    let html = studyDocument(page, language);
    html = staticSeo(html, language, publicPath);
    validateLocalizedPage(html, language, publicPath);
    const target = path.join(OUTPUT, language === "tr" ? "" : language, `${page.slug}.html`);
    await mkdir(path.dirname(target), {recursive: true});
    await writeFile(target, html, "utf8");
  }
}
console.log(`Built ${LANGUAGES.length * PUBLIC_PATHS.length + STUDY_PAGES.size * STUDY_RESOURCES.languages.length} indexable pages in ${OUTPUT}`);
