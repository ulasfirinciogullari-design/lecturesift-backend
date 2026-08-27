(() => {
  const STORAGE_KEY = "lecturesift-verification-email";
  let pendingEmail = sessionStorage.getItem(STORAGE_KEY) || "";
  let resendTimer = null;

  const copy = {
    tr: {
      title: "E-posta adresini doğrula",
      help: "6 haneli doğrulama kodunu gönderdik:",
      placeholder: "123456",
      verify: "Kodu doğrula",
      resend: "Yeni kod gönder",
      change: "Farklı e-posta",
      sent: "Kod gönderildi. Gelen kutunu ve spam klasörünü kontrol et.",
      resent: "Yeni doğrulama kodu gönderildi.",
      wait: seconds => `${seconds} sn sonra yeniden gönder`,
      invalid: "6 haneli doğrulama kodunu gir.",
      credentials: "Geçerli e-posta ve en az 10 karakterli parola gir.",
    },
    en: {
      title: "Verify your email address",
      help: "We sent a 6-digit verification code to:",
      placeholder: "123456",
      verify: "Verify code",
      resend: "Send a new code",
      change: "Use another email",
      sent: "Code sent. Check your inbox and spam folder.",
      resent: "A new verification code was sent.",
      wait: seconds => `Resend in ${seconds}s`,
      invalid: "Enter the 6-digit verification code.",
      credentials: "Enter a valid email and a password of at least 10 characters.",
    },
  };

  function c(key, value) {
    const language = currentLanguage === "tr" ? "tr" : "en";
    const entry = copy[language][key];
    return typeof entry === "function" ? entry(value) : entry;
  }

  function installStyles() {
    const style = document.createElement("style");
    style.textContent = `
      .verification-form { grid-template-columns:minmax(220px,1fr) minmax(145px,.55fr) auto auto auto; }
      .verification-form .verification-code { text-align:center; letter-spacing:.32em; font-size:16px; font-weight:850; }
      .verification-form .verification-notice { min-height:14px; color:#7fe0b7; }
      .verification-form button.tertiary { border:1px solid transparent; background:transparent; color:#90a3c3; }
      .verification-form button:disabled { opacity:.52; cursor:not-allowed; }
      @media (max-width:980px) {
        .verification-form { grid-template-columns:1fr 1fr; }
        .verification-form > div { grid-column:span 2; }
      }
      @media (max-width:430px) {
        .verification-form { grid-template-columns:1fr; }
        .verification-form > div { grid-column:span 1; }
      }
    `;
    document.head.appendChild(style);
  }

  function installForm() {
    const authForm = $("authForm");
    if (!authForm || $("verificationForm")) return;
    const form = document.createElement("div");
    form.id = "verificationForm";
    form.className = "auth-form verification-form";
    form.hidden = true;
    form.innerHTML = `
      <div>
        <strong id="verificationTitle"></strong>
        <small><span id="verificationHelp"></span> <b id="verificationEmail"></b></small>
        <small id="verificationNotice" class="verification-notice"></small>
      </div>
      <input id="verificationCode" class="verification-code" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]{6}">
      <button id="verifyEmailButton" type="button"></button>
      <button id="resendCodeButton" type="button" class="secondary"></button>
      <button id="cancelVerificationButton" type="button" class="tertiary"></button>
    `;
    authForm.insertAdjacentElement("afterend", form);
  }

  function localizeForm() {
    if (!$("verificationForm")) return;
    $("verificationTitle").textContent = c("title");
    $("verificationHelp").textContent = c("help");
    $("verificationCode").placeholder = c("placeholder");
    $("verifyEmailButton").textContent = c("verify");
    if (!$("resendCodeButton").disabled) $("resendCodeButton").textContent = c("resend");
    $("cancelVerificationButton").textContent = c("change");
  }

  function setNotice(message) {
    $("verificationNotice").textContent = message || "";
  }

  function startResendCountdown(seconds) {
    clearInterval(resendTimer);
    const button = $("resendCodeButton");
    let remaining = Math.max(0, Number(seconds) || 0);
    const render = () => {
      button.disabled = remaining > 0;
      button.textContent = remaining > 0 ? c("wait", remaining) : c("resend");
    };
    render();
    if (!remaining) return;
    resendTimer = setInterval(() => {
      remaining -= 1;
      render();
      if (remaining <= 0) clearInterval(resendTimer);
    }, 1000);
  }

  function showVerification(email, resendAfter = 0, notice = "") {
    pendingEmail = String(email || "").trim().toLowerCase();
    if (!pendingEmail) return;
    sessionStorage.setItem(STORAGE_KEY, pendingEmail);
    $("verificationEmail").textContent = pendingEmail;
    $("verificationCode").value = "";
    setNotice(notice || c("sent"));
    startResendCountdown(resendAfter);
    renderBillingAccount();
    $("verificationCode").focus();
  }

  function clearVerification() {
    pendingEmail = "";
    sessionStorage.removeItem(STORAGE_KEY);
    clearInterval(resendTimer);
    setNotice("");
  }

  async function request(path, payload) {
    const response = await fetch(`${API}${path}`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    const text = await response.text();
    let body = {};
    try { body = text ? JSON.parse(text) : {}; } catch { body = {message: text}; }
    if (!response.ok) {
      const detail = body.detail || body;
      throw Object.assign(new Error(detail.message || body.message || "Request failed"), {
        code: detail.code || body.code || "LS-SYSTEM-01",
        retryAfter: detail.retry_after || 0,
      });
    }
    return body;
  }

  function finishAuthentication(body) {
    billingToken = body.token;
    billingAccount = body.account;
    localStorage.setItem("lecturesift-billing-token", billingToken);
    $("billingPassword").value = "";
    clearVerification();
    renderBillingAccount();
    renderPlans();
  }

  async function authenticate(mode) {
    const email = $("billingEmail").value.trim();
    const password = $("billingPassword").value;
    if (!email || password.length < 10) {
      showError(c("credentials"), "LS-BILL-11");
      return;
    }
    try {
      const body = await request(`/billing/${mode}`, {email, password, language: currentLanguage});
      if (body.verification_required) {
        showVerification(body.email || email, body.resend_after || 0);
        return;
      }
      finishAuthentication(body);
    } catch (error) {
      if (error.code === "LS-AUTH-03" || error.code === "LS-AUTH-06") {
        showVerification(email, 0, error.message);
        return;
      }
      showError(error.message, error.code || "LS-BILL-12");
    }
  }

  async function verify() {
    const code = $("verificationCode").value.replace(/\D/g, "");
    if (code.length !== 6) {
      showError(c("invalid"), "LS-AUTH-07");
      return;
    }
    try {
      const body = await request("/billing/verify-email", {email: pendingEmail, code});
      finishAuthentication(body);
    } catch (error) {
      showError(error.message, error.code || "LS-AUTH-07");
    }
  }

  async function resend() {
    try {
      const body = await request("/billing/resend-verification", {
        email: pendingEmail,
        language: currentLanguage,
      });
      setNotice(c("resent"));
      startResendCountdown(body.resend_after || 60);
    } catch (error) {
      if (error.retryAfter) startResendCountdown(error.retryAfter);
      showError(error.message, error.code || "LS-AUTH-09");
    }
  }

  installStyles();
  installForm();

  const originalRenderBillingAccount = renderBillingAccount;
  renderBillingAccount = function renderBillingAccountWithVerification() {
    originalRenderBillingAccount();
    const loggedIn = Boolean(billingAccount && billingToken);
    if (loggedIn && pendingEmail) clearVerification();
    $("authForm").hidden = loggedIn || Boolean(pendingEmail);
    $("verificationForm").hidden = loggedIn || !pendingEmail;
    if (pendingEmail) $("verificationEmail").textContent = pendingEmail;
    localizeForm();
  };

  $("registerButton").onclick = () => authenticate("register");
  $("loginButton").onclick = () => authenticate("login");
  $("verifyEmailButton").onclick = verify;
  $("resendCodeButton").onclick = resend;
  $("cancelVerificationButton").onclick = () => {
    clearVerification();
    renderBillingAccount();
    $("billingEmail").focus();
  };
  $("verificationCode").addEventListener("input", event => {
    event.target.value = event.target.value.replace(/\D/g, "").slice(0, 6);
  });
  $("verificationCode").addEventListener("keydown", event => {
    if (event.key === "Enter") verify();
  });
  $("uiLanguage").addEventListener("change", () => {
    localizeForm();
    if ($("resendCodeButton").disabled) return;
    $("resendCodeButton").textContent = c("resend");
  });

  if (pendingEmail) {
    $("verificationEmail").textContent = pendingEmail;
    startResendCountdown(0);
  }
  renderBillingAccount();
})();
