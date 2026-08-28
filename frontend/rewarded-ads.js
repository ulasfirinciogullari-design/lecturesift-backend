(() => {
  "use strict";

  const GPT_SRC = "https://securepubads.g.doubleclick.net/tag/js/gpt.js";
  let loader = null;

  function loadProvider() {
    if (!window.LectureSiftConsent?.allows("advertising")) {
      return Promise.reject(new Error("advertising-consent-required"));
    }
    if (window.googletag?.apiReady) return Promise.resolve(window.googletag);
    if (loader) return loader;
    loader = new Promise((resolve, reject) => {
      window.googletag = window.googletag || {cmd: []};
      const script = document.createElement("script");
      script.async = true;
      script.src = GPT_SRC;
      script.referrerPolicy = "strict-origin-when-cross-origin";
      script.onload = () => resolve(window.googletag);
      script.onerror = () => reject(new Error("rewarded-ad-provider-unavailable"));
      document.head.append(script);
    });
    return loader;
  }

  async function show(adUnitPath) {
    if (!adUnitPath || !String(adUnitPath).startsWith("/")) {
      throw new Error("rewarded-ad-unit-invalid");
    }
    const googletag = await loadProvider();
    return new Promise((resolve, reject) => {
      googletag.cmd.push(() => {
        const slot = googletag.defineOutOfPageSlot(
          adUnitPath,
          googletag.enums.OutOfPageFormat.REWARDED,
        );
        if (!slot) {
          reject(new Error("rewarded-ad-unsupported"));
          return;
        }

        const pubads = googletag.pubads();
        let granted = false;
        let settled = false;
        const finish = (callback, value) => {
          if (settled) return;
          settled = true;
          clearTimeout(timeout);
          pubads.removeEventListener("rewardedSlotReady", onReady);
          pubads.removeEventListener("rewardedSlotGranted", onGranted);
          pubads.removeEventListener("rewardedSlotClosed", onClosed);
          pubads.removeEventListener("slotRenderEnded", onRendered);
          googletag.destroySlots([slot]);
          callback(value);
        };
        const onReady = event => {
          if (event.slot === slot) event.makeRewardedVisible();
        };
        const onGranted = event => {
          if (event.slot === slot) granted = true;
        };
        const onClosed = event => {
          if (event.slot === slot) finish(resolve, granted);
        };
        const onRendered = event => {
          if (event.slot === slot && event.isEmpty) finish(reject, new Error("rewarded-ad-empty"));
        };
        const timeout = setTimeout(
          () => finish(reject, new Error("rewarded-ad-timeout")),
          25_000,
        );

        pubads.addEventListener("rewardedSlotReady", onReady);
        pubads.addEventListener("rewardedSlotGranted", onGranted);
        pubads.addEventListener("rewardedSlotClosed", onClosed);
        pubads.addEventListener("slotRenderEnded", onRendered);
        slot.addService(pubads);
        googletag.enableServices();
        googletag.display(slot);
      });
    });
  }

  window.LectureSiftRewardedAds = Object.freeze({show});
})();
