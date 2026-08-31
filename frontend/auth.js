const API = "https://api.lecturesift.com";
const TOKEN_KEY = "lecturesift-billing-token";
const LOCALE_DATA = window.LECTURESIFT_LOCALE_DATA || {countries: [], currencies: [], currencyForCountry: {}};
const I18N = window.LectureSiftI18n || {language:"tr", locale:"tr-TR", languages:{tr:"Türkçe"}, t:(key, fallback)=>fallback || key};
const page = document.body.dataset.page || "login";
const $ = id => document.getElementById(id);
const t = (key, fallback) => I18N.t(key, fallback);

function recordAnalytics(type, name, parameters = {}) {
  const analytics = window.LectureSiftAnalytics;
  if (type === "conversion" && analytics?.trackConversion) return void analytics.trackConversion(name, parameters);
  if (type === "event" && analytics?.track) return void analytics.track(name, parameters);
  window.__lecturesiftAnalyticsQueue = window.__lecturesiftAnalyticsQueue || [];
  window.__lecturesiftAnalyticsQueue.push({type, name, parameters});
}

function errorMessage(body, fallback) {
  return body?.detail?.message || body?.message || fallback;
}

function showNotice(message, isError = false) {
  const node = $("authNotice");
  if (!node) return;
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.hidden = false;
}

