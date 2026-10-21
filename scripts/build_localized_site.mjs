import {cp, mkdir, readdir, readFile, rm, writeFile} from "node:fs/promises";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {createHash} from "node:crypto";
import {Script} from "node:vm";

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
const LANDING_PAGES = JSON.parse(await readFile(path.join(SOURCE, "landing-pages.json"), "utf8"));
const STUDY_PAGES = new Map(STUDY_RESOURCES.pages.map(page => [`/${page.slug}.html`, page]));
const pageLanguages = publicPath => STUDY_PAGES.has(publicPath) ? STUDY_RESOURCES.languages : LANGUAGES;
// Subject (course) pages are authored TR/EN articles that also carry a visible FAQ.
const COURSE_PAGES = STUDY_RESOURCES.pages.filter(page => page.kind === "course");
const TOPIC_PAGES = STUDY_RESOURCES.pages.filter(page => page.kind === "topic");
const studyArticle = publicPath => ["article", "course", "topic"].includes(STUDY_PAGES.get(publicPath)?.kind);
for (const publicPath of STUDY_PAGES.keys()) PUBLIC_PATH_SET.add(publicPath);

const pageCopySource = await readFile(path.join(SOURCE, "page-i18n.js"), "utf8");
const marker = "window.LECTURESIFT_PAGE_COPY=";
if (!pageCopySource.includes(marker)) throw new Error("Static translation catalog is unavailable");
// page-i18n.js stays the edited source. The per-language browser catalogs are
// generated from this unmerged copy; `catalog` also gains the runtime rows below.
const pageCopy = JSON.parse(pageCopySource.split(marker, 2)[1].trim().replace(/;\s*$/, ""));
const catalog = {...pageCopy};
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

function documentTrialHero(language) {
  const index = LANGUAGES.indexOf(language);
  const text = key => {
    const value = keyCatalog[`document.${key}`]?.[index];
    if (!value) throw new Error(`Missing document trial copy: ${language}/${key}`);
    return escapeAttribute(value);
  };
  const keyed = (tag, key, attributes = "") => `<${tag}${attributes} data-i18n="document.${key}">${text(key)}</${tag}>`;
  const link = route => localizedPath(language, route);
  const fullExample = ["tr", "en"].includes(language)
    ? `<a class="pdf-trial-detail-link" href="#example">${language === "tr" ? "Tam örneği ve çözümlü soruyu incele" : "Explore the full example and worked question"}</a>` : "";
  return `<section class="detail-hero pdf-trial-hero" data-document-trial-hero aria-labelledby="document-trial-title">
    <div class="pdf-trial-intro"><p class="eyebrow">${escapeAttribute(translate("PDF VE BELGE ÖZETLEME", language))}</p>
      ${keyed("h1", "sampleHeading", ' id="document-trial-title"')}
      ${keyed("p", "sampleLead", ' class="pdf-trial-lead"')}
      <div class="detail-actions"><a class="primary" data-i18n="document.sampleAction" href="${link("/workspace.html")}#classicDropZone">${text("sampleAction")}</a><a class="secondary" data-i18n="document.sampleExampleAction" href="#document-example">${text("sampleExampleAction")}</a></div>
      ${keyed("p", "sampleLimitSummary", ' class="pdf-trial-limits" data-document-trial-tip')}
      <a class="pdf-trial-plans" data-i18n="document.samplePlans" href="${link("/plans.html")}">${text("samplePlans")}</a>
    </div>
    <aside class="pdf-trial-example" id="document-example" aria-labelledby="document-example-title">
      ${keyed("h2", "sampleExampleTitle", ' id="document-example-title"')}
      ${keyed("p", "sampleExampleSource", ' class="pdf-trial-source-label"')}
      <p class="pdf-trial-numbers" dir="ltr">18, 20, 20, 22, 70</p>
      <div class="pdf-trial-example-notes">${keyed("p", "sampleExampleMean")}${keyed("p", "sampleExampleMedian")}</div>
      ${keyed("p", "sampleExampleNotice", ' class="pdf-trial-example-notice"')}${fullExample}
    </aside>
  </section>`;
}

