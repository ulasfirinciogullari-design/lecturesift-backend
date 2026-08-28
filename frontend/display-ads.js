(() => {
  "use strict";

  const API_BASE = "https://lecturesift-backend.onrender.com";
  const GPT_SRC = "https://securepubads.g.doubleclick.net/tag/js/gpt.js";
  const PUBLIC_AD_PATHS = new Set(["/", "/index.html", "/features.html", "/plans.html", "/about.html"]);
  const TOKEN_KEY = "lecturesift-billing-token";
  let started = false;

  function unlocalizedPath() {
    const languages = new Set(["tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi"]);
    const parts = location.pathname.split("/").filter(Boolean);
    if (languages.has(parts[0])) parts.shift();
    return `/${parts.join("/")}` || "/";
  }

  async function json(path, options = {}) {
    const response = await fetch(`${API_BASE}${path}`, options);
    if (!response.ok) throw new Error(`display-ad-request-${response.status}`);
    return response.json();
  }

  async function paidAccountIsAdFree() {
    let token = "";
    try { token = localStorage.getItem(TOKEN_KEY) || ""; } catch (_) {}
    if (!token) return false;
    try {
      const body = await json("/billing/me", {headers: {Authorization: `Bearer ${token}`}});
      return body.account?.plan?.entitlements?.ad_free === true;
    } catch (_) {
      return false;
    }
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

  async function start() {
    if (started || !PUBLIC_AD_PATHS.has(unlocalizedPath())) return;
    if (!window.LectureSiftConsent?.allows("advertising")) return;
    started = true;
    try {
      const config = await json("/ads/config");
      if (!config.enabled || config.provider !== "google_gpt" || !String(config.banner_unit_path || "").startsWith("/")) return;
      if (await paidAccountIsAdFree()) return;
      const {container, slot} = insertContainer();
      await loadProvider();
      window.googletag.cmd.push(() => {
        const pubads = window.googletag.pubads();
        pubads.addEventListener("slotRenderEnded", event => {
          if (event.slot?.getSlotElementId?.() === slot.id && event.isEmpty) container.remove();
        });
        const adSlot = window.googletag
          .defineSlot(config.banner_unit_path, [[970, 90], [728, 90], [320, 100]], slot.id)
          ?.addService(pubads);
        if (!adSlot) { container.remove(); return; }
        pubads.enableSingleRequest();
        window.googletag.enableServices();
        window.googletag.display(slot.id);
      });
    } catch (_) {
      document.querySelector(".display-ad")?.remove();
    }
  }

  document.addEventListener("lecturesift:consent", event => {
    if (event.detail?.advertising) start();
    else document.querySelector(".display-ad")?.remove();
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, {once: true});
  else start();
})();
