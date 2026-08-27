const API = "https://lecturesift-backend.onrender.com";
const TOKEN_KEY = "lecturesift-billing-token";
const page = document.body.dataset.page || "login";
const $ = id => document.getElementById(id);

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
  if (!response.ok) throw new Error(errorMessage(body, "İstek tamamlanamadı."));
  return body;
}

function setBusy(button, busy, label) {
  if (!button) return;
  if (busy) button.dataset.label = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? label : (button.dataset.label || button.textContent);
}

function selectedCountry() {
  const localeCountry = (navigator.language.split("-")[1] || "").toUpperCase();
  return localeCountry.length === 2 ? localeCountry : (Intl.DateTimeFormat().resolvedOptions().timeZone === "Europe/Istanbul" ? "TR" : "US");
}

function safeNext() {
  const value = new URLSearchParams(location.search).get("next") || "/account.html";
  return value.startsWith("/") && !value.startsWith("//") ? value : "/account.html";
}

async function initRegister() {
  $("countryCode").value = selectedCountry();
  $("registerForm").addEventListener("submit", async event => {
    event.preventDefault();
    const button = $("registerSubmit");
    const password = $("password").value;
    if (password !== $("passwordConfirm").value) return showNotice("Parolalar birbiriyle eşleşmiyor.", true);
    if (!$("terms").checked) return showNotice("Devam etmek için kullanım ve gizlilik koşullarını kabul et.", true);
    setBusy(button, true, "Hesap hazırlanıyor…");
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
    } catch (error) { showNotice(error.message, true); }
    finally { setBusy(button, false, "Hesap oluştur"); }
  });
}

async function initLogin() {
  if (localStorage.getItem(TOKEN_KEY)) location.replace("/account.html");
  $("loginForm").addEventListener("submit", async event => {
    event.preventDefault();
    const button = $("loginSubmit");
    setBusy(button, true, "Giriş yapılıyor…");
    try {
      const body = await request("/billing/login", {
        method: "POST",
        body: JSON.stringify({email: $("email").value.trim(), password: $("password").value}),
      });
      localStorage.setItem(TOKEN_KEY, body.token);
      location.replace(safeNext());
    } catch (error) { showNotice(error.message, true); }
    finally { setBusy(button, false, "Giriş yap"); }
  });
  $("resendButton").addEventListener("click", async () => {
    const email = $("email").value.trim();
    if (!email) return showNotice("Önce e-posta adresini gir.", true);
    try {
      const body = await request("/billing/resend-verification", {method:"POST", body:JSON.stringify({email})});
      showNotice(body.message);
    } catch (error) { showNotice(error.message, true); }
  });
}

async function initVerify() {
  const token = new URLSearchParams(location.search).get("token") || "";
  if (!token) return showNotice("Doğrulama bağlantısı eksik.", true);
  try {
    const body = await request("/billing/verify-email", {method:"POST", body:JSON.stringify({token})});
    localStorage.setItem(TOKEN_KEY, body.token);
    $("verifyTitle").textContent = "E-posta doğrulandı";
    $("verifyText").textContent = "Hesabın etkin. LectureSift çalışma alanına geçebilirsin.";
    $("verifyAction").hidden = false;
  } catch (error) {
    $("verifyTitle").textContent = "Doğrulama tamamlanamadı";
    showNotice(error.message, true);
  }
}

async function initForgot() {
  $("forgotForm").addEventListener("submit", async event => {
    event.preventDefault();
    const button = $("forgotSubmit");
    setBusy(button, true, "Gönderiliyor…");
    try {
      const body = await request("/billing/forgot-password", {method:"POST", body:JSON.stringify({email:$("email").value.trim()})});
      showNotice(body.message);
    } catch (error) { showNotice(error.message, true); }
    finally { setBusy(button, false, "Yenileme bağlantısı gönder"); }
  });
}

async function initReset() {
  const token = new URLSearchParams(location.search).get("token") || "";
  if (!token) showNotice("Şifre yenileme bağlantısı eksik.", true);
  $("resetForm").addEventListener("submit", async event => {
    event.preventDefault();
    const password = $("password").value;
    if (password !== $("passwordConfirm").value) return showNotice("Parolalar birbiriyle eşleşmiyor.", true);
    const button = $("resetSubmit");
    setBusy(button, true, "Şifre yenileniyor…");
    try {
      const body = await request("/billing/reset-password", {method:"POST", body:JSON.stringify({token, new_password:password})});
      localStorage.removeItem(TOKEN_KEY);
      $("resetForm").hidden = true;
      showNotice(body.message);
      $("loginAfterReset").hidden = false;
    } catch (error) { showNotice(error.message, true); }
    finally { setBusy(button, false, "Şifreyi yenile"); }
  });
}

function planName(code) {
  return ({free:"Ücretsiz",credit:"Dakika Paketi",lite:"Lite",plus:"Plus",pro:"Pro",max:"Max",business:"Business"})[code] || code;
}

async function initAccount() {
  const token = localStorage.getItem(TOKEN_KEY);
  if (!token) return location.replace("/login.html?next=/account.html");
  try {
    const body = await request("/billing/me", {}, token);
    const account = body.account, user = account.user;
    $("accountName").textContent = user.name || user.email;
    $("accountEmail").textContent = user.email;
    $("accountPlan").textContent = planName(account.plan.code);
    $("remainingMinutes").textContent = account.remaining_minutes == null ? "Sınırsız" : account.remaining_minutes.toLocaleString("tr-TR");
    $("usedMinutes").textContent = `${account.used_minutes.toLocaleString("tr-TR")} dk kullanıldı`;
    const total = account.plan.minutes || 0;
    const percentage = total ? Math.min(100, Math.round(account.used_minutes / total * 100)) : 0;
    $("usageTrack").style.setProperty("--usage", `${percentage}%`);
    $("accountCountry").textContent = user.country_code || "—";
    $("accountPhone").textContent = user.phone || "Eklenmedi";
    $("ordersList").innerHTML = account.manual_orders.length ? account.manual_orders.map(order => `
      <div class="order-row"><span>${order.reference}<br><small>${planName(order.plan_code)}</small></span><strong>${order.status === "paid" ? "Aktif" : "Kontrol bekliyor"}</strong></div>`).join("") : '<p class="empty-copy">Henüz ödeme siparişin yok.</p>';
    $("accountPage").hidden = false;
  } catch {
    localStorage.removeItem(TOKEN_KEY);
    location.replace("/login.html?next=/account.html");
  }
  $("logoutButton").addEventListener("click", async () => {
    try { await request("/billing/logout", {method:"POST"}, token); } catch {}
    localStorage.removeItem(TOKEN_KEY);
    location.replace("/login.html");
  });
}

({register:initRegister, login:initLogin, verify:initVerify, forgot:initForgot, reset:initReset, account:initAccount})[page]?.();