function enrichLandingPage(html, language, publicPath) {
  const copy = LANDING_PAGES.pages[publicPath]?.[language];
  // Publish richer editions only where the editorial copy actually exists.
  if (!copy) return publicPath === "/document-summary.html"
    ? html.replace(/<section\b[^>]*data-document-trial-hero[^>]*>[\s\S]*?<\/section>/i, () => documentTrialHero(language)) : html;
  const e = escapeAttribute;
  const link = route => localizedPath(language, route);
  const tr = language === "tr";
  const updated = new Intl.DateTimeFormat(tr ? "tr-TR" : "en-GB", {
    day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
  }).format(new Date(`${LANDING_PAGES.updated}T00:00:00Z`));
  const actions = `<div class="detail-actions"><a class="primary" href="${link("/workspace.html")}">${e(copy.action)}</a><a class="secondary" href="${link("/plans.html")}">${tr ? "Planları ve kullanım haklarını gör" : "See plans and allowances"}</a></div>`;
  const hero = publicPath === "/document-summary.html" ? documentTrialHero(language)
    : `<section class="detail-hero landing-hero"><p class="eyebrow">${e(copy.eyebrow)}</p><h1>${e(copy.heading)}</h1><p>${e(copy.lead)}</p>${actions}<a class="landing-example-link" href="#example">${tr ? "Önce örneği incele ↓" : "Explore the example first ↓"}</a></section>`;
  const main = `<main class="detail-main landing-page" data-landing-page>
    ${hero}
    <section class="detail-section landing-example" id="example" aria-labelledby="example-title"><p class="eyebrow">${tr ? "KAYNAKTAN ÇALIŞMA NOTUNA" : "FROM SOURCE TO STUDY NOTE"}</p><h2 id="example-title">${e(copy.example.title)}</h2><p class="intro">${tr ? "Aşağıdaki metin ve cevaplar öğretim amacıyla hazırlanmış örneklerdir; canlı işlem sonucu değildir." : "The text and answers below are teaching examples, not the result of a live processing job."}</p><div class="landing-example-grid"><div class="landing-source"><h3>${e(copy.example.sourceTitle)}</h3><blockquote>${e(copy.example.source)}</blockquote></div><div class="landing-outputs">${copy.example.outputs.map(item => `<article><h3>${e(item.title)}</h3><p>${e(item.text)}</p></article>`).join("")}</div></div><details class="landing-answer"><summary>${e(copy.example.question)}</summary><p>${e(copy.example.answer)}</p></details></section>
    <section class="detail-section"><h2>${e(copy.stepsTitle)}</h2><ol class="landing-steps">${copy.steps.map(item => `<li><h3>${e(item.title)}</h3><p>${e(item.text)}</p></li>`).join("")}</ol></section>
    <div class="landing-explain">${copy.sections.map(item => `<section class="detail-section"><h2>${e(item.title)}</h2><p>${e(item.text)}</p></section>`).join("")}</div>
    <section class="detail-section"><h2>${tr ? "Sık sorulan sorular" : "Frequently asked questions"}</h2><div class="faq-list">${copy.faqs.map(item => `<details><summary>${e(item.question)}</summary><p>${e(item.answer)}</p></details>`).join("")}</div></section>
    <section class="detail-section landing-related"><h2>${tr ? "Bir sonraki çalışma adımın" : "Your next study step"}</h2><div class="landing-related-grid">${copy.related.map(item => `<a href="${link(item.path)}"><h3>${e(item.title)}</h3><p>${e(item.text)}</p><span aria-hidden="true">↗</span></a>`).join("")}</div></section>
    <section class="detail-section landing-closing"><h2>${e(copy.closing)}</h2><p>${e(copy.closingText)}</p>${actions}</section>
    <p class="landing-updated">${tr ? "İçerik güncellemesi" : "Content updated"}: <time datetime="${LANDING_PAGES.updated}">${updated}</time></p>
  </main>`;
  if (!/<main\b[^>]*>[\s\S]*?<\/main>/i.test(html)) throw new Error(`Landing page has no main: ${publicPath}`);
  return html
    .replace(/<title[^>]*>[\s\S]*?<\/title>/i, () => `<title>${e(copy.title)}</title>`)
    .replace(/<meta\s+name="description"\s+content="[^"]*"\s*\/?\s*>/i, () => `<meta name="description" content="${e(copy.description)}">`)
    .replace(/<\/head>/i, `<meta property="article:modified_time" content="${LANDING_PAGES.updated}"><link rel="stylesheet" href="/landing-pages.css?v=1"></head>`)
    .replace(/<main\b[^>]*>[\s\S]*?<\/main>/i, () => main);
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

// plans.html writes its static plan numbers in Turkish format ("₺1.199",
// "4.000"). Reformat them for each language so a reader or crawler never sees
// ₺1.199 as about one lira; plans.js later replaces them with live catalog data.
function localizeStaticPlanValues(html, language) {
  if (language === "tr") return html;
  const locale = OG_LOCALES[language].replace("_", "-");
  const number = new Intl.NumberFormat(locale);
  const lira = new Intl.NumberFormat(locale, {
    style: "currency", currency: "TRY", currencyDisplay: "narrowSymbol",
    minimumFractionDigits: 0, maximumFractionDigits: 0,
  });
  return html.replace(/(<span\b[^>]*\bdata-plan-value="[^"]+"[^>]*>)([^<>]*)(<\/span>)/gi, (match, open, value, close) => {
    const text = value.trim();
    const price = text.startsWith("₺");
    const digits = price ? text.slice(1) : text;
    if (!/^\d{1,3}(?:\.\d{3})*$/.test(digits)) return match;
    const amount = Number(digits.replaceAll(".", ""));
    const formatted = price
      ? lira.formatToParts(amount).map(part => part.type === "currency" ? "₺" : part.value).join("")
      : number.format(amount);
    return `${open}${formatted}${close}`;
  });
}

