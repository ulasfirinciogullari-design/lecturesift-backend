(() => {
  "use strict";

  const API_BASE = "https://api.lecturesift.com";
  const PRODUCTION_ORIGIN = new Set(["https://lecturesift.com", "https://www.lecturesift.com"]).has(location.origin);
  const PUBLIC_PATHS = new Set([
    "/", "/features", "/plans", "/about", "/contact", "/privacy",
    "/terms", "/cookies", "/refund", "/distance-sales",
    "/document-summary", "/lecture-video-summary", "/quiz-flashcards",
    "/study-guides", "/study-pack-example", "/check-ai-notes", "/active-recall",
    "/about-study-guides", "/cornell-notes", "/pdf-note-check",
  ]);
  const CONTENT_PATHS = new Set([
    "/", "/document-summary", "/lecture-video-summary", "/quiz-flashcards",
    "/study-guides", "/study-pack-example", "/check-ai-notes", "/active-recall",
    "/about-study-guides", "/cornell-notes", "/pdf-note-check",
  ]);
  const RESOURCE_PATHS = new Set([
    "/assets/study/mean-median-tr.txt", "/assets/study/mean-median-en.txt",
    "/assets/study/cornell-notes-tr.txt", "/assets/study/cornell-notes-en.txt",
    "/assets/study/pdf-check-tr.txt", "/assets/study/pdf-check-en.txt",
  ]);
  const EVENT_PATHS = new Set([...PUBLIC_PATHS, "/register", "/account"]);
  const configuredIds = new Set();
  const pageNonce = document.querySelector("script[nonce]")?.nonce || "";
  let remoteConfig = null;
  let configPromise = null;
  let tagLoaded = false;

  function unlocalizedPath(pathname = location.pathname) {
    const languages = new Set(["tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi"]);
    const parts = pathname.split("/").filter(Boolean);
    if (languages.has(parts[0])) parts.shift();
    const path = `/${parts.join("/")}`.replace(/\.html$/, "");
    return path === "/index" ? "/" : path;
  }

  function pageContext() {
    // The previous same-origin page can contain verification tokens or email.
    // Only its origin is needed for attribution; never forward that full URL.
    let referrer = "";
    try {
      const url = new URL(document.referrer);
      if (["http:", "https:"].includes(url.protocol)) referrer = url.origin;
    } catch (_) {}
    // Public landing-page campaign parameters remain available for attribution.
    // Account and registration events must not send their query or fragment.
    if (PUBLIC_PATHS.has(unlocalizedPath())) return {page_referrer: referrer};
    return {page_location: `${location.origin}${location.pathname}`, page_referrer: referrer};
  }

  function choices() {
    const saved = window.LectureSiftConsent?.get?.() || {analytics: false, advertising: false};
    const provider = window.LectureSiftConsent?.google?.();
    const google = provider
      ? Object.fromEntries(["analytics_storage", "ad_storage", "ad_user_data", "ad_personalization"].map(key => [key, provider[key] === "granted" ? "granted" : "denied"]))
      : {analytics_storage: saved.analytics ? "granted" : "denied",
        ad_storage: saved.advertising ? "granted" : "denied",
        ad_user_data: saved.advertising ? "granted" : "denied",
        ad_personalization: saved.advertising ? "granted" : "denied"};
    return {google, analytics: google.analytics_storage === "granted",
      advertising: google.ad_storage === "granted" && google.ad_user_data === "granted"};
  }

  function prepareGtag() {
    window.dataLayer = window.dataLayer || [];
    window.gtag = window.gtag || function gtag(){ window.dataLayer.push(arguments); };
    if (!window.__lecturesiftConsentDefaulted) {
      window.__lecturesiftConsentDefaulted = true;
      window.gtag("consent", "default", {
        analytics_storage: "denied",
        ad_storage: "denied",
        ad_user_data: "denied",
        ad_personalization: "denied",
        wait_for_update: 500,
      });
      window.gtag("js", new Date());
    }
  }

  function updateConsent() {
    if (!PRODUCTION_ORIGIN) return;
    prepareGtag();
    const consent = choices();
    window.gtag("consent", "update", consent.google);
    const gaId = String(remoteConfig?.measurement_id || "");
    if (gaId) window[`ga-disable-${gaId}`] = !consent.analytics;
  }

  function loadGoogleTag(id) {
    if (tagLoaded) return;
    tagLoaded = true;
    const script = document.createElement("script");
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(id)}`;
    script.referrerPolicy = "strict-origin-when-cross-origin";
    if (pageNonce) script.nonce = pageNonce;
    document.head.append(script);
  }

  async function getConfig() {
    if (remoteConfig) return remoteConfig;
    if (!configPromise) {
      configPromise = fetch(`${API_BASE}/analytics/config`)
        .then(response => {
          if (!response.ok) throw new Error("analytics-config-unavailable");
          return response.json();
        })
        .then(value => (remoteConfig = value))
        .catch(() => { configPromise = null; return null; });
    }
    return configPromise;
  }

  function configureDestinations(config) {
    const consent = choices();
    const gaId = String(config?.measurement_id || "").toUpperCase();
    const adsId = String(config?.google_ads?.id || "").toUpperCase();
    const gaReady = consent.analytics && config?.enabled && /^G-[A-Z0-9]+$/.test(gaId);
    const adsReady = consent.advertising && config?.google_ads?.enabled && /^AW-[0-9]+$/.test(adsId);
    if (!gaReady && !adsReady) return;
    prepareGtag();
    updateConsent();
    loadGoogleTag(gaReady ? gaId : adsId);
    if (gaReady && !configuredIds.has(gaId)) {
      configuredIds.add(gaId);
      window[`ga-disable-${gaId}`] = false;
      window.gtag("config", gaId, {
        ...pageContext(),
        send_page_view: PUBLIC_PATHS.has(unlocalizedPath()),
        allow_google_signals: false,
        allow_ad_personalization_signals: false,
        anonymize_ip: true,
      });
    }
    if (adsReady && !configuredIds.has(adsId)) {
      configuredIds.add(adsId);
      window.gtag("config", adsId, {...pageContext(), allow_ad_personalization_signals: consent.google.ad_personalization === "granted"});
    }
  }

  async function start() {
    if (!PRODUCTION_ORIGIN || !EVENT_PATHS.has(unlocalizedPath())) return;
    if (!choices().analytics && !choices().advertising) return;
    const config = await getConfig();
    if (!config) return;
    configureDestinations(config);
  }

  async function track(eventName, parameters = {}) {
    if (!PRODUCTION_ORIGIN || !EVENT_PATHS.has(unlocalizedPath()) || !choices().analytics) return false;
    const config = await getConfig();
    // Consent may have been withdrawn while configuration was loading.
    if (!choices().analytics) return false;
    configureDestinations(config);
    const measurementId = String(config?.measurement_id || "").toUpperCase();
    if (!config?.enabled || !configuredIds.has(measurementId) || typeof window.gtag !== "function") return false;
    window.gtag("event", String(eventName), {...parameters, ...pageContext(), send_to: measurementId});
    return true;
  }

  async function trackConversion(kind, parameters = {}) {
    if (!PRODUCTION_ORIGIN || !EVENT_PATHS.has(unlocalizedPath()) || !choices().advertising) return false;
    const config = await getConfig();
    if (!choices().advertising) return false;
    configureDestinations(config);
    const ads = config?.google_ads;
    const adsId = String(ads?.id || "").toUpperCase();
    const label = kind === "purchase" ? ads?.purchase_label : kind === "signup" ? ads?.signup_label : null;
    if (!ads?.enabled || !configuredIds.has(adsId) || !label || typeof window.gtag !== "function") return false;
    window.gtag("event", "conversion", {...parameters, ...pageContext(), send_to: `${adsId}/${label}`});
    return true;
  }

  // These are content interactions, never completed uploads, signups or sales.
  // Send only known routes and fixed labels; never link text, user files or URL parameters.
  function contentAction(event) {
    if (!PRODUCTION_ORIGIN || !CONTENT_PATHS.has(unlocalizedPath()) || !choices().analytics) return;
    const link = event.target?.closest?.("a[href]");
    if (!link) return;
    let destination;
    try { destination = new URL(link.href, location.href); } catch { return; }
    if (destination.origin !== location.origin) return;
    const target = unlocalizedPath(destination.pathname);
    let action;
    if (target === "/workspace") action = "open_workspace";
    else if (target === "/register") action = "open_registration";
    else if (target === "/plans") action = "view_plans";
    else if (CONTENT_PATHS.has(target) && target !== unlocalizedPath()) action = "read_related";
    else if (RESOURCE_PATHS.has(destination.pathname) && link.hasAttribute("download")) action = "download_resource";
    if (!action) return;
    void track("content_action", {
      action, content_type: action, content_id: unlocalizedPath(),
      content_language: document.documentElement.lang,
      target_path: action === "download_resource" ? destination.pathname : target,
      link_placement: link.closest("header") ? "header" : link.closest("footer") ? "footer" : "content",
      page_location: `${location.origin}${location.pathname}`,
      transport_type: "beacon",
    });
  }

  document.addEventListener("click", contentAction);

  window.LectureSiftAnalytics = Object.freeze({track, trackConversion, refresh: start});
  const queued = Array.isArray(window.__lecturesiftAnalyticsQueue) ? window.__lecturesiftAnalyticsQueue.splice(0) : [];
  queued.forEach(item => {
    const result = item?.type === "conversion" ? trackConversion(item.name, item.parameters)
      : item?.type === "event" ? track(item.name, item.parameters) : Promise.resolve(false);
    void result.then(value => item?.resolve?.(value === true), () => item?.resolve?.(false));
  });
  document.addEventListener("lecturesift:consent", () => { updateConsent(); void start(); });
  document.addEventListener("lecturesift:consent-ready", () => { updateConsent(); void start(); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, {once: true});
  else void start();
})();
