const API = "https://api.lecturesift.com";
const TOKEN_KEY = "lecturesift-billing-token";
const LOCALE_DATA = window.LECTURESIFT_LOCALE_DATA || {countries: [], currencies: [], currencyForCountry: {}};
const I18N = window.LectureSiftI18n || {language:"tr", locale:"tr-TR", languages:{tr:"Türkçe"}, t:(key, fallback)=>fallback || key};
const REFERRAL_I18N = window.LectureSiftReferralI18n || {t:(_key, fallback)=>fallback || "", format:(_key, _values, fallback)=>fallback || ""};
const page = document.body.dataset.page || "login";
const $ = id => document.getElementById(id);
const t = (key, fallback) => I18N.t(key, fallback);
const rt = (key, fallback) => REFERRAL_I18N.t(key, fallback);
const rf = (key, values, fallback) => REFERRAL_I18N.format(key, values, fallback);
const referralCode = value => {
  const normalized = String(value || "").trim().toUpperCase();
  return /^LSR-[A-F0-9]{24}$/.test(normalized) ? normalized : "";
};
const referralCouponCode = value => {
  const normalized = String(value || "").trim().toUpperCase();
  return /^LSC-[A-F0-9]{24}$/.test(normalized) ? normalized : "";
};

function validatedReferralSummary(body) {
  const value = body?.ok === true && body.referrals && typeof body.referrals === "object" ? body.referrals : null;
  if (!value || typeof value.enabled !== "boolean") return null;
  if (!value.enabled) return {enabled:false};
  const integers = [
    value.reward_minutes, value.invitee_reward_minutes, value.monthly_invitation_cap,
    value.monthly_reserved_count, value.monthly_remaining_count, value.earned_minutes,
    value.pending_minutes, value.hold_days,
  ];
  if (
    integers.some(number => !Number.isInteger(number) || number < 0)
    || value.reward_minutes !== 60 || value.invitee_reward_minutes !== 30
    || value.monthly_invitation_cap !== 5 || value.hold_days !== 14
    || value.monthly_reserved_count > value.monthly_invitation_cap
    || value.monthly_remaining_count !== value.monthly_invitation_cap - value.monthly_reserved_count
    || value.history_limit !== 50 || typeof value.has_more_rewards !== "boolean" || typeof value.has_more_coupons !== "boolean"
    || value.settlement !== "admin_reconciliation_after_14_days"
    || !/^\d{4}-(0[1-9]|1[0-2])$/.test(value.month_utc || "")
  ) return null;
  const coupon = value.coupon;
  if (
    !coupon || coupon.percent !== 10 || coupon.max_discount_minor !== 5000
    || coupon.currency !== "TRY" || coupon.valid_days !== 90 || coupon.monthly_only !== true
  ) return null;
  const renewal = value.renewal;
  if (
    !renewal || renewal.reward_minutes !== 30 || renewal.invitee_reward_minutes !== 0
    || renewal.monthly_per_invitee_cap !== 1 || renewal.coupon?.percent !== 5
    || renewal.coupon.max_discount_minor !== 2500 || renewal.coupon.currency !== "TRY"
    || renewal.coupon.valid_days !== 90 || renewal.coupon.monthly_only !== true
  ) return null;
  const policies = value.coupon_policies;
  const policyKinds = {"referral-2026-09-v1":"first_purchase", "referral-2026-09-v2":"first_purchase", "referral-2026-09-renewal-v1":"renewal"};
  if (!policies || typeof policies !== "object" || Object.keys(policies).length !== Object.keys(policyKinds).length
    || Object.keys(policies).some(version => !Object.hasOwn(policyKinds, version))) return null;
  if (!Array.isArray(value.redemption_currencies)
    || new Set(value.redemption_currencies).size !== value.redemption_currencies.length
    || value.redemption_currencies.some(currency => !LOCALE_DATA.currencies?.includes(currency))) return null;
  for (const [version, kind] of Object.entries(policyKinds)) {
    const terms = policies[version];
    if (!terms || typeof terms !== "object" || !terms.TRY) return null;
    if (Object.entries(terms).some(([currency, item]) =>
      !LOCALE_DATA.currencies?.includes(currency) || !item || item.currency !== currency
      || item.percent !== (kind === "renewal" ? 5 : 10)
      || !Number.isSafeInteger(item.max_discount_minor) || item.max_discount_minor <= 0
      || item.valid_days !== 90 || item.monthly_only !== true
      || (version === "referral-2026-09-v1" && currency !== "TRY")
    )) return null;
  }
  const code = value.referral_code === null ? null : referralCode(value.referral_code);
  if (value.referral_code !== null && !code) return null;
  if (code) {
    if (value.referral_url !== `https://lecturesift.com/register.html?ref=${code}`) return null;
  } else if (value.referral_url !== null) return null;
  if (!Array.isArray(value.rewards) || !Array.isArray(value.coupons) || value.rewards.length > 500 || value.coupons.length > 500) return null;
  const rewardIds = new Set();
  const rewardsValid = value.rewards.every(reward => {
    if (!reward || typeof reward !== "object" || rewardIds.has(reward.id)) return false;
    rewardIds.add(reward.id);
    const isRenewal = reward.kind === "renewal";
    if (!isRenewal && reward.kind !== "first_purchase") return false;
    const terms = policies[reward.policy_version]?.[reward.coupon_currency];
    if (!terms || policyKinds[reward.policy_version] !== reward.kind
      || typeof reward.coupon_currency_selected !== "boolean") return false;
    const validId = isRenewal ? /^rr-[0-9a-f]{32}$/i.test(reward.id || "")
      : /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(reward.id || "");
    return validId
      && reward.inviter_minutes === (isRenewal ? 30 : 60)
      && reward.invitee_minutes === (isRenewal ? 0 : 30)
      && reward.coupon_percent === (isRenewal ? 5 : 10)
      && reward.coupon_max_discount_minor === terms.max_discount_minor
      && ["inviter", "invitee"].includes(reward.role)
      && ["invited", "pending", "released", "blocked", "cap_reached", "monthly_limit"].includes(reward.status)
      && [null, "minutes", "coupon"].includes(reward.reward_choice)
      && !Number.isNaN(Date.parse(reward.created_at))
      && (reward.pending_until === null || !Number.isNaN(Date.parse(reward.pending_until)));
  });
  const couponCodes = new Set();
  const couponsValid = value.coupons.every(item => {
    const selected = referralCouponCode(item?.code);
    if (!selected || couponCodes.has(selected)) return false;
    couponCodes.add(selected);
    return ["ready", "reserved", "used", "void", "expired"].includes(item.status)
      && Object.values(policies).some(terms => terms[item.currency]?.percent === item.percent
        && terms[item.currency]?.max_discount_minor === item.max_discount_minor)
      && !Number.isNaN(Date.parse(item.expires_at));
  });
  return rewardsValid && couponsValid ? value : null;
}

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
  const referralInput = $("referralCode");
  const queryReferral = new URLSearchParams(location.search).get("ref");
  if (queryReferral !== null) {
    const normalized = referralCode(queryReferral);
    if (normalized) {
      referralInput.value = normalized;
      $("referralCodeNotice").textContent = rt("registerDetected", "Bağlantıdaki davet kodu forma eklendi.");
      $("referralCodeNotice").hidden = false;
    } else if (queryReferral.trim()) {
      $("referralCodeNotice").textContent = rt("registerInvalid", "Bağlantıdaki davet kodu geçerli biçimde değil; istersen doğru kodu elle yazabilirsin.");
      $("referralCodeNotice").classList.add("error");
      $("referralCodeNotice").hidden = false;
    }
  }
  $("registerForm").addEventListener("submit", async event => {
    event.preventDefault();
    const button = $("registerSubmit");
    const password = $("password").value;
    const rawReferral = referralInput.value.trim();
    const normalizedReferral = referralCode(rawReferral);
    if (password !== $("passwordConfirm").value) return showNotice(t("auth.passwordMismatch", "Parolalar birbiriyle eşleşmiyor."), true);
    if (!$("terms").checked) return showNotice(t("auth.acceptRequired", "Devam etmek için kullanım ve gizlilik koşullarını kabul et."), true);
    if (rawReferral && !normalizedReferral) return showNotice(rt("registerInvalid", "Davet kodu geçerli biçimde değil."), true);
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
          ...(normalizedReferral ? {referral_code: normalizedReferral} : {}),
        }),
      });
      $("registerForm").hidden = true;
      $("successBox").hidden = false;
      $("successEmail").textContent = body.user.email;
      $("enterCodeLink").href = `/verify.html?email=${encodeURIComponent(body.user.email)}`;
      const referralResult = $("registerReferralStatus");
      referralResult.hidden = !normalizedReferral;
      referralResult.textContent = "";
      if (normalizedReferral) {
        const statusKey = body.referral_status === "accepted" ? "registerAccepted"
          : body.referral_status === "disabled" ? "registerDisabled"
          : body.referral_status === "invalid" ? "registerRejected" : "registerUnconfirmed";
        referralResult.textContent = rt(statusKey);
      }
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
  let referralLoadStarted = false;
  const accountViews = ["overview", "profile", "payments", "lessons", "referrals", "security"];
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
        const direction = document.documentElement.dir === "rtl" ? -1 : 1;
        const moves = {ArrowRight:direction, ArrowLeft:-direction, ArrowDown:1, ArrowUp:-1, Home:-index, End:buttons.length - 1 - index};
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

  const paymentMethodLabel = order => {
    if (order.payment_method === "bank_transfer") {
      return order.provider === "iyzico"
        ? t("payment.method.iyzicoProtected", "iyzico Korumalı Havale/EFT")
        : t("payment.method.manualIban", "Kişisel IBAN'a manuel havale/EFT");
    }
    if (order.payment_method === "unknown") {
      return t("payment.method.legacy", "iyzico ödeme yöntemi (eski kayıt)");
    }
    if (order.provider === "iyzico") return t("payment.method.iyzicoCard", "iyzico kart");
    if (order.provider === "paytr") return t("payment.method.paytrCard", "PayTR kart");
    return t("payment.method.legacy", "Ödeme yöntemi");
  };

  const paymentMoney = order => {
    const amountMinor = Number(order.amount_minor);
    if (!Number.isFinite(amountMinor)) return "—";
    try {
      return new Intl.NumberFormat(I18N.locale, {style:"currency", currency:String(order.currency || "TRY").toUpperCase()}).format(amountMinor / 100);
    } catch (_) {
      return `${(amountMinor / 100).toLocaleString(I18N.locale)} ${String(order.currency || "TRY").toUpperCase()}`;
    }
  };

  const paymentDateTime = value => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat(I18N.locale, {dateStyle:"medium", timeStyle:"short"}).format(date);
  };

  const renderOrders = account => {
    const orders = [...(account.manual_orders || []), ...(account.payment_orders || [])]
      .sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    $("ordersList").innerHTML = orders.length ? orders.map(order => {
      const method = paymentMethodLabel(order);
      const failure = ["failed", "token_failed"].includes(order.status) && (order.failure_message || order.failure_code)
        ? `<p class="payment-order-failure">${adminSafe(order.failure_message || t("payment.declined", "Ödeme onaylanmadı."))}${order.failure_code ? ` · ${adminSafe(order.failure_code)}` : ""}</p>`
        : "";
      const pendingMethod = order.payment_method_confirmed === false
        ? `<span class="payment-method-pending">${adminSafe(t("payment.method.pending", "Yöntem doğrulaması bekliyor"))}</span>`
        : "";
      return `<article class="order-row payment-order-row">
        <div class="payment-order-heading"><strong>${adminSafe(order.order_number || order.reference || "—")}</strong><small>${adminSafe(planName(order.plan_code))}</small></div>
        <span class="payment-order-status">${adminSafe(t(`order.${order.status}`, order.status || "—"))}</span>
        <dl class="payment-order-meta">
          <div><dt>${adminSafe(t("payment.method", "Ödeme yöntemi"))}</dt><dd>${adminSafe(method)}${pendingMethod}</dd></div>
          <div><dt>${adminSafe(t("payment.amount", "Tutar"))}</dt><dd>${adminSafe(paymentMoney(order))}</dd></div>
          <div><dt>${adminSafe(t("payment.createdAt", "Sipariş zamanı"))}</dt><dd>${adminSafe(paymentDateTime(order.created_at))}</dd></div>
          <div><dt>${adminSafe(t("payment.status", "Durum"))}</dt><dd>${adminSafe(t(`order.${order.status}`, order.status || "—"))}</dd></div>
        </dl>${failure}
      </article>`;
    }).join("") : `<p class="empty-copy">${t("payment.noOrders", "Henüz ödeme siparişin yok.")}</p>`;
    const paidOrders = orders.filter(order => order.status === "paid");
    $("refundOrderSelect").replaceChildren(
      ...(
        paidOrders.length
          ? paidOrders.map(order => new Option(`${order.order_number || order.reference} · ${planName(order.plan_code)} · ${paymentMethodLabel(order)} · ${paymentMoney(order)}`, order.reference))
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

  const referralDate = value => new Intl.DateTimeFormat(I18N.locale, {dateStyle:"medium"}).format(new Date(value));
  let currentReferralSummary = null;
  const referralCouponValue = (percent, maxMinor, currency) => rf("couponValue", {
    percent:new Intl.NumberFormat(I18N.locale, {style:"percent"}).format(percent / 100),
    amount:new Intl.NumberFormat(I18N.locale, {style:"currency", currency}).format(maxMinor / (["JPY", "KRW"].includes(currency) ? 1 : 100)),
  }, "Kupon {percent}, en fazla {amount}");

  const showReferralNotice = (message, error = false) => {
    const notice = $("referralNotice");
    notice.textContent = message;
    notice.classList.toggle("error", error);
    notice.hidden = false;
  };

  const disableReferralUi = message => {
    currentReferralSummary = null;
    $("referralLoading").hidden = true;
    $("referralContent").hidden = true;
    $("referralUnavailable").textContent = message;
    $("referralUnavailable").hidden = false;
  };

  const renderReferralSummary = summary => {
    if (!summary?.enabled) {
      disableReferralUi(rt("disabled", "Davet programı bu ortamda henüz etkin değil."));
      return;
    }
    currentReferralSummary = summary;
    $("referralLoading").hidden = true;
    $("referralUnavailable").hidden = true;
    $("referralContent").hidden = false;
    $("referralNoCode").hidden = Boolean(summary.referral_code);
    $("referralLinkWrap").hidden = !summary.referral_code;
    $("referralLink").value = summary.referral_url || "";
    $("referralReserved").textContent = `${summary.monthly_reserved_count.toLocaleString(I18N.locale)} / ${summary.monthly_invitation_cap.toLocaleString(I18N.locale)}`;
    $("referralRemaining").textContent = summary.monthly_remaining_count.toLocaleString(I18N.locale);
    $("referralEarned").textContent = `${summary.earned_minutes.toLocaleString(I18N.locale)} ${t("unit.minuteShort", "dk")}`;
    $("referralPending").textContent = `${summary.pending_minutes.toLocaleString(I18N.locale)} ${t("unit.minuteShort", "dk")}`;
    $("referralHistoryNotice").hidden = !summary.has_more_rewards && !summary.has_more_coupons;

    const rewardKind = reward => reward.kind === "renewal"
      ? rt("renewal", "Abonelik yenilemesi") : rt("firstPurchase", "İlk abonelik alışverişi");
    const selectedCurrency = reward => {
      const saved = localStorage.getItem("lecturesift-currency");
      const preferred = saved || LOCALE_DATA.currencyForCountry[currentAccount?.user?.country_code] || "TRY";
      return reward.status === "pending" && !reward.coupon_currency_selected && summary.coupon_policies[reward.policy_version][preferred]
        ? preferred : reward.coupon_currency;
    };

    const choices = summary.rewards.filter(reward => reward.role === "inviter" && reward.status === "pending");
    $("referralRewards").innerHTML = choices.length ? choices.map(reward => {
      const pendingDate = reward.pending_until ? referralDate(reward.pending_until) : "—";
      const currency = selectedCurrency(reward);
      const terms = summary.coupon_policies[reward.policy_version][currency];
      const couponAvailable = summary.redemption_currencies.includes(currency);
      const options = Object.keys(summary.coupon_policies[reward.policy_version]).map(code => `<option value="${adminSafe(code)}"${code === currency ? " selected" : ""}>${adminSafe(code)}</option>`).join("");
      return `<article class="referral-reward" data-referral-reward="${adminSafe(reward.id)}"><div class="referral-reward-copy"><strong>${adminSafe(rewardKind(reward))}</strong><small>${adminSafe(pendingDate)}</small><small data-coupon-preview>${adminSafe(referralCouponValue(terms.percent, terms.max_discount_minor, currency))}</small><small data-coupon-unavailable${couponAvailable ? " hidden" : ""}>${adminSafe(rt("couponUnavailable", "Bu para biriminde kupon kullanımı şu anda desteklenmiyor. Dakika seçebilir veya desteklenen bir para birimi seçebilirsin."))}</small></div><div class="referral-choice-actions"><label class="field"><span>${adminSafe(rt("couponCurrency", "Kupon para birimi"))}</span><select data-referral-currency>${options}</select></label><button class="secondary-action${reward.reward_choice === "minutes" ? " selected" : ""}" type="button" data-referral-choice="minutes" aria-pressed="${String(reward.reward_choice === "minutes")}">${adminSafe(rf("minutesChoiceDynamic", {minutes:reward.inviter_minutes}, "{minutes} dakika seç"))}</button><button class="secondary-action${reward.reward_choice === "coupon" ? " selected" : ""}" type="button" data-referral-choice="coupon" aria-pressed="${String(reward.reward_choice === "coupon")}"${couponAvailable ? "" : " disabled"}>${adminSafe(rf("couponChoiceDynamic", {percent:reward.coupon_percent}, "%{percent} kupon seç"))}</button></div></article>`;
    }).join("") : `<p class="empty-copy">${adminSafe(rt("noChoices", "Şu anda seçim bekleyen davet ödülün yok."))}</p>`;

    const statuses = {invited:"statusInvited", pending:"statusPending", released:"statusReleased", blocked:"statusBlocked", cap_reached:"statusCapReached", monthly_limit:"statusMonthlyLimit"};
    const history = summary.rewards.filter(reward => reward.role === "inviter" || reward.kind === "first_purchase");
    $("referralHistory").innerHTML = history.length ? history.map(reward => {
      const benefit = !["pending", "released"].includes(reward.status) ? "—"
        : reward.role === "inviter" && reward.reward_choice === "coupon"
        ? referralCouponValue(reward.coupon_percent, reward.coupon_max_discount_minor, reward.coupon_currency)
        : `${(reward.role === "inviter" ? reward.inviter_minutes : reward.invitee_minutes).toLocaleString(I18N.locale)} ${t("unit.minuteShort", "dk")}`;
      return `<article class="referral-reward"><div class="referral-reward-copy"><strong>${adminSafe(rewardKind(reward))}</strong><small>${adminSafe(referralDate(reward.created_at))}</small></div><div class="referral-reward-copy"><strong>${adminSafe(rt(statuses[reward.status], reward.status))}</strong><small>${adminSafe(benefit)}</small></div></article>`;
    }).join("") : `<p class="empty-copy">${adminSafe(rt("historyEmpty", "Henüz davet ödülü kaydın yok."))}</p>`;

    const readyCoupons = summary.coupons.filter(coupon => coupon.status === "ready");
    $("referralCoupons").innerHTML = readyCoupons.length ? readyCoupons.map(coupon => {
      const expires = rf("couponExpires", {date:referralDate(coupon.expires_at)}, "Son kullanım: {date}");
      return `<article class="referral-coupon"><strong>${adminSafe(referralCouponValue(coupon.percent, coupon.max_discount_minor, coupon.currency))}</strong><div class="referral-code-row"><input value="${adminSafe(coupon.code)}" readonly aria-label="${adminSafe(rt("couponsTitle", "Hazır kuponların"))}"><button class="secondary-action" type="button" data-copy-referral-coupon="${adminSafe(coupon.code)}">${adminSafe(rt("copy", "Kopyala"))}</button></div><small>${adminSafe(expires)}</small></article>`;
    }).join("") : `<p class="empty-copy">${adminSafe(rt("noCoupons", "Şu anda kullanıma hazır kuponun yok."))}</p>`;
  };

  const referralSummaryFromResponse = body => {
    const summary = validatedReferralSummary(body);
    if (!summary) throw new Error(rt("unavailable", "Davet bilgileri şu anda doğrulanamıyor."));
    return summary;
  };

  const loadReferralSummary = async () => {
    try {
      renderReferralSummary(referralSummaryFromResponse(await request("/billing/referrals", {}, token)));
    } catch {
      disableReferralUi(rt("unavailable", "Davet bilgileri şu anda doğrulanamıyor. Herhangi bir ödül kazanılmış veya beklemede olarak gösterilmiyor."));
    }
  };

  const copyReferralText = async (value, fallbackInput = null) => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(value);
      showReferralNotice(rt("copied", "Bağlantı kopyalandı."));
    } catch {
      fallbackInput?.focus();
      fallbackInput?.select();
      showReferralNotice(rt("copyFailed", "Bağlantı kopyalanamadı; metni elle seçebilirsin."), true);
    }
  };

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
    if (!referralLoadStarted) {
      referralLoadStarted = true;
      void loadReferralSummary();
    }
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
  $("createReferralCodeButton").addEventListener("click", async () => {
    const button = $("createReferralCodeButton");
    setBusy(button, true, rt("creating", "Bağlantı oluşturuluyor…"));
    try {
      renderReferralSummary(referralSummaryFromResponse(await request("/billing/referrals/code", {method:"POST"}, token)));
    } catch {
      showReferralNotice(rt("unavailable", "Davet bilgileri şu anda doğrulanamıyor."), true);
    } finally {
      setBusy(button, false, rt("create", "Davet bağlantısı oluştur"));
    }
  });
  $("copyReferralLinkButton").addEventListener("click", () => {
    const input = $("referralLink");
    if (input.value) void copyReferralText(input.value, input);
  });
  $("referralRewards").addEventListener("change", event => {
    const selector = event.target?.closest?.("[data-referral-currency]");
    const card = selector?.closest?.("[data-referral-reward]");
    if (!selector || !card || !$("referralRewards").contains(card)) return;
    const reward = currentReferralSummary?.rewards.find(item => item.id === card.dataset.referralReward);
    const terms = reward && currentReferralSummary.coupon_policies[reward.policy_version]?.[selector.value];
    if (terms) {
      card.querySelector("[data-coupon-preview]").textContent = referralCouponValue(terms.percent, terms.max_discount_minor, terms.currency);
      const available = currentReferralSummary.redemption_currencies.includes(selector.value);
      card.querySelector('[data-referral-choice="coupon"]').disabled = !available;
      card.querySelector("[data-coupon-unavailable]").hidden = available;
    }
  });
  $("referralRewards").addEventListener("click", async event => {
    const button = event.target?.closest?.("[data-referral-choice]");
    const reward = button?.closest?.("[data-referral-reward]");
    if (!button || button.disabled || !reward || !$("referralRewards").contains(button)) return;
    const choice = button.dataset.referralChoice;
    if (!["minutes", "coupon"].includes(choice)) return;
    const buttons = [...reward.querySelectorAll("[data-referral-choice]")];
    const original = button.textContent;
    buttons.forEach(item => { item.disabled = true; });
    button.textContent = rt("savingChoice", "Seçim kaydediliyor…");
    try {
      const body = await request(`/billing/referrals/rewards/${encodeURIComponent(reward.dataset.referralReward)}/choice`, {
        method:"POST", body:JSON.stringify({reward_choice:choice, currency:reward.querySelector("[data-referral-currency]")?.value}),
      }, token);
      renderReferralSummary(referralSummaryFromResponse(body));
    } catch (error) {
      showReferralNotice(error.message || rt("unavailable", "Davet bilgileri şu anda doğrulanamıyor."), true);
      buttons.forEach(item => { item.disabled = false; });
      button.textContent = original;
    }
  });
  $("referralCoupons").addEventListener("click", event => {
    const button = event.target?.closest?.("[data-copy-referral-coupon]");
    if (!button || !$("referralCoupons").contains(button)) return;
    const code = referralCouponCode(button.dataset.copyReferralCoupon);
    if (code) void copyReferralText(code, button.closest(".referral-code-row")?.querySelector("input"));
  });
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
