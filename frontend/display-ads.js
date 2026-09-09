(() => {
  "use strict";

  const API_BASE = "https://api.lecturesift.com";
  const GPT_SRC = "https://securepubads.g.doubleclick.net/tag/js/gpt.js";
  const PUBLIC_AD_PATHS = new Set(["/", "/features", "/plans", "/about"]);
  const TOKEN_KEY = "lecturesift-billing-token";
  let started = false;
  let activeDisplaySlot = null;
  let adsenseAutoAdsLoaded = false;

  function unlocalizedPath() {
    const languages = new Set(["tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi"]);
    const parts = location.pathname.split("/").filter(Boolean);
    if (languages.has(parts[0])) parts.shift();
    const path = `/${parts.join("/")}`;
    if (path === "/index.html") return "/";
    return path.replace(/\.html$/, "");
  }

  async function json(path, options = {}) {
    const response = await fetch(`${API_BASE}${path}`, options);
    if (!response.ok) throw new Error(`display-ad-request-${response.status}`);
    return response.json();
  }

  async function accountAdMode() {
    let token = "";
    try { token = localStorage.getItem(TOKEN_KEY) || ""; } catch (_) { return "none"; }
    if (!token) return "standard";
    try {
      const body = await json("/billing/me", {headers: {Authorization: `Bearer ${token}`}});
      if (body.account?.plan?.entitlements?.ad_free === true) return "none";
      // A signed-in account may receive publisher ads only when the API
      // explicitly identifies it as non-ad-free. Unknown entitlement state is
      // treated as ad-free so transient failures cannot leak ads to paid users.
      const entitlements = body.account?.plan?.entitlements;
      if (entitlements?.ad_free !== false) return "none";
      return entitlements.ad_mode === "limited" ? "limited" : "standard";
    } catch (_) {
      return "none";
    }
  }

  function publisherMode(config) {
    // `enabled` is the canonical publisher-ad kill switch. A nested identifier
    // is configuration, not authorization to start a different Google product.
    if (config?.enabled !== true) return null;
    if (
      config.provider === "google_gpt"
      && String(config.banner_unit_path || "").startsWith("/")
    ) return "google_gpt";
    if (
      config.provider === "google_adsense_auto"
      && config.adsense_auto_ads?.enabled === true
      && /^ca-pub-[0-9]+$/.test(String(config.adsense_auto_ads.publisher_id || ""))
    ) return "google_adsense_auto";
    return null;
  }

  function advertisingAllowed() {
    return window.LectureSiftConsent?.allows("advertising") === true;
  }

  function clearDisplayAd() {
    if (activeDisplaySlot && window.googletag?.apiReady) {
      try { window.googletag.destroySlots([activeDisplaySlot]); } catch (_) {}
    }
    activeDisplaySlot = null;
    document.querySelector(".display-ad")?.remove();
  }

  function insertContainer() {
    const container = document.createElement("aside");
    container.className = "display-ad";
    container.setAttribute("aria-label", window.LectureSiftI18n?.t("ads.label", "Advertisement") || "Advertisement");
    const label = document.createElement("span");
    label.className = "display-ad-label";
    label.textContent = window.LectureSiftI18n?.t("ads.label", "Advertisement") || "Advertisement";
    const slot = document.createElement("div");
    slot.id = "lecturesift-display-ad";
    slot.className = "display-ad-slot";
    container.append(label, slot);
    const footer = document.querySelector("footer");
    if (footer) footer.before(container);
    else document.body.append(container);
    return {container, slot};
  }

  function renderHouseCampaign(campaign) {
    if (!campaign?.enabled || document.querySelector(".display-ad")) return;
    const container = document.createElement("aside");
    container.className = "display-ad house-campaign";
    container.setAttribute("aria-label", campaign.title || "LectureSift kampanyası");
    const copy = document.createElement("div");
    const eyebrow = document.createElement("span");
    eyebrow.className = "display-ad-label";
    eyebrow.textContent = "LectureSift önerisi";
    const title = document.createElement("strong");
    title.textContent = campaign.title || "Planları keşfet";
    const text = document.createElement("p");
    text.textContent = campaign.text || "Daha fazla ders işleme hakkına ulaş.";
    copy.append(eyebrow, title, text);
    const link = document.createElement("a");
    link.href = String(campaign.url || "/plans.html").startsWith("/") ? campaign.url : "/plans.html";
    link.textContent = campaign.cta || "Planları incele";
    link.className = "house-campaign-action";
    container.append(copy, link);
    const footer = document.querySelector("footer");
    if (footer) footer.before(container);
    else document.body.append(container);
  }

  function loadProvider() {
    if (window.googletag?.apiReady) return Promise.resolve();
    return new Promise((resolve, reject) => {
      window.googletag = window.googletag || {cmd: []};
      const script = document.createElement("script");
      script.async = true;
      script.src = GPT_SRC;
      script.referrerPolicy = "strict-origin-when-cross-origin";
      script.onload = resolve;
      script.onerror = () => reject(new Error("display-ad-provider-unavailable"));
      document.head.append(script);
    });
  }

  function loadAdSenseAutoAds(publisherId) {
    if (!/^ca-pub-[0-9]+$/.test(String(publisherId || ""))) return;
    if (document.querySelector('script[data-lecturesift-adsense="true"]')) return;
    const script = document.createElement("script");
    script.async = true;
    script.crossOrigin = "anonymous";
    script.dataset.lecturesiftAdsense = "true";
    script.src = `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${encodeURIComponent(publisherId)}`;
    document.head.append(script);
    adsenseAutoAdsLoaded = true;
  }

  async function start() {
    if (started || !PUBLIC_AD_PATHS.has(unlocalizedPath())) return;
    started = true;
    try {
      const config = await json("/ads/config");
      const adMode = await accountAdMode();
      if (adMode === "none" || (adMode === "limited" && unlocalizedPath() !== "/")) return;
      const mode = publisherMode(config);
      if (!advertisingAllowed() || !mode) {
        renderHouseCampaign(config.house_campaign);
        return;
      }
      if (mode === "google_adsense_auto") {
        loadAdSenseAutoAds(config.adsense_auto_ads.publisher_id);
        return;
      }
      const {container, slot} = insertContainer();
      await loadProvider();
      if (!advertisingAllowed()) { container.remove(); return; }
      window.googletag.cmd.push(() => {
        if (!advertisingAllowed()) { container.remove(); return; }
        const pubads = window.googletag.pubads();
        pubads.addEventListener("slotRenderEnded", event => {
          if (event.slot?.getSlotElementId?.() === slot.id && event.isEmpty) container.remove();
        });
        const adSlot = window.googletag
          .defineSlot(config.banner_unit_path, [[970, 90], [728, 90], [320, 100]], slot.id)
          ?.addService(pubads);
        if (!adSlot) { container.remove(); return; }
        activeDisplaySlot = adSlot;
        pubads.enableSingleRequest();
        window.googletag.enableServices();
        window.googletag.display(slot.id);
      });
    } catch (_) {
      document.querySelector(".display-ad")?.remove();
    }
  }

  const refreshForConsent = () => {
    clearDisplayAd();
    // Auto Ads does not expose a reliable client-side teardown API. Reloading
    // after revocation clears its injected frames before continuing denied.
    if (!advertisingAllowed() && adsenseAutoAdsLoaded) {
      location.reload();
      return;
    }
    started = false;
    start();
  };
  document.addEventListener("lecturesift:consent", refreshForConsent);
  document.addEventListener("lecturesift:consent-ready", refreshForConsent);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, {once: true});
  else start();
})();