// Static sign-in links carry a continuation target such as next=/plans.html?...
// Keep the reader on their language after sign-in.
function localizeContinuationTargets(html, language) {
  if (language === "tr") return html;
  return html.replace(/<a\b[^>]*>/gi, tag => tag.replace(/\bhref="([^"]*[?&]next=)([^"&#]*)/i, (match, prefix, encoded) => {
    let next;
    try { next = decodeURIComponent(encoded); } catch { return match; }
    if (!next.startsWith("/") || next.startsWith("//")) return match;
    const boundary = next.search(/[?#]/);
    const pathname = boundary >= 0 ? next.slice(0, boundary) : next;
    const suffix = boundary >= 0 ? next.slice(boundary) : "";
    if (!PUBLIC_PATH_SET.has(pathname)) return match;
    return `href="${prefix}${encodeURIComponent(localizedPath(language, pathname) + suffix)}`;
  }));
}

function removeStaticTranslationCatalog(html) {
  // Public pages are already translated before they are written to dist, so
  // downloading and parsing a page-copy catalog again on every indexable page
  // would only delay first interaction without changing the page.
  return html.replace(
    /<script\b[^>]*\bsrc="(?:\.\/|\/)?page-i18n\.js(?:\?[^\"]*)?"[^>]*><\/script>\s*/gi,
    "",
  );
}

// App pages are one file shared by every language URL and choose their
// language in the browser, so they cannot be translated here. Each language
// gets its own catalog with the same window.LECTURESIFT_PAGE_COPY shape that
// i18n.js reads: row[index] holds the value i18n.js would have resolved from
// the full row (row[index] || row[1]). Turkish needs a row only where it
// differs from its own source text, which is normally never.
function pageCopyCatalogSource(language) {
  const index = LANGUAGES.indexOf(language);
  const copy = {};
  for (const [source, row] of Object.entries(pageCopy)) {
    const value = row[index] || row[1] || "";
    if (language !== "tr" || value !== source) copy[source] = value;
  }
  if (!Object.keys(copy).length) return null;
  return `// Generated from page-i18n.js by scripts/build_localized_site.mjs. Do not edit.
(function () {
  const index = ${index}, copy = ${JSON.stringify(copy)}, rows = {};
  for (const source in copy) {
    const row = [source];
    row[index] = copy[source];
    rows[source] = row;
  }
  window.LECTURESIFT_PAGE_COPY = rows;
})();
`;
}

// A loader runs i18n.js's own routing and language-choice code, copied
// verbatim so it cannot disagree with i18n.js, then writes the chosen script
// as a parser-blocking script that still runs before the next tag on the page.
// The page-copy and runtime loaders are both generated here from that copy.
function languageLoaderSource(scriptPaths, filename) {
  const copyFromRuntime = (start, end, label) => {
    const from = dynamicCopySource.indexOf(start);
    const to = from < 0 ? -1 : dynamicCopySource.indexOf(end, from);
    if (to < 0) throw new Error(`i18n.js has no ${label}`);
    return dynamicCopySource.slice(from, to);
  };
  const routing = copyFromRuntime("const codes = ", "const rows = {", "language routing").trim();
  const choice = `${copyFromRuntime("const selected = (() => {", "})();", "language choice")}})();`;
  const source = `// Generated from i18n.js by scripts/build_localized_site.mjs. Do not edit.
(function () {
  ${routing}
  ${choice}
  const script = ${JSON.stringify(scriptPaths)}[selected];
  if (script) document.write('<script src="' + script + '"><\\/script>');
})();
`;
  // A change in i18n.js can cut the copy short. Fail the deploy instead of
  // publishing a loader that leaves every app page untranslated.
  new Script(source, {filename});
  return source;
}

async function writeContentAddressedScript(directory, name, source) {
  const hash = createHash("sha256").update(source).digest("hex").slice(0, 12);
  const publicPath = `/assets/${directory}/${name}-${hash}.js`;
  await mkdir(path.join(OUTPUT, "assets", directory), {recursive: true});
  await writeFile(path.join(OUTPUT, publicPath.slice(1)), source, "utf8");
  return publicPath;
}

const PAGE_COPY_SCRIPT = /<script\b[^>]*\bsrc="(?:\.\/|\/)?page-i18n\.js(?:\?[^"]*)?"[^>]*><\/script>/gi;
const RUNTIME_SCRIPT = /<script\b[^>]*\bsrc="(?:\.\/|\/)?i18n\.js(?:\?[^"]*)?"[^>]*><\/script>/gi;
const FULL_RUNTIME_REFERENCE = /\bsrc="(?:[^"]*\/)?i18n\.js\b/i;
// App pages that deliberately keep the all-language /i18n.js, each with its
// reason. None needs it today: app scripts read the runtime only through t()
// and exact() in the reader's language, and every language switch loads the
// page again. The one switch app.js also applies in place is to Turkish on the
// unprefixed URL, and Turkish is the source text every runtime file keeps.
const FULL_RUNTIME_PAGES = new Map();

// Swaps an all-language script on every app page for its generated loader.
async function loadReaderLanguage(pattern, name, loaderPath, keep = new Map()) {
  for (const entry of await readdir(SOURCE, {withFileTypes: true})) {
    if (!entry.isFile() || !entry.name.endsWith(".html")) continue;
    if (entry.name === "index.html" || PUBLIC_PATH_SET.has(`/${entry.name}`) || keep.has(entry.name)) continue;
    const target = path.join(OUTPUT, entry.name);
    const html = await readFile(target, "utf8");
    const updated = html.replace(pattern, tag => {
      // document.write is ignored when it comes from a deferred or async script.
      if (!/^<script src="[^"]+"><\/script>$/.test(tag)) {
        throw new Error(`${entry.name} must load ${name} as a plain blocking script`);
      }
      return `<script src="${loaderPath}"></script>`;
    });
    if (updated !== html) await writeFile(target, updated, "utf8");
  }
}

