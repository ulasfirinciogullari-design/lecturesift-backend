const API = "https://lecturesift-backend.onrender.com";
const TOKEN_KEY = "lecturesift-billing-token";
const LOCALE_DATA = window.LECTURESIFT_LOCALE_DATA || {countries: [], currencies: [], currencyForCountry: {}};
const I18N = window.LectureSiftI18n || {language:"tr", locale:"tr-TR", languages:{tr:"Türkçe"}, t:(key, fallback)=>fallback || key};
const page = document.body.dataset.page || "login";
const $ = id => document.getElementById(id);
const t = (key, fallback) => I18N.t(key, fallback);

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

  const showFormNotice = (id, message, error = false) => {
    const notice = $(id); notice.textContent = message; notice.classList.toggle("error", error); notice.hidden = false;
  };

  const renderOrders = account => {
    $("ordersList").innerHTML = account.manual_orders.length ? account.manual_orders.map(order => `
      <div class="order-row"><span><strong>${order.order_number || order.reference}</strong><br><small>${planName(order.plan_code)} · ${new Intl.DateTimeFormat(I18N.locale, {dateStyle:"medium"}).format(new Date(order.created_at))}</small></span><strong>${order.status === "paid" ? t("order.paid", "Aktif") : t("order.pending", "Kontrol bekliyor")}</strong></div>`).join("") : `<p class="empty-copy">${t("payment.noOrders", "Henüz ödeme siparişin yok.")}</p>`;
  };

  const renderAccount = account => {
    currentAccount = account;
    const user = account.user;
    $("accountName").textContent = user.name || user.email;
    $("accountEmail").textContent = user.email;
    $("accountPlan").textContent = planName(account.plan.code);
    $("remainingMinutes").textContent = account.remaining_minutes == null ? t("account.unlimited", "Sınırsız") : account.remaining_minutes.toLocaleString(I18N.locale);
    $("creditMinutes").textContent = `${(account.credit_minutes || 0).toLocaleString(I18N.locale)} dk`;
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
    renderOrders(account);
    $("accountPage").hidden = false;
  };

  try {
    const body = await request("/billing/me", {}, token);
    renderAccount(body.account);
    const transfer = await request("/billing/manual-transfer");
    if (transfer.available && transfer.bank) {
      $("accountBankIban").textContent = transfer.bank.iban.replace(/(.{4})/g, "$1 ").trim();
      $("accountBankHolder").textContent = transfer.bank.account_holder;
      $("accountBankName").textContent = transfer.bank.bank_name || "—";
      $("bankDetails").hidden = false;
    }
  } catch {
    localStorage.removeItem(TOKEN_KEY);
    location.replace("/login.html?next=/account.html");
  }
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
      if (I18N.language !== body.account.user.preferred_language) location.reload();
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
  $("logoutButton").addEventListener("click", async () => {
    try { await request("/billing/logout", {method:"POST"}, token); } catch {}
    localStorage.removeItem(TOKEN_KEY);
    location.replace("/login.html");
  });
}

({register:initRegister, login:initLogin, verify:initVerify, forgot:initForgot, reset:initReset, account:initAccount})[page]?.();
