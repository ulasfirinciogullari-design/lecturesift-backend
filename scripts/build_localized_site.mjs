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

const pageCopySource = await readFile(path.join(SOURCE, "page-i18n.js"), "utf8");
const marker = "window.LECTURESIFT_PAGE_COPY=";
if (!pageCopySource.includes(marker)) throw new Error("Static translation catalog is unavailable");
const catalog = JSON.parse(pageCopySource.split(marker, 2)[1].trim().replace(/;\s*$/, ""));
const dynamicCopySource = await readFile(path.join(SOURCE, "i18n.js"), "utf8");
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

function localizedPath(language, publicPath) {
  if (language === "tr") return publicPath;
  return publicPath === "/" ? `/${language}/` : `/${language}${publicPath}`;
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
    graph.push({
      "@type": "BreadcrumbList", "@id": `${canonical}#breadcrumb`,
      itemListElement: [
        {"@type": "ListItem", position: 1, name: "LectureSift", item: `${ORIGIN}/`},
        {"@type": "ListItem", position: 2, name: title, item: canonical},
      ],
    });
  }
  if (GUIDE_PATHS.has(publicPath)) {
    graph.push({
      "@type": "Article", "@id": `${canonical}#article`, headline: title, description, image,
      inLanguage: language, dateModified: "2026-08-29", mainEntityOfPage: {"@id": webpageId},
      author: {"@id": organizationId}, publisher: {"@id": organizationId},
    });
  }
  const questions = [...html.matchAll(/<details\b[^>]*>[\s\S]*?<summary\b[^>]*>([\s\S]*?)<\/summary>[\s\S]*?<p\b[^>]*>([\s\S]*?)<\/p>[\s\S]*?<\/details>/gi)]
    .map(match => ({
      "@type": "Question", name: plainText(match[1]),
      acceptedAnswer: {"@type": "Answer", text: plainText(match[2])},
    }))
    .filter(item => item.name && item.acceptedAnswer.text);
  if (questions.length) {
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
  const alternates = LANGUAGES.map(alternate =>
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
  <link rel="alternate" hreflang="x-default" href="${ORIGIN}${publicPath}">
  <meta property="og:type" content="${ARTICLE_PATHS.has(publicPath) ? "article" : "website"}">
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
  if (alternateCount !== LANGUAGES.length + 1) {
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

for (const language of LANGUAGES) {
  for (const publicPath of PUBLIC_PATHS) {
    const sourceName = publicPath === "/" ? "index.html" : publicPath.slice(1);
    let html = await readFile(path.join(SOURCE, sourceName), "utf8");
    html = translateDocument(html, language);
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

console.log(`Built ${LANGUAGES.length * PUBLIC_PATHS.length} indexable localized pages in ${OUTPUT}`);
