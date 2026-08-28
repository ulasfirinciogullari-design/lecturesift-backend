(() => {
  "use strict";

  const API_BASE = "https://lecturesift-backend.onrender.com";
  const PUBLIC_PATHS = new Set([
    "/", "/index.html", "/features.html", "/plans.html", "/about.html",
    "/contact.html", "/privacy.html", "/terms.html", "/cookies.html", "/refund.html",
  ]);
  let started = false;
  let measurementId = "";

  function unlocalizedPath() {
    const languages = new Set(["tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi"]);
    const parts = location.pathname.split("/").filter(Boolean);
    if (languages.has(parts[0])) parts.shift();
    return `/${parts.join("/")}` || "/";
  }

  function setConsent(granted) {
    if (!measurementId) return;
    window[`ga-disable-${measurementId}`] = !granted;
    if (typeof window.gtag === "function") {
      window.gtag("consent", "update", {
        analytics_storage: granted ? "granted" : "denied",
        ad_storage: "denied",
        ad_user_data: "denied",
        ad_personalization: "denied",
      });
    }
  }

  function loadGoogleTag(id) {
    window.dataLayer = window.dataLayer || [];
    window.gtag = window.gtag || function gtag(){ window.dataLayer.push(arguments); };
    window[`ga-disable-${id}`] = false;
    window.gtag("consent", "default", {
      analytics_storage: "granted",
      ad_storage: "denied",
      ad_user_data: "denied",
      ad_personalization: "denied",
      wait_for_update: 500,
    });
    window.gtag("js", new Date());
    window.gtag("config", id, {
      allow_google_signals: false,
      allow_ad_personalization_signals: false,
      anonymize_ip: true,
    });
    const script = document.createElement("script");
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(id)}`;
    script.referrerPolicy = "strict-origin-when-cross-origin";
    document.head.append(script);
  }

  async function start() {
    if (started || !PUBLIC_PATHS.has(unlocalizedPath())) return;
    if (!window.LectureSiftConsent?.allows("analytics")) return;
    started = true;
    try {
      const response = await fetch(`${API_BASE}/analytics/config`);
      if (!response.ok) throw new Error("analytics-config-unavailable");
      const config = await response.json();
      const id = String(config.measurement_id || "").toUpperCase();
      if (!config.enabled || config.provider !== "google_analytics_4" || !/^G-[A-Z0-9]+$/.test(id)) return;
      measurementId = id;
      loadGoogleTag(id);
    } catch (_) {
      started = false;
    }
  }

  document.addEventListener("lecturesift:consent", event => {
    if (event.detail?.analytics) start();
    else setConsent(false);
  });
  document.addEventListener("lecturesift:consent-ready", start);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, {once: true});
  else start();
})();
