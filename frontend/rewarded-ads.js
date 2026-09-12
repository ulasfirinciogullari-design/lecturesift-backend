(() => {
  "use strict";

  const GPT_SRC = "https://securepubads.g.doubleclick.net/tag/js/gpt.js";
  const CLOSE_GRANT_GRACE_MS = 5_000;
  let loader = null;

  function loadProvider(deadlineAt) {
    if (!window.LectureSiftConsent?.allows("advertising")) {
      return Promise.reject(new Error("advertising-consent-required"));
    }
    if (window.googletag?.apiReady) return Promise.resolve(window.googletag);
    if (loader) return loader;
    loader = new Promise((resolve, reject) => {
      window.googletag = window.googletag || {cmd: []};
      const script = document.createElement("script");
      let settled = false;
      const finish = (callback, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        callback(value);
      };
      script.async = true;
      script.src = GPT_SRC;
      script.referrerPolicy = "strict-origin-when-cross-origin";
      script.onload = () => finish(resolve, window.googletag);
      script.onerror = () => finish(reject, new Error("rewarded-ad-provider-unavailable"));
      const timeout = setTimeout(
        () => finish(reject, new Error("rewarded-ad-timeout")),
        Math.max(1, deadlineAt - Date.now()),
      );
      document.head.append(script);
    });
    loader.catch(() => { loader = null; });
    return loader;
  }

  async function show(adUnitPath, lifecycle = {}) {
    if (!adUnitPath || !String(adUnitPath).startsWith("/")) {
      throw new Error("rewarded-ad-unit-invalid");
    }
    lifecycle = lifecycle || {};
    const notifyPresented = typeof lifecycle.onPresented === "function" ? lifecycle.onPresented : null;
    const notifyGranted = typeof lifecycle.onGranted === "function" ? lifecycle.onGranted : null;
    const notifyAbandoned = typeof lifecycle.onAbandoned === "function" ? lifecycle.onAbandoned : null;
    const requestedTimeout = Number(lifecycle.timeoutMs);
    const timeoutMs = Number.isFinite(requestedTimeout)
      ? Math.max(15_000, Math.min(115_000, requestedTimeout))
      : 115_000;
    const deadlineAt = Date.now() + timeoutMs;
    const beforeDeadline = callback => {
      if (!callback) return Promise.resolve();
      const remaining = deadlineAt - Date.now();
      if (remaining <= 0) return Promise.reject(new Error("rewarded-ad-timeout"));
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("rewarded-ad-timeout")), remaining);
        Promise.resolve()
          .then(callback)
          .then(
            value => { clearTimeout(timer); resolve(value); },
            error => { clearTimeout(timer); reject(error); },
          );
      });
    };
    const abandon = async () => {
      try { await beforeDeadline(notifyAbandoned); } catch {}
    };
    let googletag;
    try {
      googletag = await loadProvider(deadlineAt);
    } catch (error) {
      await abandon();
      throw error;
    }
    if (Date.now() >= deadlineAt) {
      await abandon();
      throw new Error("rewarded-ad-timeout");
    }
    return new Promise((resolve, reject) => {
      let commandFinished = false;
      let resultSettled = false;
      let finishStarted = false;
      let cleanupPromise = null;
      let slot = null;
      let pubads = null;
      let overallTimeout = null;
      let closeGraceTimeout = null;
      let ready = false;
      let visible = false;
      let grantPending = false;
      let granted = false;
      let closed = false;
      let eventQueue = Promise.resolve();
      let onReady = null;
      let onGranted = null;
      let onClosed = null;
      let onRendered = null;

      const settleResult = (callback, value) => {
        if (resultSettled) return;
        resultSettled = true;
        callback(value);
      };
      const queue = callback => {
        if (!callback) return eventQueue;
        eventQueue = eventQueue.then(() => beforeDeadline(callback));
        return eventQueue;
      };
      const cleanup = (shouldAbandon = false) => {
        if (cleanupPromise) return cleanupPromise;
        cleanupPromise = (async () => {
          clearTimeout(overallTimeout);
          clearTimeout(closeGraceTimeout);
          if (pubads) {
            if (onReady) pubads.removeEventListener("rewardedSlotReady", onReady);
            if (onGranted) pubads.removeEventListener("rewardedSlotGranted", onGranted);
            if (onClosed) pubads.removeEventListener("rewardedSlotClosed", onClosed);
            if (onRendered) pubads.removeEventListener("slotRenderEnded", onRendered);
          }
          let queuedError = null;
          try { await eventQueue; } catch (error) { queuedError = error; }
          if (shouldAbandon && !granted) await abandon();
          try { if (slot) googletag.destroySlots([slot]); } catch {}
          return queuedError;
        })();
        return cleanupPromise;
      };
      const finish = (callback, value, shouldAbandon = false) => {
        if (finishStarted) return;
        finishStarted = true;
        void cleanup(shouldAbandon).then(queuedError => {
          if (queuedError) settleResult(reject, queuedError);
          else settleResult(callback, value);
        });
      };
      const recordGrant = () => {
        if (granted || finishStarted) return;
        granted = true;
        clearTimeout(closeGraceTimeout);
        void queue(notifyGranted).then(() => {
          settleResult(resolve, true);
          if (closed) void cleanup(false);
        }).catch(error => finish(reject, error));
      };

      const commandTimeout = setTimeout(() => {
        if (commandFinished) return;
        commandFinished = true;
        finish(reject, new Error("rewarded-ad-timeout"), true);
      }, Math.max(1, deadlineAt - Date.now()));
      const startSlot = () => {
        if (commandFinished || finishStarted) return;
        commandFinished = true;
        clearTimeout(commandTimeout);
        try {
          slot = googletag.defineOutOfPageSlot(
            adUnitPath,
            googletag.enums.OutOfPageFormat.REWARDED,
          );
          if (!slot) {
            finish(reject, new Error("rewarded-ad-unsupported"), true);
            return;
          }
          pubads = googletag.pubads();
          onReady = event => {
            if (event.slot !== slot || ready || finishStarted) return;
            ready = true;
            try { visible = event.makeRewardedVisible() === true; } catch {}
            if (visible !== true) {
              finish(reject, new Error("rewarded-ad-not-visible"), true);
              return;
            }
            void queue(notifyPresented).catch(error => finish(reject, error, true));
            if (grantPending) recordGrant();
          };
          onGranted = event => {
            if (event.slot !== slot || granted || finishStarted) return;
            if (!ready) {
              grantPending = true;
              return;
            }
            if (!visible) {
              finish(reject, new Error("rewarded-ad-event-order-invalid"), true);
              return;
            }
            recordGrant();
          };
          onClosed = event => {
            if (event.slot !== slot || closed || finishStarted) return;
            closed = true;
            if (granted) {
              void eventQueue.then(() => {
                settleResult(resolve, true);
                return cleanup(false);
              }).catch(error => finish(reject, error));
              return;
            }
            const remaining = deadlineAt - Date.now();
            if (remaining <= 0) {
              finish(resolve, false, true);
              return;
            }
            // The close grace is now the sole terminal timer. When it is
            // capped by the absolute deadline this avoids two same-time
            // callbacks racing to report different outcomes.
            clearTimeout(overallTimeout);
            closeGraceTimeout = setTimeout(
              () => finish(resolve, false, true),
              Math.max(1, Math.min(CLOSE_GRANT_GRACE_MS, remaining)),
            );
          };
          onRendered = event => {
            if (event.slot === slot && event.isEmpty) {
              finish(reject, new Error("rewarded-ad-empty"), true);
            }
          };
          overallTimeout = setTimeout(
            () => {
              if (resultSettled && granted) void cleanup(false);
              else finish(reject, new Error("rewarded-ad-timeout"), !granted);
            },
            Math.max(1, deadlineAt - Date.now()),
          );

          pubads.addEventListener("rewardedSlotReady", onReady);
          pubads.addEventListener("rewardedSlotGranted", onGranted);
          pubads.addEventListener("rewardedSlotClosed", onClosed);
          pubads.addEventListener("slotRenderEnded", onRendered);
          slot.addService(pubads);
          googletag.enableServices();
          googletag.display(slot);
        } catch (error) {
          finish(reject, error, true);
        }
      };
      try {
        googletag.cmd.push(startSlot);
      } catch (error) {
        clearTimeout(commandTimeout);
        commandFinished = true;
        finish(reject, error, true);
      }
    });
  }

  window.LectureSiftRewardedAds = Object.freeze({show});
})();
