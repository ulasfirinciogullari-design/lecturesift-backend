(() => {
  "use strict";

  const API_BASE = "https://lecturesift-backend.onrender.com";
  const PUBLIC_PATHS = new Set([
    "/", "/index.html", "/features.html", "/plans.html", "/about.html",
    "/contact.html", "/privacy.html", "/terms.html", "/cookies.html", "/refund.html",
    "/distance-sales.html",
  ]);
  const EVENT_PATHS = new Set([...PUBLIC_PATHS, "/register.html", "/account.html"]);
  const configuredIds = new Set();
  let remoteConfig = null;
  let configPromise = null;
  let tagLoaded = false;

  function unlocalizedPath() {
    const languages = new Set(["tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi"]);
    const parts = location.pathname.split("/").filter(Boolean);
    if (languages.has(parts[0])) parts.shift();
    return `/${parts.join("/")}` || "/";
  }

  function choices() {
    return window.LectureSiftConsent?.get?.() || {analytics: false, advertising: false};
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
    prepareGtag();
    const consent = choices();
    window.gtag("consent", "update", {
      analytics_storage: consent.analytics ? "granted" : "denied",
      ad_storage: consent.advertising ? "granted" : "denied",
      ad_user_data: consent.advertising ? "granted" : "denied",
      ad_personalization: consent.advertising ? "granted" : "denied",
    });
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
        .catch(() => null);
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
        send_page_view: PUBLIC_PATHS.has(unlocalizedPath()),
        allow_google_signals: false,
        allow_ad_personalization_signals: false,
        anonymize_ip: true,
      });
    }
    if (adsReady && !configuredIds.has(adsId)) {
      configuredIds.add(adsId);
      window.gtag("config", adsId, {allow_ad_personalization_signals: consent.advertising});
    }
  }

  async function start() {
    if (!EVENT_PATHS.has(unlocalizedPath())) return;
    const config = await getConfig();
    if (!config) return;
    configureDestinations(config);
  }

  async function track(eventName, parameters = {}) {
    if (!EVENT_PATHS.has(unlocalizedPath()) || !choices().analytics) return false;
    const config = await getConfig();
    configureDestinations(config);
    if (!config?.enabled || typeof window.gtag !== "function") return false;
    window.gtag("event", String(eventName), {...parameters, send_to: config.measurement_id});
    return true;
  }

  async function trackConversion(kind, parameters = {}) {
    if (!EVENT_PATHS.has(unlocalizedPath()) || !choices().advertising) return false;
    const config = await getConfig();
    configureDestinations(config);
    const ads = config?.google_ads;
    const label = kind === "purchase" ? ads?.purchase_label : kind === "signup" ? ads?.signup_label : null;
    if (!ads?.enabled || !label || typeof window.gtag !== "function") return false;
    window.gtag("event", "conversion", {...parameters, send_to: `${ads.id}/${label}`});
    return true;
  }

  window.LectureSiftAnalytics = Object.freeze({track, trackConversion, refresh: start});
  const queued = Array.isArray(window.__lecturesiftAnalyticsQueue) ? window.__lecturesiftAnalyticsQueue.splice(0) : [];
  queued.forEach(item => {
    if (item?.type === "conversion") void trackConversion(item.name, item.parameters);
    else if (item?.type === "event") void track(item.name, item.parameters);
  });
  document.addEventListener("lecturesift:consent", () => { updateConsent(); void start(); });
  document.addEventListener("lecturesift:consent-ready", () => { updateConsent(); void start(); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, {once: true});
  else void start();
})();