async function request(path, options = {}, token = "") {
  const headers = {"Content-Type": "application/json", ...(options.headers || {})};
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${API}${path}`, {...options, headers});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorMessage(body, t("error.request", "İstek tamamlanamadı.")));
  return body;
}

function setBusy(button, busy, label) {
  if (!button) return;
  if (busy) button.dataset.label = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? label : (button.dataset.label || button.textContent);
}

function selectedCountry() {
  const saved = localStorage.getItem("lecturesift-country");
  if (saved?.length === 2) return saved.toUpperCase();
  const localeCountry = (navigator.language.split("-")[1] || "").toUpperCase();
  return localeCountry.length === 2 ? localeCountry : (Intl.DateTimeFormat().resolvedOptions().timeZone === "Europe/Istanbul" ? "TR" : "US");
}

function populateCountrySelect(select, selected = "") {
  if (!select || !LOCALE_DATA.countries.length) return;
  let names;
  try { names = new Intl.DisplayNames([I18N.language, "en"], {type: "region"}); } catch { names = null; }
  const options = LOCALE_DATA.countries
    .map(code => ({code, label: names?.of(code) || code}))
    .sort((left, right) => left.label.localeCompare(right.label, I18N.language));
  select.replaceChildren(...options.map(item => new Option(item.label, item.code)));
  select.value = LOCALE_DATA.countries.includes(selected) ? selected : "TR";
}

function safeNext() {
  const value = new URLSearchParams(location.search).get("next") || "/account.html";
  return value.startsWith("/") && !value.startsWith("//") ? value : "/account.html";
}

async function initRegister() {
  populateCountrySelect($("countryCode"), selectedCountry());
  $("registerForm").addEventListener("submit", async event => {
    event.preventDefault();
    const button = $("registerSubmit");
    const password = $("password").value;
    if (password !== $("passwordConfirm").value) return showNotice(t("auth.passwordMismatch", "Parolalar birbiriyle eşleşmiyor."), true);
    if (!$("terms").checked) return showNotice(t("auth.acceptRequired", "Devam etmek için kullanım ve gizlilik koşullarını kabul et."), true);
    setBusy(button, true, t("auth.preparing", "Hesap hazırlanıyor…"));
    try {
      const body = await request("/billing/register", {
        method: "POST",
        body: JSON.stringify({
          first_name: $("firstName").value.trim(),
          last_name: $("lastName").value.trim(),
          email: $("email").value.trim(),
          phone: $("phone").value.trim(),
          country_code: $("countryCode").value,
          password,
        }),
      });
      $("registerForm").hidden = true;
      $("successBox").hidden = false;
      $("successEmail").textContent = body.user.email;
      $("enterCodeLink").href = `/verify.html?email=${encodeURIComponent(body.user.email)}`;
      localStorage.setItem("lecturesift-country", body.user.country_code);
      const suggestedCurrency = LOCALE_DATA.currencyForCountry[body.user.country_code];
      if (suggestedCurrency) localStorage.setItem("lecturesift-currency", suggestedCurrency);
      recordAnalytics("event", "sign_up", {method: "email"});
      recordAnalytics("conversion", "signup", {});
    } catch (error) { showNotice(error.message, true); }
    finally { setBusy(button, false, t("auth.create", "Hesap oluştur")); }
  });
}

async function initLogin() {
  if (localStorage.getItem(TOKEN_KEY)) location.replace("/account.html");
  $("loginForm").addEventListener("submit", async event => {
    event.preventDefault();
    const button = $("loginSubmit");
    setBusy(button, true, t("auth.signingIn", "Giriş yapılıyor…"));
    try {
      const body = await request("/billing/login", {
        method: "POST",
        body: JSON.stringify({email: $("email").value.trim(), password: $("password").value}),
      });
      localStorage.setItem(TOKEN_KEY, body.token);
      location.replace(safeNext());
    } catch (error) { showNotice(error.message, true); }
    finally { setBusy(button, false, t("auth.signIn", "Giriş yap")); }
  });
  $("resendButton").addEventListener("click", async () => {
    const email = $("email").value.trim();
    if (!email) return showNotice(t("auth.enterEmailFirst", "Önce e-posta adresini gir."), true);
    try {
      const body = await request("/billing/resend-verification", {method:"POST", body:JSON.stringify({email})});
      showNotice(body.message);
    } catch (error) { showNotice(error.message, true); }
  });
}

async function initVerify() {
  const params = new URLSearchParams(location.search);
  const token = params.get("token") || "";
  const email = params.get("email") || "";
  $("verifyEmail").value = email;
  const complete = body => {
    localStorage.setItem(TOKEN_KEY, body.token);
    $("verifyTitle").textContent = t("auth.emailVerified", "E-posta doğrulandı");
    $("verifyText").textContent = t("auth.accountActive", "Hesabın etkin. LectureSift çalışma alanına geçebilirsin.");
    $("verifyCodeForm").hidden = true;
    $("verifyAction").hidden = false;
  };
  if (token) {
    $("verifyText").textContent = t("auth.checkingLink", "Güvenli bağlantın kontrol ediliyor.");
    try {
      complete(await request("/billing/verify-email", {method:"POST", body:JSON.stringify({token})}));
    } catch (error) {
      $("verifyTitle").textContent = t("auth.linkFailed", "Bağlantı doğrulanamadı");
      $("verifyText").textContent = t("auth.tryCode", "E-postandaki altı haneli kodu kullanmayı deneyebilirsin.");
      showNotice(error.message, true);
    }
  }
  $("verifyCodeForm").addEventListener("submit", async event => {
    event.preventDefault();
    const button = $("verifyCodeSubmit");
    setBusy(button, true, t("auth.verifying", "Doğrulanıyor…"));
    try {
      complete(await request("/billing/verify-email-code", {method:"POST", body:JSON.stringify({email:$("verifyEmail").value.trim(), code:$("verifyCode").value.trim()})}));
    } catch (error) { showNotice(error.message, true); }
    finally { setBusy(button, false, t("auth.verifyByCode", "Kodla doğrula")); }
  });
}

async function initForgot() {
  $("forgotForm").addEventListener("submit", async event => {
    event.preventDefault();
    const button = $("forgotSubmit");
    setBusy(button, true, t("auth.sending", "Gönderiliyor…"));
    try {
      const body = await request("/billing/forgot-password", {method:"POST", body:JSON.stringify({email:$("email").value.trim()})});
      showNotice(body.message);
    } catch (error) { showNotice(error.message, true); }
    finally { setBusy(button, false, t("auth.sendReset", "Yenileme bağlantısı gönder")); }
  });
}

async function initReset() {
  const token = new URLSearchParams(location.search).get("token") || "";
  if (!token) showNotice(t("auth.resetMissing", "Şifre yenileme bağlantısı eksik."), true);
  $("resetForm").addEventListener("submit", async event => {
    event.preventDefault();
    const password = $("password").value;
    if (password !== $("passwordConfirm").value) return showNotice(t("auth.passwordMismatch", "Parolalar birbiriyle eşleşmiyor."), true);
    const button = $("resetSubmit");
    setBusy(button, true, t("auth.resetting", "Şifre yenileniyor…"));
    try {
      const body = await request("/billing/reset-password", {method:"POST", body:JSON.stringify({token, new_password:password})});
      localStorage.removeItem(TOKEN_KEY);
      $("resetForm").hidden = true;
      showNotice(body.message);
      $("loginAfterReset").hidden = false;
    } catch (error) { showNotice(error.message, true); }
    finally { setBusy(button, false, t("auth.resetPassword", "Şifreyi yenile")); }
  });
}

function planName(code) {
  return t(`plan.${code}`, code);
}

async function initAccount() {
  let token = localStorage.getItem(TOKEN_KEY);
  if (!token) return location.replace("/login.html?next=/account.html");
  let currentAccount = null;
  const accountViews = ["overview", "profile", "payments", "lessons", "security"];
  const accountViewKey = "lecturesift-account-view";

  const activateAccountView = (requested, {focus = false, updateHash = true} = {}) => {
    const view = accountViews.includes(requested) ? requested : "overview";
    document.querySelectorAll("[data-account-view]").forEach(panel => {
      const selected = panel.dataset.accountView === view;
      panel.hidden = !selected;
      panel.setAttribute("aria-hidden", String(!selected));
    });
    document.querySelectorAll("[data-account-view-button]").forEach(button => {
      const selected = button.dataset.accountViewButton === view;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      if (selected && focus) {
        button.focus({preventScroll:true});
        button.scrollIntoView({behavior:"smooth", block:"nearest", inline:"center"});
      }
    });
    sessionStorage.setItem(accountViewKey, view);
    if (updateHash) history.replaceState(null, "", `${location.pathname}${location.search}#account-${view}`);
  };

  const setupAccountNavigation = () => {
    const buttons = [...document.querySelectorAll("[data-account-view-button]")];
    buttons.forEach((button, index) => {
      const panel = document.querySelector(`[data-account-view="${button.dataset.accountViewButton}"]`);
      button.id = `accountViewTab-${button.dataset.accountViewButton}`;
      panel?.setAttribute("aria-labelledby", button.id);
      button.addEventListener("click", () => activateAccountView(button.dataset.accountViewButton, {focus:true}));
      button.addEventListener("keydown", event => {
        const moves = {ArrowRight:1, ArrowLeft:-1, Home:-index, End:buttons.length - 1 - index};
        if (moves[event.key] === undefined) return;
        event.preventDefault();
        const target = (index + moves[event.key] + buttons.length) % buttons.length;
        activateAccountView(buttons[target].dataset.accountViewButton, {focus:true});
      });
    });
    const hashView = location.hash.startsWith("#account-") ? location.hash.slice(9) : "";
    const paymentRedirect = new URLSearchParams(location.search).has("payment");
    activateAccountView(paymentRedirect ? "payments" : (hashView || sessionStorage.getItem(accountViewKey) || "overview"), {updateHash:false});
    window.addEventListener("hashchange", () => {
      const next = location.hash.startsWith("#account-") ? location.hash.slice(9) : "";
      if (next) activateAccountView(next, {updateHash:false});
    });
  };

  setupAccountNavigation();

  const showFormNotice = (id, message, error = false) => {
    const notice = $(id); notice.textContent = message; notice.classList.toggle("error", error); notice.hidden = false;
  };

  const renderOrders = account => {
    const orders = [...(account.manual_orders || []), ...(account.payment_orders || [])]
      .sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    $("ordersList").innerHTML = orders.length ? orders.map(order => {
      const failure = ["failed", "token_failed"].includes(order.status) && (order.failure_message || order.failure_code)
        ? `<br><small>${adminSafe(order.failure_message || t("payment.declined", "Ödeme onaylanmadı."))}${order.failure_code ? ` · ${adminSafe(order.failure_code)}` : ""}</small>`
        : "";
      return `<div class="order-row"><span><strong>${adminSafe(order.order_number || order.reference)}</strong><br><small>${adminSafe(planName(order.plan_code))} · ${adminSafe(new Intl.DateTimeFormat(I18N.locale, {dateStyle:"medium"}).format(new Date(order.created_at)))}</small>${failure}</span><strong>${adminSafe(t(`order.${order.status}`, order.status))}</strong></div>`;
    }).join("") : `<p class="empty-copy">${t("payment.noOrders", "Henüz ödeme siparişin yok.")}</p>`;
    const paidOrders = orders.filter(order => order.status === "paid");
    $("refundOrderSelect").replaceChildren(
      ...(
        paidOrders.length
          ? paidOrders.map(order => new Option(`${order.order_number || order.reference} · ${planName(order.plan_code)}`, order.reference))
          : [new Option(t("refund.noEligibleOrders", "İadeye uygun ödenmiş sipariş yok"), "")]
      ),
    );
    $("refundOrderSelect").disabled = !paidOrders.length;
    $("refundSubmit").disabled = !paidOrders.length;
  };

  const renderJobHistory = jobs => {
    const workspacePath = I18N.localizedPath ? I18N.localizedPath(I18N.language, "/workspace.html") : "/workspace.html";
    $("jobHistory").innerHTML = jobs.length ? jobs.map(job => {
      const created = new Intl.DateTimeFormat(I18N.locale, {dateStyle:"medium", timeStyle:"short"}).format(new Date(Number(job.created || 0) * 1000));
      const label = job.title || (job.options?.job_type === "audio_export" ? t("history.audioExport", "MP3 dönüşümü") : job.options?.job_type === "download_video" ? t("history.videoDownload", "Video indirme") : t("history.studyPack", "Ders çalışma paketi"));
      const status = job.status === "done" ? t("history.ready", "Hazır") : job.status === "error" ? t("history.failed", "Tamamlanamadı") : t("history.processing", "İşleniyor");
      const action = job.status === "error" ? "" : `<a class="secondary-action link-action" href="${adminSafe(workspacePath)}?job=${encodeURIComponent(job.job_id)}">${t("history.open", "Aç")}</a>`;
      return `<div class="order-row history-row"><span><strong>${adminSafe(label)}</strong><br><small>${adminSafe(created)} · ${adminSafe(status)}</small></span>${action}</div>`;
    }).join("") : `<p class="empty-copy">${t("account.noHistory", "Henüz işlenmiş bir dersin yok.")}</p>`;
  };

  const adminSafe = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);

  const loadJobHistory = async () => {
    try {
      const body = await request("/jobs?limit=30", {}, token);
      renderJobHistory(body.jobs || []);
    } catch (error) {
      $("jobHistory").innerHTML = `<p class="empty-copy">${adminSafe(error.message)}</p>`;
    }
  };

  const renderRefundRequests = requests => {
    $("refundRequestsList").innerHTML = requests.length ? requests.map(item => `
      <div class="order-row"><span><strong>${adminSafe(item.order_reference)}</strong><br><small>${adminSafe(new Intl.DateTimeFormat(I18N.locale, {dateStyle:"medium"}).format(new Date(item.created_at)))}</small></span><strong>${adminSafe(t(`refund.status.${item.status}`, item.status))}</strong></div>
    `).join("") : `<p class="empty-copy">${t("refund.noRequests", "Henüz iade talebin yok.")}</p>`;
  };

  const loadRefundRequests = async () => {
    try {
      const body = await request("/billing/me/refund-requests", {}, token);
      renderRefundRequests(body.requests || []);
    } catch (error) {
      $("refundRequestsList").innerHTML = `<p class="empty-copy">${adminSafe(error.message)}</p>`;
    }
  };

  const renderAccount = account => {
    currentAccount = account;
    const user = account.user;
    $("accountName").textContent = user.name || user.email;
    $("accountEmail").textContent = user.email;
    $("accountPlan").textContent = planName(account.plan.code);
    const subscription = account.subscription;
    $("subscriptionEndsRow").hidden = !subscription;
    $("subscriptionActions").hidden = !subscription;
    if (subscription) {
      $("subscriptionEnds").textContent = new Intl.DateTimeFormat(I18N.locale, {dateStyle:"long"}).format(new Date(subscription.ends_at));
      const scheduled = Boolean(subscription.cancel_at_period_end);
      $("subscriptionState").textContent = scheduled
        ? t("account.cancellationScheduled", "Yenileme durduruldu; ücretli hakların dönem sonuna kadar açık.")
        : t("account.renewsUntilCancelled", "Ücretli hakların dönem boyunca açık. Yenilemeyi dilediğinde durdurabilirsin.");
      $("cancelSubscriptionButton").hidden = scheduled;
    }
    $("accountAdMode").textContent = account.plan.entitlements?.ad_free
      ? t("plans.adFree", "Reklamsız kullanım")
      : t("plans.rewardedOption", "İsteğe bağlı reklamla ek dakika");
    $("remainingMinutes").textContent = account.remaining_minutes == null ? t("account.unlimited", "Sınırsız") : account.remaining_minutes.toLocaleString(I18N.locale);
    $("creditMinutes").textContent = `${(account.credit_minutes || 0).toLocaleString(I18N.locale)} ${t("unit.minuteShort", "dk")}`;
    $("usedMinutes").textContent = t("account.usedMinutes", "{count} dk kullanıldı").replace("{count}", account.used_minutes.toLocaleString(I18N.locale));
    const total = account.plan.minutes || 0;
    const percentage = total ? Math.min(100, Math.round(account.used_minutes / total * 100)) : 0;
    $("usageTrack").style.setProperty("--usage", `${percentage}%`);
    $("accountCountry").textContent = user.country_code || "—";
    $("accountPhone").textContent = user.phone || t("profile.notAdded", "Eklenmedi");
    $("profileFirstName").value = user.first_name || "";
    $("profileLastName").value = user.last_name || "";
    $("profilePhone").value = user.phone || "";
    populateCountrySelect($("accountCountrySelect"), user.country_code || "TR");
    $("preferredLanguage").replaceChildren(...Object.entries(I18N.languages).map(([code, label]) => new Option(label, code)));
    $("preferredLanguage").value = user.preferred_language || I18N.language;
    let adminLink = $("accountAdminLink");
    if (account.is_admin && !adminLink) {
      adminLink = document.createElement("a");
      adminLink.id = "accountAdminLink";
      adminLink.className = "secondary-action link-action";
      adminLink.href = "/admin.html";
      adminLink.textContent = t("admin.open", "Yönetici panelini aç");
      $("logoutButton").insertAdjacentElement("beforebegin", adminLink);
    } else if (!account.is_admin && adminLink) {
      adminLink.remove();
    }
    renderOrders(account);
    $("accountPage").hidden = false;
    void loadJobHistory();
    void loadRefundRequests();
  };

  const reconcilePaymentRedirect = async () => {
    const params = new URLSearchParams(location.search);
    const reference = params.get("order");
    const result = params.get("payment");
    if (!reference || !result) return;
    if (result === "failed") {
      try {
        const body = await request("/billing/me", {}, token);
        renderAccount(body.account);
        const order = (body.account.payment_orders || []).find(item => item.reference === reference);
        const reason = order?.failure_message || t("payment.declined", "Ödeme banka veya iyzico tarafından onaylanmadı.");
        const code = order?.failure_code ? ` (${order.failure_code})` : "";
        showFormNotice("paymentResultNotice", `${reason}${code}`, true);
      } catch {
        showFormNotice("paymentResultNotice", t("order.failed", "Ödeme başarısız"), true);
      }
      return;
    }
    showFormNotice("paymentResultNotice", t("payment.verifying", "Ödeme sonucu güvenli bildirimle doğrulanıyor…"));
    for (let attempt = 0; attempt < 6; attempt += 1) {
      try {
        const body = await request("/billing/me", {}, token);
        renderAccount(body.account);
        const order = (body.account.payment_orders || []).find(item => item.reference === reference);
        if (order?.status === "paid") {
          showFormNotice("paymentResultNotice", t("payment.confirmed", "Ödeme doğrulandı; plan veya kredilerin hesabına eklendi."));
          const conversionKey = `lecturesift-purchase-${reference}`;
          if (!sessionStorage.getItem(conversionKey)) {
            const purchase = {
              transaction_id: reference,
              value: Number(order.amount_minor || 0) / 100,
              currency: order.currency || "TRY",
              items: [{item_id: order.plan_code, item_name: planName(order.plan_code), quantity: 1}],
            };
            sessionStorage.setItem(conversionKey, "1");
            recordAnalytics("event", "purchase", purchase);
            recordAnalytics("conversion", "purchase", purchase);
          }
          return;
        }
        if (["failed", "token_failed"].includes(order?.status)) {
          showFormNotice("paymentResultNotice", t(`order.${order.status}`, order.status), true);
          return;
        }
      } catch {}
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
    showFormNotice("paymentResultNotice", t("payment.stillPending", "Ödeme bildirimi henüz gelmedi. Sipariş numaranla birkaç dakika sonra tekrar kontrol edebilirsin."));
  };

  try {
    const body = await request("/billing/me", {}, token);
    renderAccount(body.account);
  } catch {
    localStorage.removeItem(TOKEN_KEY);
    location.replace("/login.html?next=/account.html");
  }
  void reconcilePaymentRedirect();
  $("preferencesForm").addEventListener("submit", async event => {
    event.preventDefault();
    const button = $("preferencesSubmit");
    setBusy(button, true, t("state.saving", "Kaydediliyor…"));
    try {
      const body = await request("/billing/me/preferences", {
        method:"PATCH",
        body:JSON.stringify({country_code:$("accountCountrySelect").value, preferred_language:$("preferredLanguage").value}),
      }, token);
      localStorage.setItem("lecturesift-ui", body.account.user.preferred_language);
      localStorage.setItem("lecturesift-country", body.account.user.country_code);
      const suggestedCurrency = LOCALE_DATA.currencyForCountry[body.account.user.country_code];
      if (suggestedCurrency) localStorage.setItem("lecturesift-currency", suggestedCurrency);
      renderAccount(body.account);
      showFormNotice("preferencesNotice", body.message);
      if (I18N.language !== body.account.user.preferred_language) {
        location.assign(I18N.localizedPath(body.account.user.preferred_language));
      }
    } catch (error) {
      showFormNotice("preferencesNotice", error.message, true);
    } finally { setBusy(button, false, t("preferences.save", "Tercihleri kaydet")); }
  });
  $("profileForm").addEventListener("submit", async event => {
    event.preventDefault();
    const button = $("profileSubmit"); setBusy(button, true, t("state.saving", "Kaydediliyor…"));
    try {
      const body = await request("/billing/me/profile", {method:"PATCH", body:JSON.stringify({first_name:$("profileFirstName").value.trim(), last_name:$("profileLastName").value.trim(), phone:$("profilePhone").value.trim()})}, token);
      renderAccount(body.account); showFormNotice("profileNotice", body.message);
    } catch (error) { showFormNotice("profileNotice", error.message, true); }
    finally { setBusy(button, false, t("profile.save", "Profili kaydet")); }
  });
  $("passwordForm").addEventListener("submit", async event => {
    event.preventDefault();
    const next = $("newPassword").value;
    if (next !== $("newPasswordConfirm").value) return showFormNotice("passwordNotice", t("auth.passwordMismatch", "Parolalar birbiriyle eşleşmiyor."), true);
    const button = $("passwordSubmit"); setBusy(button, true, t("state.saving", "Kaydediliyor…"));
    try {
      const body = await request("/billing/me/change-password", {method:"POST", body:JSON.stringify({current_password:$("currentPassword").value, new_password:next})}, token);
      token = body.token; localStorage.setItem(TOKEN_KEY, token); renderAccount(body.account); $("passwordForm").reset(); showFormNotice("passwordNotice", body.message);
    } catch (error) { showFormNotice("passwordNotice", error.message, true); }
    finally { setBusy(button, false, t("security.changePassword", "Parolayı değiştir")); }
  });
  $("cancelSubscriptionButton").addEventListener("click", async () => {
    if (!confirm(t("account.cancelConfirm", "Abonelik yenilemesini durdurmak istediğine emin misin? Mevcut dönem hakların korunacak."))) return;
    const button = $("cancelSubscriptionButton");
    setBusy(button, true, t("state.saving", "Kaydediliyor…"));
    try {
      const body = await request("/billing/me/subscription/cancel", {method:"POST"}, token);
      renderAccount(body.account);
      showFormNotice("subscriptionNotice", body.message);
    } catch (error) {
      showFormNotice("subscriptionNotice", error.message, true);
      setBusy(button, false, t("account.cancelSubscription", "Abonelik yenilemesini durdur"));
    }
  });
  $("refundRequestForm").addEventListener("submit", async event => {
    event.preventDefault();
    const button = $("refundSubmit");
    setBusy(button, true, t("state.saving", "Kaydediliyor…"));
    try {
      const body = await request("/billing/me/refund-requests", {
        method:"POST",
        body:JSON.stringify({
          order_reference:$("refundOrderSelect").value,
          reason:$("refundReason").value.trim(),
        }),
      }, token);
      $("refundReason").value = "";
      showFormNotice("refundNotice", body.message);
      await loadRefundRequests();
    } catch (error) {
      showFormNotice("refundNotice", error.message, true);
    } finally {
      setBusy(button, false, t("refund.submit", "Talep oluştur"));
    }
  });
  $("exportDataButton").addEventListener("click", async () => {
    const button = $("exportDataButton");
    setBusy(button, true, t("account.exporting", "Veriler hazırlanıyor…"));
    try {
      const body = await request("/billing/me/export", {}, token);
      const blob = new Blob([JSON.stringify(body.export, null, 2)], {type:"application/json"});
      const href = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href;
      link.download = `lecturesift-account-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      URL.revokeObjectURL(href);
      showFormNotice("accountDataNotice", t("account.exportReady", "Veri dosyan indirildi."));
    } catch (error) { showFormNotice("accountDataNotice", error.message, true); }
    finally { setBusy(button, false, t("account.exportData", "Verilerimi indir")); }
  });
  $("closeAccountForm").addEventListener("submit", async event => {
    event.preventDefault();
    if (!confirm(t("account.closeConfirm", "Hesabını ve ders dosyalarını kalıcı olarak kapatmak istediğine emin misin?"))) return;
    const button = $("closeAccountButton");
    setBusy(button, true, t("account.closing", "Hesap kapatılıyor…"));
    try {
      await request("/billing/me/close-account", {
        method:"POST",
        body:JSON.stringify({
          email_confirmation:$("closeAccountEmail").value.trim(),
          current_password:$("closeAccountPassword").value,
        }),
      }, token);
      localStorage.removeItem(TOKEN_KEY);
      location.replace("/?account=closed");
    } catch (error) {
      showFormNotice("accountDataNotice", error.message, true);
      setBusy(button, false, t("account.closeButton", "Hesabımı kalıcı olarak kapat"));
    }
  });
  $("logoutButton").addEventListener("click", async () => {
    try { await request("/billing/logout", {method:"POST"}, token); } catch {}
    localStorage.removeItem(TOKEN_KEY);
    location.replace("/login.html");
  });
}

({register:initRegister, login:initLogin, verify:initVerify, forgot:initForgot, reset:initReset, account:initAccount})[page]?.();
