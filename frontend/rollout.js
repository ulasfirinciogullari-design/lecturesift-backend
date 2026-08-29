(() => {
  const API_BASE = "https://lecturesift-backend.onrender.com";
  const TOKEN_KEY = "lecturesift-billing-token";
  const GUEST_TOKEN_KEY = "lecturesift-guest-token";
  const DEVICE_KEY = "lecturesift-guest-device";
  const ZERO_DECIMAL = new Set(["JPY", "KRW"]);
  let guestTrialState = null;
  const $ = id => document.getElementById(id);
  const rt = (key, fallback) => window.LectureSiftI18n?.t(key) || fallback || key;
  const rolloutLocale = () => window.LectureSiftI18n?.locale || navigator.language || "tr-TR";
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);

  document.querySelectorAll(".version-pill").forEach(node => node.remove());

  function registeredToken() {
    return localStorage.getItem(TOKEN_KEY) || "";
  }

  function activeToken() {
    if (typeof billingToken !== "undefined" && billingToken) return billingToken;
    return registeredToken() || sessionStorage.getItem(GUEST_TOKEN_KEY) || "";
  }

  async function api(path, options = {}, token = activeToken()) {
    const headers = new Headers(options.headers || {});
    if (!(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(`${API_BASE}${path}`, {...options, headers, cache: "no-store"});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = body.detail || body;
      throw Object.assign(new Error(detail.message || rt("error.request", "İşlem tamamlanamadı.")), {code: detail.code});
    }
    return body;
  }

  function deviceId() {
    let value = localStorage.getItem(DEVICE_KEY);
    if (!value) {
      value = crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      localStorage.setItem(DEVICE_KEY, value);
    }
    return value;
  }

  function setWorkspaceIdentity(token, account, guest = false, trial = null) {
    if (typeof billingToken !== "undefined") billingToken = token;
    if (typeof billingAccount !== "undefined") billingAccount = account;
    if (guest) {
      sessionStorage.setItem(GUEST_TOKEN_KEY, token);
      guestTrialState = trial;
    }
    if (typeof renderBillingAccount === "function") renderBillingAccount();
    if (typeof renderPlans === "function") renderPlans();
    updateGuestTrialUi();
  }

  async function ensureGuestIdentity() {
    if (activeToken()) return activeToken();
    const body = await api("/billing/guest-session", {
      method: "POST",
      body: JSON.stringify({device_id: deviceId()}),
    }, "");
    setWorkspaceIdentity(body.token, body.account, true, body.trial || null);
    return body.token;
  }

  async function restoreGuestIdentity() {
    if (registeredToken()) {
      sessionStorage.removeItem(GUEST_TOKEN_KEY);
      return;
    }
    const token = sessionStorage.getItem(GUEST_TOKEN_KEY);
    if (!token) return;
    try {
      const [body, rollout] = await Promise.all([
        api("/billing/me", {}, token),
        api("/billing/me/rollout", {}, token),
      ]);
      setWorkspaceIdentity(token, body.account, true, rollout.guest_trial || null);
    } catch {
      sessionStorage.removeItem(GUEST_TOKEN_KEY);
    }
  }

  function updateGuestTrialUi() {
    const button = $("analyzeButton");
    const note = document.querySelector(".rollout-guest-note");
    const guest = typeof billingAccount !== "undefined" && billingAccount?.plan?.code === "guest";
    const used = guest && guestTrialState?.used === true;
    if (button && used) button.disabled = true;
    if (note && used) {
      note.textContent = rt(
        "rollout.guestUsed",
        "Tek seferlik 5 dakikalık denemen kullanıldı. Devam etmek için ücretsiz hesap oluştur.",
      );
    }
    let signup = $("guestTrialSignup");
    if (used && note && !signup) {
      signup = document.createElement("a");
      signup.id = "guestTrialSignup";
      signup.className = "rollout-guest-signup";
      signup.href = window.LectureSiftI18n?.localizedPath?.(
        window.LectureSiftI18n?.language || "tr",
        "/register.html",
      ) || "/register.html";
      signup.textContent = rt("rollout.createFreeAccount", "Ücretsiz hesap oluştur");
      note.insertAdjacentElement("afterend", signup);
    } else if (!used && signup) {
      signup.remove();
    }
  }

  window.LectureSiftGuestTrial = {
    async ensureAccess() {
      if (!activeToken()) await ensureGuestIdentity();
      return {
        token: activeToken(),
        account: typeof billingAccount !== "undefined" ? billingAccount : null,
        trial: guestTrialState ? {...guestTrialState} : null,
      };
    },
    markUsed(jobId) {
      const guest = typeof billingAccount !== "undefined" && billingAccount?.plan?.code === "guest";
      if (!guest) return;
      guestTrialState = {
        ...(guestTrialState || {}),
        used: true,
        remaining_minutes: 0,
        job_id: jobId || guestTrialState?.job_id || null,
      };
      updateGuestTrialUi();
    },
    state() { return guestTrialState ? {...guestTrialState} : null; },
  };

  function formatCurrency(amountMinor, currency) {
    const divisor = ZERO_DECIMAL.has(currency) ? 1 : 100;
    try {
      return new Intl.NumberFormat(rolloutLocale(), {
        style: "currency", currency, maximumFractionDigits: divisor === 1 ? 0 : 2,
      }).format(Number(amountMinor || 0) / divisor);
    } catch {
      return `${currency} ${Number(amountMinor || 0) / divisor}`;
    }
  }

  function selectedCurrency() {
    const saved = localStorage.getItem("lecturesift-currency");
    if (saved) return saved;
    const locale = (navigator.language || "en-US").toUpperCase();
    if (locale.includes("-TR")) return "TRY";
    if (locale.includes("-GB")) return "GBP";
    if (/^(DE|FR|ES|IT|PT)/.test(locale)) return "EUR";
    return "USD";
  }

  function showInlineMessage(message, error = false) {
    if (typeof showError === "function" && error) {
      showError(message, "LS-ROLLOUT-01");
      return;
    }
    let node = $("rolloutNotice");
    if (!node) {
      node = document.createElement("div");
      node.id = "rolloutNotice";
      node.className = "rollout-status";
      document.body.appendChild(node);
    }
    node.classList.toggle("error", error);
    node.textContent = message;
  }

  function installUploadSpeedProbe() {
    if (XMLHttpRequest.prototype.__lecturesiftSpeedProbe) return;
    const originalSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function(body) {
      if (body instanceof FormData) {
        const started = performance.now();
        this.upload.addEventListener("progress", event => {
          const seconds = Math.max(.1, (performance.now() - started) / 1000);
          window.__lecturesiftUploadBps = event.loaded / seconds;
        });
      }
      return originalSend.call(this, body);
    };
    XMLHttpRequest.prototype.__lecturesiftSpeedProbe = true;
  }

  function etaText(job) {
    if (job.status === "done") return rt("rollout.jobDone", "İşlem tamamlandı.");
    const total = Math.max(0, Number(job.eta_seconds || 0));
    const progress = Math.max(0, Math.min(99, Number(job.percent || 0)));
    const elapsed = job.eta_started_at ? Math.max(0, Date.now() / 1000 - Number(job.eta_started_at)) : 0;
    let remaining = total ? total * (1 - progress / 100) : 0;
    if (progress >= 8 && elapsed > 5) {
      const observed = elapsed * (100 - progress) / progress;
      remaining = remaining ? (remaining * .55 + observed * .45) : observed;
    }
    if (!remaining) return rt("rollout.etaCalculating", "Tahmini bitiş süresi hesaplanıyor…");
    const rounded = Math.max(1, Math.round(remaining));
    const time = rounded < 60
      ? `${rounded} ${rt("rollout.seconds", "saniye")}`
      : `${Math.floor(rounded / 60)} ${rt("unit.minuteShort", "dk")}${rounded % 60 ? ` ${rounded % 60} ${rt("rollout.secondShort", "sn")}` : ""}`;
    const media = Number(job.media_minutes || 0);
    const speed = Number(window.__lecturesiftUploadBps || 0);
    const extras = [
      media ? `${rt("rollout.source", "kaynak")} ${media.toFixed(1)} ${rt("unit.minuteShort", "dk")}` : "",
      speed ? `${rt("rollout.upload", "yükleme")} ${(speed / 1024 / 1024).toFixed(1)} MB/${rt("rollout.secondShort", "sn")}` : "",
      job.worker_state === "queued" ? rt("rollout.queued", "worker sırasında") : "",
      job.worker_state === "retrying" ? rt("rollout.retrying", "otomatik yeniden deneniyor") : "",
    ].filter(Boolean).join(" · ");
    return `${rt("rollout.estimatedRemaining", "Tahmini kalan")}: ${time}${extras ? ` · ${extras}` : ""}`;
  }

  function installEta() {
    const panel = document.querySelector(".process-panel");
    if (!panel || $("rolloutEta")) return;
    const node = document.createElement("div");
    node.id = "rolloutEta";
    node.className = "rollout-eta";
    node.innerHTML = `<strong>${esc(rt("rollout.realtimeEstimate", "Gerçek zamanlı tahmin"))}</strong><span>${esc(rt("rollout.etaWhenAdded", "Dosya eklendiğinde süre hesaplanacak."))}</span>`;
    const progress = panel.querySelector(".progress-visual");
    progress?.insertAdjacentElement("afterend", node);
    if (typeof updateJobView === "function") {
      const original = updateJobView;
      updateJobView = function(job) {
        original(job);
        node.querySelector("span").textContent = etaText(job);
      };
    }
  }

  function planName(code) {
    const names = {free:"Ücretsiz",credit:"Dakika Paketi",lite:"Lite",plus:"Plus",pro:"Pro",max:"Max",business:"Business",guest:"Misafir"};
    return rt(`plan.${code}`, names[code] || code);
  }

  async function createLocalizedTransferOrder(planCode, interval) {
    const token = activeToken();
    const guest = typeof billingAccount !== "undefined" && billingAccount?.plan?.code === "guest";
    if (!token || guest) {
      document.getElementById("accountPanel")?.scrollIntoView({behavior:"smooth"});
      showInlineMessage(rt("rollout.loginToBuy", "Plan satın almak için doğrulanmış hesabınla giriş yap."), true);
      return;
    }
    try {
      const body = await api("/billing/manual-transfer/orders", {
        method:"POST", body:JSON.stringify({plan_code:planCode, interval}),
      }, token);
      const order = body.order;
      if ($("transferReference")) $("transferReference").textContent = order.reference;
      if ($("transferAmount")) $("transferAmount").textContent = formatCurrency(order.amount_minor, order.currency || "TRY");
      if ($("transferIban")) $("transferIban").textContent = String(order.bank.iban || "").replace(/(.{4})/g, "$1 ").trim();
      if ($("transferHolder")) $("transferHolder").textContent = order.bank.account_holder || "";
      if ($("transferInstruction")) $("transferInstruction").textContent = `${order.instruction} ${rt("rollout.orderNumber", "Sipariş numaran")}: ${order.reference}`;
      if ($("transferStatus")) $("transferStatus").textContent = rt("rollout.transferPending", "Havale/EFT onayı bekliyor");
      if ($("transferSupport")) $("transferSupport").href = `mailto:${encodeURIComponent(order.support_email)}?subject=${encodeURIComponent(`LectureSift ${order.reference}`)}`;
      if ($("transferPanel")) { $("transferPanel").hidden = false; $("transferPanel").scrollIntoView({behavior:"smooth", block:"center"}); }
    } catch (error) { showInlineMessage(error.message, true); }
  }

  function workspacePlanCard(plan, currency, currentCode) {
    const price = plan.display_price || plan.manual_price;
    const monthly = price ? formatCurrency(price.amount_minor, price.currency || currency) : (plan.code === "free" ? formatCurrency(0, currency) : rt("plans.quote", "Teklif"));
    const annual = price ? formatCurrency(Number(price.amount_minor) * 10, price.currency || currency) : "";
    const entitlements = plan.entitlements || {};
    const minutes = entitlements.minutes ?? plan.minutes;
    const current = currentCode === plan.code;
    const actions = plan.kind === "subscription"
      ? `<div class="rollout-plan-actions"><button data-rollout-plan="${esc(plan.code)}" data-rollout-cycle="monthly" ${current ? "disabled" : ""}>${esc(rt("rollout.chooseMonthly", "Aylık seç"))}</button><button data-rollout-plan="${esc(plan.code)}" data-rollout-cycle="annual" ${current ? "disabled" : ""}>${esc(rt("rollout.annual", "Yıllık"))} · ${esc(annual)}</button></div>`
      : plan.kind === "one_time"
        ? `<div class="rollout-plan-actions"><button data-rollout-plan="${esc(plan.code)}" data-rollout-cycle="one_time">${esc(rt("rollout.chooseOnce", "Tek seferlik seç"))}</button></div>`
        : `<div class="rollout-plan-actions"><button disabled>${esc(current ? rt("plans.current", "Mevcut plan") : plan.code === "business" ? rt("plans.contact", "Bize ulaş") : rt("rollout.included", "Dahil"))}</button></div>`;
    return `<article class="plan-card ${plan.featured ? "featured" : ""}">
      ${plan.featured ? `<span class="plan-badge">${esc(rt("plans.popular", "Popüler"))}</span>` : ""}
      <h3>${esc(planName(plan.code))}</h3>
      <div class="plan-price">${esc(monthly)} <small>${plan.kind === "subscription" ? rt("plans.perMonth", "/ ay") : plan.kind === "one_time" ? rt("plans.oneTimeShort", "tek ödeme") : ""}</small></div>
      <ul class="plan-features"><li>${minutes == null ? esc(rt("rollout.enterpriseCapacity", "Kurumsal kapasite")) : `${Number(minutes).toLocaleString(rolloutLocale())} ${esc(rt("plans.minuteUnit", "dakika"))}`}</li><li>${entitlements.quiz_questions ?? plan.quiz_questions ?? "∞"} ${esc(rt("plans.quizShort", "quiz"))}</li><li>${entitlements.flashcards ?? plan.flashcards ?? "∞"} ${esc(rt("plans.cardsShort", "bilgi kartı"))}</li><li>${esc((entitlements.export_formats || plan.export_formats || []).join(", ").toUpperCase())}</li></ul>
      ${actions}
    </article>`;
  }

  async function installWorkspacePlans() {
    const grid = $("plansGrid");
    const heading = document.querySelector("#plans .billing-heading");
    if (!grid || !heading) return;
    const selector = $("billingCurrency");
    let currency = selector?.value || selectedCurrency();
    if (!document.querySelector("#plans .rollout-guest-note")) {
      const note = document.createElement("p");
      note.className = "rollout-guest-note";
      note.textContent = rt("rollout.pricingNote", "Fiyatlar seçilen para biriminde gösterilir. Havale/EFT siparişi, oluşturulduğunda ekranda görünen kesin TRY tutarıyla ödenir; açıklamaya sipariş numarası yazılır.");
      grid.insertAdjacentElement("beforebegin", note);
    }
    async function load() {
      try {
        currency = selector?.value || currency;
        const body = await api(`/billing/plans?currency=${encodeURIComponent(currency)}`, {}, "");
        if (typeof billingCatalog !== "undefined") billingCatalog = body;
        const currentCode = typeof billingAccount !== "undefined" ? billingAccount?.plan?.code : "";
        grid.innerHTML = (body.plans || []).map(plan => workspacePlanCard(plan, currency, currentCode)).join("");
        grid.querySelectorAll("[data-rollout-plan]").forEach(button => {
          button.onclick = () => createLocalizedTransferOrder(button.dataset.rolloutPlan, button.dataset.rolloutCycle);
        });
      } catch (error) { showInlineMessage(error.message, true); }
    }
    if (typeof renderPlans === "function") renderPlans = load;
    await load();
  }

  async function installWorkspace() {
    installUploadSpeedProbe();
    installEta();
    const button = $("analyzeButton");
    if (button?.onclick && !button.dataset.guestWrapped) {
      const original = button.onclick;
      button.onclick = async function(event) {
        if (typeof billingAccount !== "undefined" && billingAccount?.plan?.code === "guest" && guestTrialState?.used) {
          updateGuestTrialUi();
          showInlineMessage(
            rt("rollout.guestUsed", "Tek seferlik 5 dakikalık denemen kullanıldı. Devam etmek için ücretsiz hesap oluştur."),
            true,
          );
          return;
        }
        if (!activeToken()) {
          try { await ensureGuestIdentity(); }
          catch (error) { showInlineMessage(error.message, true); return; }
        }
        return original.call(this, event);
      };
      button.dataset.guestWrapped = "1";
      const note = document.createElement("p");
      note.className = "rollout-guest-note";
      note.textContent = rt("rollout.guestNote", "Hesapsız kullanım: bu cihazda tek seferlik, en fazla 5 dakikalık kaynak. Daha uzun işlemler için ücretsiz hesap oluştur.");
      button.insertAdjacentElement("afterend", note);
      updateGuestTrialUi();
    }
    if (typeof renderBillingAccount === "function") {
      const originalRender = renderBillingAccount;
      renderBillingAccount = function() {
        originalRender();
        if (typeof billingAccount !== "undefined" && billingAccount?.plan?.code === "guest") {
          if ($("accountButton")) $("accountButton").textContent = rt("rollout.guestTrial", "Misafir deneme");
          if ($("accountEmail")) $("accountEmail").textContent = rt("rollout.noAccountTrial", "Hesapsız deneme");
          if ($("accountPlan")) $("accountPlan").textContent = rt("rollout.singleUse", "Tek kullanımlık 5 dakika");
        } else if ($("accountStatus") && !$("rolloutAccountLink")) {
          const link = document.createElement("a");
          link.id = "rolloutAccountLink";
          link.className = "rollout-account-link";
          link.href = "/account.html";
          link.textContent = `${rt("rollout.manageProfileOrders", "Profili ve siparişleri yönet")} →`;
          $("accountStatus").appendChild(link);
        }
      };
    }
    if (typeof renderResult === "function") {
      const originalResult = renderResult;
      renderResult = function(data) {
        originalResult(data);
        if ($("downloadAll")) {
          $("downloadAll").onclick = event => {
            event.preventDefault();
            if (typeof downloadProtected === "function") downloadProtected(`/jobs/${jobId}/download`, "LectureSift_Paketi.zip");
          };
        }
      };
    }
    await restoreGuestIdentity();
    await installWorkspacePlans();
  }

  async function installPlansPage() {
    // plans.js owns the dedicated pricing page. Keeping this hook as a no-op
    // prevents the legacy workspace renderer from replacing secure checkout.
    return;
  }

  function accountToken() { return localStorage.getItem(TOKEN_KEY) || ""; }

  async function installAccountExtensions() {
    if (!document.body.matches('[data-page="account"]')) return;
    const token = accountToken();
    if (!token) return;
    const wait = async () => {
      const grid = document.querySelector(".dashboard-grid");
      if (!grid || $("rolloutEmailCard")) return false;
      try {
        const [me, rollout] = await Promise.all([api("/billing/me", {}, token), api("/billing/me/rollout", {}, token)]);
        const rewarded = rollout.rewarded_ads;
        const rewardedToday = rewarded
          ? rt("ads.today", "Bugün kazanılan: {earned} / {limit} dakika")
              .replace("{earned}", rewarded.earned_today)
              .replace("{limit}", rewarded.daily_limit_minutes)
          : "";
        const rewardedCard = rewarded?.configured && !rewarded.plan_ad_free && !rewarded.guest ? `
          <section class="dashboard-card rollout-card"><h2>${esc(rt("ads.title", "Reklamla dakika kazan"))}</h2><p>${esc(rt("ads.help", "İstersen kısa bir ödüllü reklam izle. Atlayabilir ve LectureSift'i normal biçimde kullanmaya devam edebilirsin."))}</p><p id="rewardedAdsToday" class="rollout-muted">${esc(rewardedToday)}</p><button id="rewardedAdsButton" class="rollout-action" type="button" ${rewarded.enabled ? "" : "disabled"}>${esc(rt("ads.cta", "Reklamı izle ve dakika kazan"))}</button><div id="rewardedAdsStatus" class="rollout-status" hidden></div></section>` : "";
        grid.insertAdjacentHTML("beforeend", `
          <section id="rolloutEmailCard" class="dashboard-card rollout-card"><h2>${esc(rt("rollout.changeEmail", "E-posta adresini değiştir"))}</h2><form id="rolloutEmailForm" class="rollout-form"><label>${esc(rt("rollout.newEmail", "Yeni e-posta"))}<input id="rolloutNewEmail" type="email" autocomplete="email"></label><button type="submit">${esc(rt("rollout.sendVerification", "Doğrulama kodu gönder"))}</button></form><form id="rolloutEmailVerifyForm" class="rollout-form" hidden><label>${esc(rt("rollout.sixDigitCode", "6 haneli kod"))}<input id="rolloutEmailCode" class="code-input" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6"></label><button type="submit">${esc(rt("rollout.finishEmailChange", "E-posta değişikliğini tamamla"))}</button></form><div id="rolloutEmailStatus" class="rollout-status" hidden></div></section>
          <section class="dashboard-card rollout-card"><h2>${esc(rt("rollout.instagramBonus", "Instagram takip bonusu"))}</h2><p>${esc(rt("rollout.instagramHelp", "LectureSift Instagram hesabını takip et, kullanıcı adını gönder. Takip doğrulandıktan sonra hesabına bir kez 30 dakika eklenir."))}</p><form id="rolloutInstagramForm" class="rollout-form"><label>${esc(rt("rollout.instagramHandle", "Instagram kullanıcı adı"))}<input id="rolloutInstagramHandle" placeholder="@kullanici"></label><button type="submit" ${rollout.instagram_reward ? "disabled" : ""}>${esc(rollout.instagram_reward ? rt("rollout.requestCreated", "Talep oluşturuldu") : rt("rollout.requestMinutes", "+30 dakika talep et"))}</button></form><div id="rolloutInstagramStatus" class="rollout-status" ${rollout.instagram_reward ? "" : "hidden"}>${esc(rollout.instagram_reward ? `${rt("admin.status", "Durum")}: ${rt(`order.${rollout.instagram_reward.status}`, rollout.instagram_reward.status)}` : "")}</div></section>
          ${rewardedCard}`);
        $("rolloutEmailForm").onsubmit = async event => {
          event.preventDefault(); const status = $("rolloutEmailStatus");
          try {
            const body = await api("/billing/me/email-change", {method:"POST", body:JSON.stringify({email:$("rolloutNewEmail").value})}, token);
            status.textContent = rt("rollout.verificationSent", body.message); status.hidden = false; status.classList.remove("error"); $("rolloutEmailVerifyForm").hidden = false;
          } catch (error) { status.textContent = error.message; status.hidden = false; status.classList.add("error"); }
        };
        $("rolloutEmailVerifyForm").onsubmit = async event => {
          event.preventDefault(); const status = $("rolloutEmailStatus");
          try {
            const body = await api("/billing/me/email-change/verify", {method:"POST", body:JSON.stringify({code:$("rolloutEmailCode").value})}, token);
            localStorage.setItem(TOKEN_KEY, body.token); status.textContent = rt("rollout.emailChanged", body.message); status.hidden = false; status.classList.remove("error");
            if ($("accountEmail")) $("accountEmail").textContent = body.account.user.email;
          } catch (error) { status.textContent = error.message; status.hidden = false; status.classList.add("error"); }
        };
        $("rolloutInstagramForm").onsubmit = async event => {
          event.preventDefault(); const status = $("rolloutInstagramStatus");
          try {
            const body = await api("/billing/instagram-reward", {method:"POST", body:JSON.stringify({handle:$("rolloutInstagramHandle").value})}, token);
            status.textContent = rt("rollout.bonusRequested", body.message); status.hidden = false; status.classList.remove("error"); event.submitter.disabled = true;
          } catch (error) { status.textContent = error.message; status.hidden = false; status.classList.add("error"); }
        };
        if ($("rewardedAdsButton")) $("rewardedAdsButton").onclick = async event => {
          const button = event.currentTarget;
          const status = $("rewardedAdsStatus");
          status.hidden = false;
          status.classList.remove("error");
          if (!window.LectureSiftConsent?.allows("advertising")) {
            status.textContent = rt("ads.consent", "Reklamı gösterebilmek için gizlilik tercihlerinden reklam izni vermelisin.");
            window.LectureSiftConsent?.open();
            return;
          }
          button.disabled = true;
          status.textContent = rt("ads.loading", "Reklam hazırlanıyor…");
          try {
            const issued = await api("/billing/rewarded-ads/session", {method:"POST"}, token);
            const completed = await window.LectureSiftRewardedAds?.show(issued.session.ad_unit_path);
            if (!completed) throw new Error(rt("ads.unavailable", "Şu anda uygun reklam bulunamadı. Daha sonra yeniden deneyebilirsin."));
            const body = await api("/billing/rewarded-ads/claim", {method:"POST", body:JSON.stringify({session_id:issued.session.session_id, claim_token:issued.session.claim_token})}, token);
            status.textContent = rt("ads.rewarded", "{minutes} dakika hesabına eklendi.").replace("{minutes}", body.minutes_added);
            if ($("creditMinutes")) $("creditMinutes").textContent = body.account.credit_minutes;
            if ($("remainingMinutes")) $("remainingMinutes").textContent = body.account.remaining_minutes ?? "∞";
            const state = body.rewarded_ads;
            $("rewardedAdsToday").textContent = rt("ads.today", "Bugün kazanılan: {earned} / {limit} dakika").replace("{earned}", state.earned_today).replace("{limit}", state.daily_limit_minutes);
            button.disabled = !state.enabled;
          } catch (error) {
            status.textContent = error.message?.startsWith("rewarded-ad-") ? rt("ads.unavailable", "Şu anda uygun reklam bulunamadı. Daha sonra yeniden deneyebilirsin.") : error.message;
            status.classList.add("error");
            button.disabled = false;
          }
        };
        return true;
      } catch { return false; }
    };
    if (await wait()) return;
    const timer = setInterval(async () => { if (await wait()) clearInterval(timer); }, 300);
    setTimeout(() => clearInterval(timer), 10000);
  }

  const path = location.pathname;
  if (path === "/workspace.html" || path.endsWith("/workspace.html")) installWorkspace();
  if (path.endsWith("/plans.html")) installPlansPage();
  installAccountExtensions();
})();
