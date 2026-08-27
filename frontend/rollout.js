(() => {
  const API_BASE = "https://lecturesift-backend.onrender.com";
  const TOKEN_KEY = "lecturesift-billing-token";
  const GUEST_TOKEN_KEY = "lecturesift-guest-token";
  const DEVICE_KEY = "lecturesift-guest-device";
  const ZERO_DECIMAL = new Set(["JPY", "KRW"]);
  const $ = id => document.getElementById(id);
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
      throw Object.assign(new Error(detail.message || "İşlem tamamlanamadı."), {code: detail.code});
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

  function setWorkspaceIdentity(token, account, guest = false) {
    if (typeof billingToken !== "undefined") billingToken = token;
    if (typeof billingAccount !== "undefined") billingAccount = account;
    if (guest) sessionStorage.setItem(GUEST_TOKEN_KEY, token);
    if (typeof renderBillingAccount === "function") renderBillingAccount();
    if (typeof renderPlans === "function") renderPlans();
  }

  async function ensureGuestIdentity() {
    if (activeToken()) return activeToken();
    const body = await api("/billing/guest-session", {
      method: "POST",
      body: JSON.stringify({device_id: deviceId()}),
    }, "");
    setWorkspaceIdentity(body.token, body.account, true);
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
      const body = await api("/billing/me", {}, token);
      setWorkspaceIdentity(token, body.account, true);
    } catch {
      sessionStorage.removeItem(GUEST_TOKEN_KEY);
    }
  }

  function formatCurrency(amountMinor, currency) {
    const divisor = ZERO_DECIMAL.has(currency) ? 1 : 100;
    try {
      return new Intl.NumberFormat(navigator.language || "tr-TR", {
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
    if (job.status === "done") return "İşlem tamamlandı.";
    const total = Math.max(0, Number(job.eta_seconds || 0));
    const progress = Math.max(0, Math.min(99, Number(job.percent || 0)));
    const elapsed = job.eta_started_at ? Math.max(0, Date.now() / 1000 - Number(job.eta_started_at)) : 0;
    let remaining = total ? total * (1 - progress / 100) : 0;
    if (progress >= 8 && elapsed > 5) {
      const observed = elapsed * (100 - progress) / progress;
      remaining = remaining ? (remaining * .55 + observed * .45) : observed;
    }
    if (!remaining) return "Tahmini bitiş süresi hesaplanıyor…";
    const rounded = Math.max(1, Math.round(remaining));
    const time = rounded < 60
      ? `${rounded} saniye`
      : `${Math.floor(rounded / 60)} dk${rounded % 60 ? ` ${rounded % 60} sn` : ""}`;
    const media = Number(job.media_minutes || 0);
    const speed = Number(window.__lecturesiftUploadBps || 0);
    const extras = [
      media ? `kaynak ${media.toFixed(1)} dk` : "",
      speed ? `yükleme ${(speed / 1024 / 1024).toFixed(1)} MB/sn` : "",
      job.worker_state === "queued" ? "worker sırasında" : "",
      job.worker_state === "retrying" ? "otomatik yeniden deneniyor" : "",
    ].filter(Boolean).join(" · ");
    return `Tahmini kalan: ${time}${extras ? ` · ${extras}` : ""}`;
  }

  function installEta() {
    const panel = document.querySelector(".process-panel");
    if (!panel || $("rolloutEta")) return;
    const node = document.createElement("div");
    node.id = "rolloutEta";
    node.className = "rollout-eta";
    node.innerHTML = "<strong>Gerçek zamanlı tahmin</strong><span>Dosya eklendiğinde süre hesaplanacak.</span>";
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
    return names[code] || code;
  }

  async function createLocalizedTransferOrder(planCode, interval) {
    const token = activeToken();
    const guest = typeof billingAccount !== "undefined" && billingAccount?.plan?.code === "guest";
    if (!token || guest) {
      document.getElementById("accountPanel")?.scrollIntoView({behavior:"smooth"});
      showInlineMessage("Plan satın almak için doğrulanmış hesabınla giriş yap.", true);
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
      if ($("transferInstruction")) $("transferInstruction").textContent = `${order.instruction} Sipariş numaran: ${order.reference}`;
      if ($("transferStatus")) $("transferStatus").textContent = "Havale/EFT onayı bekliyor";
      if ($("transferSupport")) $("transferSupport").href = `mailto:${encodeURIComponent(order.support_email)}?subject=${encodeURIComponent(`LectureSift ${order.reference}`)}`;
      if ($("transferPanel")) { $("transferPanel").hidden = false; $("transferPanel").scrollIntoView({behavior:"smooth", block:"center"}); }
    } catch (error) { showInlineMessage(error.message, true); }
  }

  function workspacePlanCard(plan, currency, currentCode) {
    const price = plan.display_price || plan.manual_price;
    const monthly = price ? formatCurrency(price.amount_minor, price.currency || currency) : (plan.code === "free" ? formatCurrency(0, currency) : "Teklif");
    const annual = price ? formatCurrency(Number(price.amount_minor) * 10, price.currency || currency) : "";
    const entitlements = plan.entitlements || {};
    const minutes = entitlements.minutes ?? plan.minutes;
    const current = currentCode === plan.code;
    const actions = plan.kind === "subscription"
      ? `<div class="rollout-plan-actions"><button data-rollout-plan="${esc(plan.code)}" data-rollout-cycle="monthly" ${current ? "disabled" : ""}>Aylık seç</button><button data-rollout-plan="${esc(plan.code)}" data-rollout-cycle="annual" ${current ? "disabled" : ""}>Yıllık · ${esc(annual)}</button></div>`
      : plan.kind === "one_time"
        ? `<div class="rollout-plan-actions"><button data-rollout-plan="${esc(plan.code)}" data-rollout-cycle="one_time">Tek seferlik seç</button></div>`
        : `<div class="rollout-plan-actions"><button disabled>${current ? "Mevcut plan" : plan.code === "business" ? "Bize ulaş" : "Dahil"}</button></div>`;
    return `<article class="plan-card ${plan.featured ? "featured" : ""}">
      ${plan.featured ? '<span class="plan-badge">Popüler</span>' : ""}
      <h3>${esc(planName(plan.code))}</h3>
      <div class="plan-price">${esc(monthly)} <small>${plan.kind === "subscription" ? "/ ay" : plan.kind === "one_time" ? "tek ödeme" : ""}</small></div>
      <ul class="plan-features"><li>${minutes == null ? "Kurumsal kapasite" : `${Number(minutes).toLocaleString("tr-TR")} dakika`}</li><li>${entitlements.quiz_questions ?? plan.quiz_questions ?? "∞"} quiz</li><li>${entitlements.flashcards ?? plan.flashcards ?? "∞"} bilgi kartı</li><li>${esc((entitlements.export_formats || plan.export_formats || []).join(", ").toUpperCase())}</li></ul>
      ${actions}
    </article>`;
  }

  async function installWorkspacePlans() {
    const grid = $("plansGrid");
    const heading = document.querySelector("#plans .billing-heading");
    if (!grid || !heading) return;
    let currency = selectedCurrency();
    let label = $("rolloutCurrency");
    if (!label) {
      label = document.createElement("label");
      label.id = "rolloutCurrency";
      label.className = "rollout-currency";
      label.innerHTML = '<span>Para birimi</span><select></select>';
      heading.appendChild(label);
      const supported = window.LECTURESIFT_LOCALE_DATA?.currencies || ["TRY","USD","EUR","GBP","CAD","AUD","JPY"];
      label.querySelector("select").replaceChildren(...supported.map(code => new Option(code, code)));
      label.querySelector("select").value = supported.includes(currency) ? currency : "USD";
      currency = label.querySelector("select").value;
      label.querySelector("select").onchange = () => {
        currency = label.querySelector("select").value;
        localStorage.setItem("lecturesift-currency", currency);
        load();
      };
      const note = document.createElement("p");
      note.className = "rollout-guest-note";
      note.textContent = "Fiyatlar seçilen para biriminde gösterilir. Havale/EFT siparişi, oluşturulduğunda ekranda görünen kesin TRY tutarıyla ödenir; açıklamaya sipariş numarası yazılır.";
      grid.insertAdjacentElement("beforebegin", note);
    }
    async function load() {
      try {
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
        if (!activeToken()) {
          try { await ensureGuestIdentity(); }
          catch (error) { showInlineMessage(error.message, true); return; }
        }
        return original.call(this, event);
      };
      button.dataset.guestWrapped = "1";
      const note = document.createElement("p");
      note.className = "rollout-guest-note";
      note.textContent = "Hesapsız kullanım: bu cihazda tek seferlik, en fazla 5 dakikalık kaynak. Daha uzun işlemler için ücretsiz hesap oluştur.";
      button.insertAdjacentElement("afterend", note);
    }
    if (typeof renderBillingAccount === "function") {
      const originalRender = renderBillingAccount;
      renderBillingAccount = function() {
        originalRender();
        if (typeof billingAccount !== "undefined" && billingAccount?.plan?.code === "guest") {
          if ($("accountButton")) $("accountButton").textContent = "Misafir deneme";
          if ($("accountEmail")) $("accountEmail").textContent = "Hesapsız deneme";
          if ($("accountPlan")) $("accountPlan").textContent = "Tek kullanımlık 5 dakika";
        } else if ($("accountStatus") && !$("rolloutAccountLink")) {
          const link = document.createElement("a");
          link.id = "rolloutAccountLink";
          link.className = "rollout-account-link";
          link.href = "/account.html";
          link.textContent = "Profili ve siparişleri yönet →";
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
    const grid = $("plansGrid");
    if (!grid || !$("billingCurrency")) return;
    const redraw = () => {
      if (typeof catalog === "undefined" || !catalog) return;
      const currentCode = typeof account !== "undefined" ? account?.plan?.code : "";
      grid.innerHTML = (catalog.plans || []).map(plan => workspacePlanCard(plan, catalog.selected_currency || currency || "TRY", currentCode)).join("");
      grid.querySelectorAll("[data-rollout-plan]").forEach(button => {
        button.onclick = () => createLocalizedTransferOrder(button.dataset.rolloutPlan, button.dataset.rolloutCycle);
      });
    };
    if (typeof renderPlans === "function") renderPlans = redraw;
    setTimeout(redraw, 250);
  }

  function accountToken() { return localStorage.getItem(TOKEN_KEY) || ""; }

  async function installAccountExtensions() {
    if (!document.body.matches('[data-page="account"]')) return;
    const token = accountToken();
    if (!token) return;
    const wait = async () => {
      const grid = document.querySelector(".dashboard-grid");
      if (!grid || $("rolloutProfileCard")) return false;
      try {
        const [me, rollout] = await Promise.all([api("/billing/me", {}, token), api("/billing/me/rollout", {}, token)]);
        const user = me.account.user;
        grid.insertAdjacentHTML("beforeend", `
          <section id="rolloutProfileCard" class="dashboard-card rollout-card"><h2>Profili düzenle</h2><form id="rolloutProfileForm" class="rollout-form"><label>Ad<input id="rolloutFirstName" value="${esc(user.first_name || "")}"></label><label>Soyad<input id="rolloutLastName" value="${esc(user.last_name || "")}"></label><label>Telefon<input id="rolloutPhone" value="${esc(user.phone || "")}" autocomplete="tel"></label><button type="submit">Profili kaydet</button></form><div id="rolloutProfileStatus" class="rollout-status" hidden></div></section>
          <section class="dashboard-card rollout-card"><h2>E-posta adresini değiştir</h2><form id="rolloutEmailForm" class="rollout-form"><label>Yeni e-posta<input id="rolloutNewEmail" type="email" autocomplete="email"></label><button type="submit">Doğrulama kodu gönder</button></form><form id="rolloutEmailVerifyForm" class="rollout-form" hidden><label>6 haneli kod<input id="rolloutEmailCode" inputmode="numeric" maxlength="6"></label><button type="submit">E-posta değişikliğini tamamla</button></form><div id="rolloutEmailStatus" class="rollout-status" hidden></div></section>
          <section class="dashboard-card rollout-card"><h2>Instagram takip bonusu</h2><p>LectureSift Instagram hesabını takip et, kullanıcı adını gönder. Takip doğrulandıktan sonra hesabına bir kez 30 dakika eklenir.</p><form id="rolloutInstagramForm" class="rollout-form"><label>Instagram kullanıcı adı<input id="rolloutInstagramHandle" placeholder="@kullanici"></label><button type="submit" ${rollout.instagram_reward ? "disabled" : ""}>${rollout.instagram_reward ? "Talep oluşturuldu" : "+30 dakika talep et"}</button></form><div id="rolloutInstagramStatus" class="rollout-status" ${rollout.instagram_reward ? "" : "hidden"}>${esc(rollout.instagram_reward ? `Durum: ${rollout.instagram_reward.status}` : "")}</div></section>`);
        $("rolloutProfileForm").onsubmit = async event => {
          event.preventDefault(); const status = $("rolloutProfileStatus");
          try {
            const body = await api("/billing/me/profile", {method:"PATCH", body:JSON.stringify({first_name:$("rolloutFirstName").value,last_name:$("rolloutLastName").value,phone:$("rolloutPhone").value})}, token);
            status.textContent = body.message; status.hidden = false; status.classList.remove("error");
            if ($("accountName")) $("accountName").textContent = body.account.user.name || body.account.user.email;
          } catch (error) { status.textContent = error.message; status.hidden = false; status.classList.add("error"); }
        };
        $("rolloutEmailForm").onsubmit = async event => {
          event.preventDefault(); const status = $("rolloutEmailStatus");
          try {
            const body = await api("/billing/me/email-change", {method:"POST", body:JSON.stringify({email:$("rolloutNewEmail").value})}, token);
            status.textContent = body.message; status.hidden = false; status.classList.remove("error"); $("rolloutEmailVerifyForm").hidden = false;
          } catch (error) { status.textContent = error.message; status.hidden = false; status.classList.add("error"); }
        };
        $("rolloutEmailVerifyForm").onsubmit = async event => {
          event.preventDefault(); const status = $("rolloutEmailStatus");
          try {
            const body = await api("/billing/me/email-change/verify", {method:"POST", body:JSON.stringify({code:$("rolloutEmailCode").value})}, token);
            localStorage.setItem(TOKEN_KEY, body.token); status.textContent = body.message; status.hidden = false; status.classList.remove("error");
            if ($("accountEmail")) $("accountEmail").textContent = body.account.user.email;
          } catch (error) { status.textContent = error.message; status.hidden = false; status.classList.add("error"); }
        };
        $("rolloutInstagramForm").onsubmit = async event => {
          event.preventDefault(); const status = $("rolloutInstagramStatus");
          try {
            const body = await api("/billing/instagram-reward", {method:"POST", body:JSON.stringify({handle:$("rolloutInstagramHandle").value})}, token);
            status.textContent = body.message; status.hidden = false; status.classList.remove("error"); event.submitter.disabled = true;
          } catch (error) { status.textContent = error.message; status.hidden = false; status.classList.add("error"); }
        };
        return true;
      } catch { return false; }
    };
    if (await wait()) return;
    const timer = setInterval(async () => { if (await wait()) clearInterval(timer); }, 300);
    setTimeout(() => clearInterval(timer), 10000);
  }

  const path = location.pathname;
  if (path === "/" || path.endsWith("/index.html")) installWorkspace();
  if (path.endsWith("/plans.html")) installPlansPage();
  installAccountExtensions();
})();