function publicRuntimeSource(language) {
  const index = LANGUAGES.indexOf(language);
  let count = 0;
  const source = dynamicCopySource.replace(/^(\s*,?["'][^"']+["']\s*:\s*)(\[[^\r\n]+\])(\s*,?)$/gm, (match, key, values, suffix) => {
    const row = JSON.parse(values);
    if (!Array.isArray(row) || row.length !== LANGUAGES.length) throw new Error(`Invalid runtime translation: ${key}`);
    // Runtime lookup uses the original language index and Turkish exact-text
    // keys. Retain those plus the English fallback; other editions navigate.
    const selected = Array(Math.max(2, index + 1)).fill(null);
    selected[0] = row[0];
    selected[1] = row[1];
    selected[index] = row[index];
    count += 1;
    return `${key}${JSON.stringify(selected)}${suffix}`;
  });
  if (!count) throw new Error("Runtime translation catalog is unavailable");
  return source;
}

function readHeadValue(html, pattern, label, publicPath) {
  const value = html.match(pattern)?.[1]?.trim();
  if (!value) throw new Error(`${publicPath} has no ${label}`);
  return value;
}

function plainText(value) {
  return String(value || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

// The application is one shared entity. Its description comes from the product
// homepage, while each WebPage and Article retains its own subject description.
const productDescription = readHeadValue(
  await readFile(path.join(SOURCE, "index.html"), "utf8"),
  /<meta\s+name="description"\s+content="([^"]+)"\s*\/?\s*>/i,
  "product description", "/",
);

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
      primaryImageOfPage: {"@type": "ImageObject", url: image, width: 1200, height: 630},
    },
    {
      "@type": "SoftwareApplication", "@id": applicationId, name: "LectureSift", url: `${ORIGIN}/`,
      applicationCategory: "EducationalApplication", operatingSystem: "Web",
      description: translate(productDescription, language), image,
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
  if (GUIDE_PATHS.has(publicPath) || studyArticle(publicPath)) {
    graph.push({
      "@type": "Article", "@id": `${canonical}#article`, headline: title, description, image,
      inLanguage: language, dateModified: LANDING_PAGES.pages[publicPath]?.[language] ? LANDING_PAGES.updated : STUDY_PAGES.has(publicPath) ? (STUDY_PAGES.get(publicPath).updated || STUDY_RESOURCES.updated) : "2026-08-29", mainEntityOfPage: {"@id": webpageId},
      author: {"@id": organizationId}, publisher: {"@id": organizationId},
    });
  }
  const faqHtml = html.match(/<div\b[^>]*class="[^"]*\bfaq-list\b[^"]*"[^>]*>([\s\S]*?)<\/div>/i)?.[1] || "";
  const questions = [...faqHtml.matchAll(/<details\b[^>]*>[\s\S]*?<summary\b[^>]*>([\s\S]*?)<\/summary>[\s\S]*?<p\b[^>]*>([\s\S]*?)<\/p>[\s\S]*?<\/details>/gi)]
    .map(match => ({
      "@type": "Question", name: plainText(match[1]),
      acceptedAnswer: {"@type": "Answer", text: plainText(match[2])},
    }))
    .filter(item => item.name && item.acceptedAnswer.text);
  // Guide exercises use <details> for answers, so only course and topic pages' faq-list is a FAQ.
  if (questions.length && (!STUDY_PAGES.has(publicPath) || ["course", "topic"].includes(STUDY_PAGES.get(publicPath).kind))) {
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
  const image = `${ORIGIN}/og-image.jpg`;
  const alt = escapeAttribute(title);
  const schema = structuredData(html, language, publicPath, canonical, title, description, image);
  const staticDescription = /<meta\s+name="description"/i.test(html)
    ? ""
    : `  <meta name="description" content="${escapeAttribute(description)}">\n`;
  const metadata = `
${staticDescription}
  <meta name="robots" content="max-image-preview:large,max-snippet:-1,max-video-preview:-1">
  <link rel="canonical" href="${canonical}">
${alternates}
  <link rel="alternate" hreflang="x-default" href="${ORIGIN}${localizedPath("tr", publicPath)}">
  <meta property="og:type" content="${ARTICLE_PATHS.has(publicPath) || studyArticle(publicPath) ? "article" : "website"}">
  <meta property="og:site_name" content="LectureSift">
  <meta property="og:title" content="${escapeAttribute(title)}">
  <meta property="og:description" content="${escapeAttribute(description)}">
  <meta property="og:url" content="${canonical}">
  <meta property="og:locale" content="${OG_LOCALES[language]}">
  <meta property="og:image" content="${image}">
  <meta property="og:image:type" content="image/jpeg">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
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

// Seller identity is rendered into the static HTML so it stays visible without
// JavaScript or the API. Values come from the Netlify build environment under
// the backend's names: they are never committed or sent to a translation
// service. Like /billing/operator, nothing is rendered until every required
// field is set; legal-operator.js still refreshes the block from the API.
const buildEnv = name => String(process.env[name] || "").trim();
const OPERATOR = {
  operatorName: buildEnv("LEGAL_OPERATOR_NAME"),
  address: buildEnv("LEGAL_OPERATOR_ADDRESS"),
  country: buildEnv("LEGAL_OPERATOR_COUNTRY"),
  phone: buildEnv("LEGAL_OPERATOR_PHONE"),
  // Only keys listed in netlify.toml SECRETS_SCAN_OMIT_KEYS may reach dist/;
  // the literal default matches the backend's final fallback address.
  email: buildEnv("LEGAL_OPERATOR_EMAIL") || "support@lecturesift.com",
  taxId: buildEnv("LEGAL_TAX_ID"),
  taxOffice: buildEnv("LEGAL_TAX_OFFICE"),
  registrationId: buildEnv("LEGAL_REGISTRATION_ID"),
  mersisId: buildEnv("LEGAL_MERSIS_ID"),
  tradeRegistry: buildEnv("LEGAL_TRADE_REGISTRY"),
  kepAddress: buildEnv("LEGAL_KEP_ADDRESS"),
  chamberName: buildEnv("LEGAL_CHAMBER_NAME"),
};
const OPERATOR_CONFIGURED = ["operatorName", "address", "country", "phone", "email"].every(field => OPERATOR[field]);
const OPERATOR_PAGES = new Set(["/distance-sales.html", "/contact.html"]);

function keyedCopy(key, language) {
  const row = keyCatalog[key];
  const value = row?.[LANGUAGES.indexOf(language)] || row?.[1];
  if (!value) throw new Error(`Missing operator copy: ${language}/${key}`);
  return value;
}

function insertAfterLead(html, addition, publicPath) {
  const lead = /<article\b[^>]*\bclass="legal-card"[^>]*>[\s\S]*?<p\b[^>]*\bclass="lead"[^>]*>[\s\S]*?<\/p>/i;
  if (!lead.test(html)) throw new Error(`${publicPath} has no legal lead paragraph`);
  return html.replace(lead, match => `${match}${addition}`);
}

function injectOperatorIdentity(html, language, publicPath) {
  if (!OPERATOR_CONFIGURED) return html;
  const e = escapeAttribute;
  const title = e(keyedCopy("legal.operatorTitle", language));
  if (OPERATOR_PAGES.has(publicPath)) {
    const rows = [
      ["operatorName"], ["address"], ["country"],
      ["phone", `tel:${OPERATOR.phone.replace(/[^+\d]/g, "")}`],
      ["email", `mailto:${OPERATOR.email}`],
      ["taxId"], ["taxOffice"], ["registrationId"], ["mersisId"], ["tradeRegistry"],
      ["kepAddress", `mailto:${OPERATOR.kepAddress}`],
      ["chamberName"],
    ].filter(([field]) => OPERATOR[field]).map(([field, href]) => {
      const value = e(OPERATOR[field]);
      const detail = href ? `<a href="${e(href)}">${value}</a>` : value;
      return `<div><dt>${e(keyedCopy(`legal.${field}`, language))}</dt><dd>${detail}</dd></div>`;
    }).join("");
    const block = `<section class="legal-operator" data-legal-operator="static"><h2>${title}</h2><dl>${rows}</dl></section>`;
    return insertAfterLead(html, block, publicPath);
  }
  if (publicPath === "/about.html") {
    const name = `<strong>${e(OPERATOR.operatorName)}</strong>`;
    const sentence = e(keyedCopy("about.operator", language)).replace("{name}", () => name);
    const link = `<a href="${localizedPath(language, "/distance-sales.html")}">${title}</a>`;
    return insertAfterLead(html, `<p class="about-operator" data-about-operator>${sentence} ${link}</p>`, publicPath);
  }
  return html;
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
await rm(path.join(OUTPUT, "landing-pages.json"));

const pageCopyPaths = {};
const pageCopySizes = {};
for (const language of LANGUAGES) {
  const source = pageCopyCatalogSource(language);
  if (!source) continue;
  pageCopyPaths[language] = await writeContentAddressedScript("page-i18n", language, source);
  pageCopySizes[language] = Buffer.byteLength(source);
}
const pageCopyLoader = languageLoaderSource(pageCopyPaths, "page-copy-loader.js");
await loadReaderLanguage(PAGE_COPY_SCRIPT, "page-i18n.js",
  await writeContentAddressedScript("page-i18n", "loader", pageCopyLoader));

// Public pages and app pages share one runtime file per language. App pages
// reach theirs through a loader, because one app page serves every language.
const runtimePaths = {};
const runtimeSizes = {};
for (const language of LANGUAGES) {
  const source = publicRuntimeSource(language);
  runtimePaths[language] = await writeContentAddressedScript("i18n", language, source);
  runtimeSizes[language] = Buffer.byteLength(source);
}
const runtimeLoader = languageLoaderSource(runtimePaths, "runtime-loader.js");
await loadReaderLanguage(RUNTIME_SCRIPT, "i18n.js",
  await writeContentAddressedScript("i18n", "loader", runtimeLoader), FULL_RUNTIME_PAGES);

for (const language of LANGUAGES) {
  const runtimePath = runtimePaths[language];
  for (const publicPath of PUBLIC_PATHS) {
    const sourceName = publicPath === "/" ? "index.html" : publicPath.slice(1);
    let html = await readFile(path.join(SOURCE, sourceName), "utf8");
    html = translateDocument(html, language);
    html = localizeStaticPlanValues(html, language);
    html = localizeContinuationTargets(html, language);
    html = enrichLandingPage(html, language, publicPath);
    html = injectOperatorIdentity(html, language, publicPath);
    html = canonicalizePublicLinks(html);
    html = removeStaticTranslationCatalog(html);
    html = html.replace(/src="\/i18n\.js(?:\?[^\"]*)?"/g, `src="${runtimePath}"`);
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
  const course = page.kind === "course";
  const topic = page.kind === "topic";
  const prefix = tr ? "" : "/en";
  const alternate = tr ? `/en/${page.slug}` : `/${page.slug}`;
  const toc = [...copy.body.matchAll(/<section id="([^"]+)"[^>]*>\s*<h2>([^<]+)<\/h2>/g)]
    .map(([, id, title]) => `<li><a href="#${escapeAttribute(id)}">${title}</a></li>`).join("");
  const updated = new Intl.DateTimeFormat(tr ? "tr-TR" : "en-GB", {
    day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
  }).format(new Date(`${page.updated || STUDY_RESOURCES.updated}T00:00:00Z`));
  const minutes = Math.max(2, Math.ceil(plainText(copy.body).split(/\s+/).length / 190));
  const brand = `<span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></span><span>Lecture<span>Sift</span></span>`;
  const preview = `<figure class="guide-preview"><figcaption><span class="guide-preview-dot" aria-hidden="true"></span>${tr ? "Örnek dersin içinden" : "Inside the example lesson"}</figcaption><h2>${tr ? "Aynı veri.<br>İki farklı bakış." : "Same data.<br>Two ways to see it."}</h2><p>${tr ? "Okula gidiş süresi · dakika" : "Journey to school · minutes"}</p><div class="guide-preview-data" aria-label="${tr ? "18, 20, 20, 22 ve 70 dakika" : "18, 20, 20, 22, and 70 minutes"}"><span>18</span><span>20</span><span class="guide-median">20</span><span>22</span><span>70</span></div><dl><div><dt>${tr ? "Ortalama" : "Mean"}</dt><dd>30 <small>${tr ? "dk" : "min"}</small></dd></div><div><dt>${tr ? "Medyan" : "Median"}</dt><dd>20 <small>${tr ? "dk" : "min"}</small></dd></div></dl><a href="${prefix}/study-pack-example">${tr ? "Neden farklılar? Örnekte keşfet" : "Why the difference? Explore the example"} <span aria-hidden="true">↗</span></a></figure>`;
  // Course and topic pages open with the free guest trial and the free account, with no fixed limits copied here.
  const trial = course || topic ? `<div class="guide-hero-actions" data-guide-trial><a class="guide-button guide-button-primary" href="${prefix}/workspace.html">${tr ? "Misafir olarak ücretsiz dene" : "Try it free as a guest"} <span aria-hidden="true">→</span></a><a class="guide-button" href="${prefix}/register.html">${tr ? "Ücretsiz hesap oluştur" : "Create a free account"}</a></div><p class="guide-access">${tr ? "Misafir denemesi için hesap gerekmez · Güncel sınırlar çalışma alanında ve Planlar sayfasında" : "No account needed for the guest trial · Current limits are shown in the workspace and on the Plans page"}</p>` : "";
  const eyebrow = course ? (tr ? "LectureSift · Bölüme göre çalışma" : "LectureSift · Study by subject") : topic ? (tr ? "LectureSift · Sınava ve konuya göre çalışma" : "LectureSift · Study by exam and topic") : (tr ? "LectureSift · Öğrenme kütüphanesi" : "LectureSift · Learning library");
  const hero = `<header class="guide-intro${library ? " guide-hero" : ""}"><div><p class="guide-eyebrow">${eyebrow}</p><h1>${escapeAttribute(copy.title)}</h1><p class="guide-lead">${escapeAttribute(copy.description)}</p>${library ? `<div class="guide-hero-actions"><a class="guide-button guide-button-primary" href="${prefix}/study-pack-example">${tr ? "Örnek dersi aç" : "Try the example lesson"} <span aria-hidden="true">→</span></a><a class="guide-text-link" href="#${tr ? "basla" : "start"}">${tr ? "Rehber seç" : "Choose a guide"} ↓</a></div><p class="guide-access">${tr ? "Ücretsiz rehberler · Hesap veya kredi gerekmez" : "Free guides · No account or credits needed"}</p>` : `<p class="guide-byline"><span>LectureSift</span><span>${minutes} ${tr ? "dk okuma" : "min read"}</span><time datetime="${page.updated || STUDY_RESOURCES.updated}">${updated}</time></p>${trial}`}</div>${library ? preview : ""}</header>`;
  const products = library || course || topic
    ? ["/document-summary.html", "/lecture-video-summary.html", "/quiz-flashcards.html"]
    : page.slug === "cornell-notes" ? ["/lecture-video-summary.html", "/document-summary.html"]
    : [page.slug === "active-recall" ? "/quiz-flashcards.html" : "/document-summary.html"];
  const productLinks = `<nav class="guide-related" data-guide-products aria-label="${tr ? "Kendi kaynaklarınla çalış" : "Study with your own sources"}"><strong>${tr ? "Kendi dersinle uygula" : "Apply it to your own course"}</strong>${products.map(route => `<a href="${localizedPath(language, route)}">${escapeAttribute(LANDING_PAGES.pages[route][language].action)} <span aria-hidden="true">→</span></a>`).join("")}</nav>`;
  const related = (library ? "" : course ? `<nav class="guide-related" data-guide-subjects aria-label="${tr ? "Diğer bölümler" : "Other subjects"}"><strong>${tr ? "Diğer bölümler" : "Other subjects"}</strong>${COURSE_PAGES.filter(item => item.slug !== page.slug).map(item => `<a href="${prefix}/${item.slug}">${escapeAttribute(item[language].title)} <span aria-hidden="true">→</span></a>`).join("")}</nav>` : topic ? `<nav class="guide-related" data-guide-topics aria-label="${tr ? "Diğer konu rehberleri" : "More topic guides"}"><strong>${tr ? "Diğer konu rehberleri" : "More topic guides"}</strong>${TOPIC_PAGES.filter(item => item.slug !== page.slug).map(item => `<a href="${prefix}/${item.slug}">${escapeAttribute(item[language].title)} <span aria-hidden="true">→</span></a>`).join("")}</nav>` : `<nav class="guide-related" aria-label="${tr ? "Diğer çalışma rehberleri" : "More study guides"}"><strong>${tr ? "Çalışmaya devam et" : "Keep learning"}</strong>${STUDY_RESOURCES.pages.filter(item => item.kind === "article" && item.slug !== page.slug && item.slug !== "about-study-guides").map(item => `<a href="${prefix}/${item.slug}">${escapeAttribute(item[language].title)} <span aria-hidden="true">→</span></a>`).join("")}</nav>`) + productLinks;
  return `<!doctype html><html lang="${language}" dir="ltr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapeAttribute(copy.title)} | LectureSift</title><meta name="description" content="${escapeAttribute(copy.description)}">
<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/styles.css?v=12"><link rel="stylesheet" href="/theme.css?v=17"><link rel="stylesheet" href="/product-layout.css?v=8"><link rel="stylesheet" href="/study-guides.css?v=4"><script src="/theme.js?v=13"></script></head>
<body class="guide-page"><a class="skip-link guide-skip" href="#content">${tr ? "İçeriğe geç" : "Skip to content"}</a>
<header class="guide-header"><div class="guide-header-inner"><a class="brand guide-brand" href="${prefix}/" aria-label="LectureSift ${tr ? "ana sayfa" : "home"}">${brand}</a><nav aria-label="${tr ? "Ana menü" : "Main navigation"}"><a href="${prefix}/study-guides" aria-label="${tr ? "Çalışma rehberleri" : "Study guides"}"${library ? ' aria-current="page"' : ""}><span class="guide-nav-full">${tr ? "Çalışma rehberleri" : "Study guides"}</span><span class="guide-nav-compact" aria-hidden="true">${tr ? "Rehberler" : "Guides"}</span></a><a href="${prefix}/about-study-guides" aria-label="${tr ? "Rehberler hakkında" : "About the guides"}"${page.slug === "about-study-guides" ? ' aria-current="page"' : ""}><span class="guide-nav-full">${tr ? "Rehberler hakkında" : "About the guides"}</span><span class="guide-nav-compact" aria-hidden="true">${tr ? "Hakkında" : "About"}</span></a><a class="guide-language" href="${alternate}" hreflang="${tr ? "en" : "tr"}" lang="${tr ? "en" : "tr"}">${tr ? "English" : "Türkçe"}</a></nav><a class="guide-button guide-button-primary guide-header-cta" href="${prefix}/workspace.html">${tr ? "Çalışma alanı" : "Workspace"} <span aria-hidden="true">↗</span></a></div></header>
<main class="guide-layout${library ? " guide-library" : ""}" id="content">${library ? "" : `<nav class="guide-breadcrumb" aria-label="${tr ? "Sayfa konumu" : "Breadcrumb"}"><a href="${prefix}/study-guides">← ${tr ? "Çalışma rehberleri" : "Study guides"}</a><span aria-hidden="true">/</span><span>${escapeAttribute(copy.title)}</span></nav>`}<article class="guide-article">${hero}${copy.body}${related}</article>${library ? "" : `<aside class="guide-toc" aria-label="${tr ? "İçindekiler" : "Contents"}"><details><summary>${tr ? "Bu sayfada" : "On this page"}</summary><ol>${toc}</ol></details><a class="guide-toc-home" href="${prefix}/study-guides">${tr ? "Tüm rehberler →" : "All guides →"}</a></aside>`}</main>
<footer class="guide-footer"><div class="guide-footer-top"><div><a class="brand guide-brand" href="${prefix}/">${brand}</a><p>${tr ? "Derslerin bir arada. Aklın öğrenmede." : "Your lessons together. Your mind on learning."}</p></div><nav aria-label="${tr ? "Alt menü" : "Footer navigation"}"><a href="${prefix}/study-guides">${tr ? "Çalışma rehberleri" : "Study guides"}</a><a data-guide-about href="${prefix}/about-study-guides">${tr ? "Rehberler hakkında" : "About these guides"}</a><a href="${prefix}/about">${tr ? "Hakkımızda" : "About LectureSift"}</a><a href="${prefix}/contact">${tr ? "Hata bildir / İletişim" : "Report an error / Contact"}</a><a href="${prefix}/privacy">${tr ? "Gizlilik" : "Privacy"}</a><a href="${prefix}/terms">${tr ? "Kullanım koşulları" : "Terms"}</a></nav></div><p class="guide-footer-note">© 2026 LectureSift · ${tr ? "Yapay zekâ yardımıyla hazırlanmış eğitim kaynakları. Örnek veriler öğretim amaçlıdır." : "Educational resources prepared with AI assistance. Example data are illustrative."}</p></footer></body></html>`;
}

for (const [publicPath, page] of STUDY_PAGES) {
  for (const language of STUDY_RESOURCES.languages) {
    let html = studyDocument(page, language);
    html = html.replace("</head>", '<link rel="stylesheet" href="/consent.css?v=2"><script defer src="/consent.js?v=6"></script><script defer src="/analytics.js?v=11"></script></head>');
    html = staticSeo(html, language, publicPath);
    validateLocalizedPage(html, language, publicPath);
    const target = path.join(OUTPUT, language === "tr" ? "" : language, `${page.slug}.html`);
    await mkdir(path.dirname(target), {recursive: true});
    await writeFile(target, html, "utf8");
  }
}
// Localized public pages are physical files. Only private app pages and shared
// assets need rewrites; a broad locale splat also exposes /en/en/features and
// silently substitutes Turkish when a translated page is missing.
const sharedRoutes = new Map();
for (const entry of await readdir(SOURCE, {withFileTypes: true})) {
  if (!entry.isFile()) continue;
  const route = `/${entry.name}`;
  if (entry.name.endsWith(".html")) {
    if (entry.name === "404.html" || entry.name === "index.html" || PUBLIC_PATH_SET.has(route)) continue;
    const html = await readFile(path.join(SOURCE, entry.name), "utf8");
    if (!/<meta\s+name="robots"\s+content="[^"]*\bnoindex\b/i.test(html)) {
      throw new Error(`Shared app page must be explicitly noindex: ${route}`);
    }
    sharedRoutes.set(route, route);
    sharedRoutes.set(route.slice(0, -5), route);
  } else if (/\.(?:js|css|png|svg|jpg|webp|ico|woff2)$/.test(entry.name)) {
    sharedRoutes.set(route, route);
  }
}
async function sharedAssets(directory, prefix) {
  for (const entry of await readdir(directory, {withFileTypes: true})) {
    const route = `${prefix}/${entry.name}`;
    if (entry.isDirectory()) await sharedAssets(path.join(directory, entry.name), route);
    else if (entry.isFile()) sharedRoutes.set(route, route);
  }
}
await sharedAssets(path.join(OUTPUT, "assets"), "/assets");
const sharedRewrites = LANGUAGES.filter(language => language !== "tr").flatMap(language =>
  [...sharedRoutes].sort(([a], [b]) => a.localeCompare(b)).map(([from, to]) => `/${language}${from} ${to} 200`)
);
const redirects = await readFile(path.join(SOURCE, "_redirects"), "utf8");
await writeFile(path.join(OUTPUT, "_redirects"), `${redirects}\n${sharedRewrites.join("\n")}\n`, "utf8");
async function assertNoFullCatalogs(directory) {
  for (const entry of await readdir(directory, {withFileTypes: true})) {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      await assertNoFullCatalogs(target);
      continue;
    }
    if (!entry.name.endsWith(".html")) continue;
    const page = path.relative(OUTPUT, target);
    const html = await readFile(target, "utf8");
    if (/\bsrc="[^"]*page-i18n\.js\b/i.test(html)) {
      throw new Error(`${page} still loads the all-language page-i18n.js`);
    }
    if (FULL_RUNTIME_REFERENCE.test(html) && !FULL_RUNTIME_PAGES.has(page)) {
      throw new Error(`${page} still loads the all-language i18n.js`);
    }
  }
}
await assertNoFullCatalogs(OUTPUT);
console.log(`Page copy: page-i18n.js ${Buffer.byteLength(pageCopySource)} bytes; loader ${Buffer.byteLength(pageCopyLoader)} bytes; ${
  LANGUAGES.map(language => `${language} ${pageCopySizes[language] || 0}`).join(", ")} bytes per language catalog`);
console.log(`Runtime: i18n.js ${Buffer.byteLength(dynamicCopySource)} bytes; loader ${Buffer.byteLength(runtimeLoader)} bytes; ${
  LANGUAGES.map(language => `${language} ${runtimeSizes[language]}`).join(", ")} bytes per language runtime`);
console.log(`Built ${LANGUAGES.length * PUBLIC_PATHS.length + STUDY_PAGES.size * STUDY_RESOURCES.languages.length} indexable pages in ${OUTPUT}`);
