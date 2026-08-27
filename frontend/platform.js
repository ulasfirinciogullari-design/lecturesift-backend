(() => {
  const API_BASE = window.API || "https://lecturesift-backend.onrender.com";
  const tokenKey = "lecturesift_session_token";
  const guestKey = "lecturesift_guest_id";
  const adminKey = "lecturesift_admin_token";
  const getToken = () => localStorage.getItem(tokenKey) || "";
  const getAdmin = () => localStorage.getItem(adminKey) || "";
  if (!localStorage.getItem(guestKey)) localStorage.setItem(guestKey, crypto.randomUUID?.() || Math.random().toString(36).slice(2));
  const getGuest = () => localStorage.getItem(guestKey) || "guest";
  const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));

  function fd(values = {}) {
    const data = new FormData();
    Object.entries(values).forEach(([key, value]) => data.append(key, String(value ?? "")));
    return data;
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (getToken()) headers.set("X-LectureSift-Token", getToken());
    const response = await fetch(`${API_BASE}${path}`, {...options, headers});
    let body = null;
    try { body = await response.json(); } catch { body = {}; }
    if (!response.ok) throw new Error(body?.detail?.message || body?.message || "İşlem tamamlanamadı.");
    return body;
  }

  function formatEta(seconds) {
    seconds = Math.max(0, Math.round(Number(seconds) || 0));
    if (!seconds) return "Hesaplanıyor…";
    if (seconds < 60) return `yaklaşık ${seconds} sn`;
    const min = Math.floor(seconds / 60), sec = seconds % 60;
    return `yaklaşık ${min} dk${sec ? ` ${sec} sn` : ""}`;
  }

  function updateEta(job) {
    const node = document.getElementById("etaText");
    if (!node || !job) return;
    const total = Number(job.eta_seconds || 0);
    const percent = Math.max(0, Math.min(100, Number(job.percent || 0)));
    const remaining = job.status === "done" ? 0 : Math.round(total * (1 - percent / 100));
    const speed = Number(job.upload_bps || 0);
    const speedText = speed ? ` · yükleme ${(speed / 1024 / 1024).toFixed(1)} MB/sn` : "";
    const media = Number(job.media_minutes || 0);
    node.textContent = job.status === "done" ? "İşlem tamamlandı." : `Tahmini kalan: ${formatEta(remaining || total)}${media ? ` · medya ${media.toFixed(1)} dk` : ""}${speedText}`;
  }

  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    if (init?.body instanceof FormData && url.startsWith(API_BASE) && (url.endsWith("/jobs") || url.endsWith("/jobs/url"))) {
      if (!init.body.has("session_token")) init.body.append("session_token", getToken());
      if (!init.body.has("guest_id")) init.body.append("guest_id", getGuest());
    }
    const response = await originalFetch(input, init);
    if (url.startsWith(API_BASE) && /\/jobs(?:\/url|\/[a-f0-9-]+)?$/.test(url)) {
      response.clone().json().then(updateEta).catch(() => {});
    }
    return response;
  };

  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function(body) {
    if (body instanceof FormData && this.__lecturesiftPatched !== true) {
      body.append("session_token", getToken());
      body.append("guest_id", getGuest());
      this.__lecturesiftPatched = true;
      this.addEventListener("load", () => {
        try { if (this.responseURL.includes("/jobs")) updateEta(JSON.parse(this.responseText)); } catch {}
      });
    }
    return originalSend.call(this, body);
  };

  const defaultCurrency = (() => {
    const locale = navigator.language || "tr-TR";
    if (locale.toLowerCase().startsWith("tr")) return "TRY";
    if (locale.toLowerCase().includes("gb")) return "GBP";
    if (/^(de|fr|es|it|pt)/i.test(locale)) return "EUR";
    return "USD";
  })();

  const platform = document.getElementById("platformArea");
  if (!platform) return;
  platform.innerHTML = `
    <section id="features" class="platform-section platform-features">
      <div class="platform-heading"><p class="eyebrow">Özellikler</p><h2>LectureSift tek bir transkriptten fazlası.</h2></div>
      <div class="feature-grid">
        <article><b>Akıllı ders paketi</b><span>Kapsamlı özet, akıllı notlar, quiz, soru-cevap bilgi kartları ve slaytlar.</span></article>
        <article><b>Çoklu kaynak</b><span>Birden fazla videoyu sırala veya ses ve slayt videosunu ayrı yükle.</span></article>
        <article><b>Esnek çıktı</b><span>PDF, Word, TXT ve tüm seçilen içeriklerin ZIP paketi.</span></article>
        <article><b>Canlı işlem merkezi</b><span>Dosya boyutu, medya süresi, gerçek yükleme hızı ve geçmiş işlemlerle dinamik ETA.</span></article>
      </div>
    </section>

    <section id="plans" class="platform-section">
      <div class="platform-heading row"><div><p class="eyebrow">Planlar</p><h2>İhtiyacın kadar dakika.</h2></div><label class="currency-control">Para birimi <select id="planCurrency"><option>TRY</option><option>USD</option><option>EUR</option><option>GBP</option></select></label></div>
      <div id="planGrid" class="plan-grid"><div class="platform-loading">Planlar yükleniyor…</div></div>
      <p class="platform-muted">Hesapsız kullanım tek seferlik en fazla 5 dakikalık denemedir. Havale/EFT siparişinde oluşturulan sipariş numarasını dekont açıklamasına yaz.</p>
    </section>

    <section id="account" class="platform-section">
      <div class="platform-heading"><p class="eyebrow">Hesap</p><h2>Profil, bakiye ve doğrulamalar.</h2></div>
      <div id="accountContent" class="account-grid"></div>
    </section>

    <section id="admin" class="platform-section admin-section" hidden>
      <div class="platform-heading row"><div><p class="eyebrow">Yönetim</p><h2>Havale/EFT ve bonus onayları.</h2></div><button id="adminRefresh" class="secondary-action">Yenile</button></div>
      <div id="adminContent"></div>
    </section>
  `;

  async function loadPlans() {
    const currency = document.getElementById("planCurrency")?.value || defaultCurrency;
    try {
      const data = await api(`/billing/plans?currency=${encodeURIComponent(currency)}`);
      const symbol = {TRY:"₺", USD:"$", EUR:"€", GBP:"£"}[data.currency] || data.currency;
      document.getElementById("planGrid").innerHTML = data.plans.map(plan => `
        <article class="plan-card ${plan.id === "pro" ? "featured" : ""}">
          <div><span>${escapeHtml(plan.name)}</span><b>${plan.minutes} dk / ay</b></div>
          <div class="plan-prices"><strong>${symbol}${plan.monthly}<small>/ay</small></strong><span>${symbol}${plan.yearly} / yıl</span></div>
          <div class="plan-actions"><button data-buy="${plan.id}" data-cycle="monthly">Aylık seç</button><button data-buy="${plan.id}" data-cycle="yearly">Yıllık seç</button></div>
        </article>`).join("");
      document.querySelectorAll("[data-buy]").forEach(button => button.onclick = () => startOrder(button.dataset.buy, button.dataset.cycle, data.currency));
    } catch (error) {
      document.getElementById("planGrid").innerHTML = `<div class="platform-error">${escapeHtml(error.message)}</div>`;
    }
  }

  async function startOrder(planId, cycle, currency) {
    if (!getToken()) {
      location.hash = "account";
      renderAccount("Plan satın almak için önce e-posta ile giriş yap.");
      return;
    }
    try {
      const order = await api("/billing/bank-transfer", {method:"POST", body:fd({plan_id:planId, cycle, currency})});
      const bankReady = order.bank?.iban && order.bank?.recipient;
      const message = bankReady
        ? `<b>Sipariş: ${escapeHtml(order.order_no)}</b><p>${escapeHtml(order.amount)} ${escapeHtml(order.currency)} tutarı <strong>${escapeHtml(order.bank.recipient)}</strong> adına <code>${escapeHtml(order.bank.iban)}</code> hesabına gönder.</p><p>Dekont açıklamasına yalnızca <code>${escapeHtml(order.transfer_note)}</code> sipariş numarasını yaz. Ödeme admin onayından sonra dakikaların hesabına eklenir.</p>`
        : `<b>Sipariş oluşturuldu: ${escapeHtml(order.order_no)}</b><p>Banka hesabı sunucuda henüz yapılandırılmadı. Yönetici BANK_TRANSFER_IBAN ve BANK_TRANSFER_RECIPIENT değişkenlerini eklemeli.</p>`;
      showPlatformNotice(message);
      await renderAccount();
    } catch (error) { showPlatformNotice(escapeHtml(error.message), true); }
  }

  function showPlatformNotice(html, error = false) {
    let node = document.getElementById("platformNotice");
    if (!node) { node = document.createElement("div"); node.id = "platformNotice"; document.body.appendChild(node); }
    node.className = `platform-notice ${error ? "error" : ""}`;
    node.innerHTML = `<button aria-label="Kapat">×</button><div>${html}</div>`;
    node.querySelector("button").onclick = () => node.remove();
  }

  function loginForm(message = "") {
    return `<div class="account-card login-card"><h3>E-posta ile giriş / kayıt</h3>${message ? `<p class="platform-message">${escapeHtml(message)}</p>` : ""}<label>E-posta<input id="loginEmail" type="email" autocomplete="email" placeholder="sen@ornek.com"></label><label>Ad soyad<input id="loginName" autocomplete="name" placeholder="Adın"></label><button id="requestCode">Doğrulama kodu gönder</button><div id="codeArea" hidden><label>6 haneli kod<input id="loginCode" inputmode="numeric" maxlength="6"></label><button id="verifyCode">Kodu doğrula</button></div><small>Giriş şifresizdir; e-posta doğrulama koduyla yapılır.</small></div>`;
  }

  async function renderAccount(message = "") {
    const root = document.getElementById("accountContent");
    if (!getToken()) {
      root.innerHTML = loginForm(message);
      document.getElementById("requestCode").onclick = async () => {
        try {
          const email = document.getElementById("loginEmail").value;
          await api("/auth/request-code", {method:"POST", body:fd({email, purpose:"login"})});
          document.getElementById("codeArea").hidden = false;
          showPlatformNotice("Doğrulama kodu e-posta adresine gönderildi.");
        } catch (error) { showPlatformNotice(escapeHtml(error.message), true); }
      };
      document.getElementById("verifyCode").onclick = async () => {
        try {
          const result = await api("/auth/verify-code", {method:"POST", body:fd({email:document.getElementById("loginEmail").value, code:document.getElementById("loginCode").value, name:document.getElementById("loginName").value})});
          localStorage.setItem(tokenKey, result.token); await renderAccount();
        } catch (error) { showPlatformNotice(escapeHtml(error.message), true); }
      };
      return;
    }
    try {
      const user = await api("/account/me");
      const orders = await api("/billing/orders");
      root.innerHTML = `
        <div class="account-card"><h3>${escapeHtml(user.name || "LectureSift kullanıcısı")}</h3><p>${escapeHtml(user.email)}</p><div class="balance"><b>${user.minutes_balance}</b><span>dakika bakiye</span></div><p>Plan: <strong>${escapeHtml(user.plan)}</strong></p><button id="logoutButton" class="secondary-action">Çıkış yap</button></div>
        <div class="account-card"><h3>Profili düzenle</h3><label>Ad soyad<input id="profileName" value="${escapeHtml(user.name)}"></label><label>Tercih edilen dil<select id="profileLanguage"><option value="tr">Türkçe</option><option value="en">English</option><option value="de">Deutsch</option><option value="fr">Français</option></select></label><button id="saveProfile">Kaydet</button><hr><label>Yeni e-posta<input id="newEmail" type="email"></label><button id="requestEmailChange" class="secondary-action">Yeni e-postayı doğrula</button><div id="emailChangeCode" hidden><label>Kod<input id="newEmailCode" maxlength="6" inputmode="numeric"></label><button id="verifyEmailChange">E-posta değişikliğini tamamla</button></div></div>
        <div class="account-card"><h3>Instagram +30 dk</h3><p>LectureSift Instagram hesabını takip ediyorsan kullanıcı adını gönder. Takip doğrulandıktan sonra bonus bir kez eklenir.</p><label>Instagram kullanıcı adı<input id="instagramHandle" placeholder="@kullanici"></label><button id="claimInstagram" ${user.instagram_bonus_claimed ? "disabled" : ""}>${user.instagram_bonus_claimed ? "Bonus kullanıldı" : "+30 dk talep et"}</button></div>
        <div class="account-card orders-card"><h3>Siparişler</h3>${orders.length ? orders.map(order => `<div class="order-row"><b>${escapeHtml(order.order_no)}</b><span>${escapeHtml(order.plan_name)} · ${escapeHtml(order.amount)} ${escapeHtml(order.currency)}</span><i>${escapeHtml(order.status)}</i></div>`).join("") : "<p>Henüz sipariş yok.</p>"}</div>`;
      document.getElementById("profileLanguage").value = user.preferred_language || "tr";
      document.getElementById("logoutButton").onclick = () => { localStorage.removeItem(tokenKey); renderAccount(); };
      document.getElementById("saveProfile").onclick = async () => { try { await api("/account/profile", {method:"POST", body:fd({name:document.getElementById("profileName").value, preferred_language:document.getElementById("profileLanguage").value})}); showPlatformNotice("Profil güncellendi."); await renderAccount(); } catch (error) { showPlatformNotice(escapeHtml(error.message), true); } };
      document.getElementById("requestEmailChange").onclick = async () => { try { const email = document.getElementById("newEmail").value; await api("/auth/request-code", {method:"POST", body:fd({email, purpose:"email_change", session_token:getToken()})}); document.getElementById("emailChangeCode").hidden = false; showPlatformNotice("Yeni e-posta adresine doğrulama kodu gönderildi."); } catch (error) { showPlatformNotice(escapeHtml(error.message), true); } };
      document.getElementById("verifyEmailChange").onclick = async () => { try { const result = await api("/auth/verify-code", {method:"POST", body:fd({email:document.getElementById("newEmail").value, code:document.getElementById("newEmailCode").value})}); if (result.token) localStorage.setItem(tokenKey, result.token); showPlatformNotice("E-posta adresin değiştirildi."); await renderAccount(); } catch (error) { showPlatformNotice(escapeHtml(error.message), true); } };
      document.getElementById("claimInstagram").onclick = async () => { try { await api("/rewards/instagram/claim", {method:"POST", body:fd({handle:document.getElementById("instagramHandle").value})}); showPlatformNotice("Bonus talebin alındı. Takip doğrulandıktan sonra +30 dk eklenecek."); } catch (error) { showPlatformNotice(escapeHtml(error.message), true); } };
    } catch (error) {
      localStorage.removeItem(tokenKey); root.innerHTML = loginForm("Oturumun yenilenmesi gerekiyor."); renderAccount();
    }
  }

  async function renderAdmin() {
    const section = document.getElementById("admin");
    const root = document.getElementById("adminContent");
    section.hidden = false;
    if (!getAdmin()) {
      root.innerHTML = `<div class="account-card"><h3>Admin girişi</h3><label>Admin anahtarı<input id="adminTokenInput" type="password"></label><button id="saveAdminToken">Giriş</button></div>`;
      document.getElementById("saveAdminToken").onclick = () => { localStorage.setItem(adminKey, document.getElementById("adminTokenInput").value); renderAdmin(); };
      return;
    }
    try {
      const headers = {"X-Admin-Token": getAdmin()};
      const [ordersResponse, rewardsResponse] = await Promise.all([originalFetch(`${API_BASE}/admin/orders?status=pending_transfer`, {headers}), originalFetch(`${API_BASE}/admin/rewards`, {headers})]);
      if (!ordersResponse.ok || !rewardsResponse.ok) throw new Error("Admin anahtarı geçersiz.");
      const orders = await ordersResponse.json(), rewards = await rewardsResponse.json();
      root.innerHTML = `<div class="admin-grid"><div class="account-card"><h3>Bekleyen havale/EFT</h3>${orders.length ? orders.map(o => `<div class="admin-row"><div><b>${escapeHtml(o.order_no)}</b><span>${escapeHtml(o.email)} · ${escapeHtml(o.amount)} ${escapeHtml(o.currency)} · ${escapeHtml(o.plan_name)}</span></div><button data-order="${escapeHtml(o.order_no)}" data-approve="true">Onayla</button><button class="danger" data-order="${escapeHtml(o.order_no)}" data-approve="false">Reddet</button></div>`).join("") : "<p>Bekleyen sipariş yok.</p>"}</div><div class="account-card"><h3>Instagram bonus talepleri</h3>${rewards.filter(r => r.status === "pending_verification").map(r => `<div class="admin-row"><div><b>@${escapeHtml(r.handle)}</b><span>${escapeHtml(r.email)} · +${escapeHtml(r.minutes)} dk</span></div><button data-reward="${escapeHtml(r.id)}" data-approve="true">Onayla</button><button class="danger" data-reward="${escapeHtml(r.id)}" data-approve="false">Reddet</button></div>`).join("") || "<p>Bekleyen bonus yok.</p>"}</div></div>`;
      root.querySelectorAll("[data-order]").forEach(btn => btn.onclick = async () => { await originalFetch(`${API_BASE}/admin/orders/${encodeURIComponent(btn.dataset.order)}/decision`, {method:"POST", headers:{"X-Admin-Token":getAdmin()}, body:fd({approve:btn.dataset.approve})}); renderAdmin(); renderAccount(); });
      root.querySelectorAll("[data-reward]").forEach(btn => btn.onclick = async () => { await originalFetch(`${API_BASE}/admin/rewards/${encodeURIComponent(btn.dataset.reward)}/decision`, {method:"POST", headers:{"X-Admin-Token":getAdmin()}, body:fd({approve:btn.dataset.approve})}); renderAdmin(); renderAccount(); });
    } catch (error) {
      localStorage.removeItem(adminKey); root.innerHTML = `<div class="platform-error">${escapeHtml(error.message)}</div>`;
    }
  }

  document.getElementById("planCurrency").value = defaultCurrency;
  document.getElementById("planCurrency").onchange = loadPlans;
  document.getElementById("adminRefresh").onclick = renderAdmin;
  const params = new URLSearchParams(location.search);
  if (params.get("admin") === "1" || getAdmin()) renderAdmin();

  document.addEventListener("click", event => {
    const anchor = event.target.closest('a[href="#account"]'); if (anchor) setTimeout(() => renderAccount(), 0);
  });

  // Avoid showing a translated tab when the source/output selection is explicitly the same language.
  const sourceLanguage = document.getElementById("sourceLanguage"), outputLanguage = document.getElementById("outputLanguage"), translateControl = document.getElementById("translateControl"), translateCheckbox = document.getElementById("translateTranscript");
  function syncTranslationChoice() {
    if (!sourceLanguage || !outputLanguage || !translateControl || !translateCheckbox) return;
    const same = sourceLanguage.value !== "auto" && sourceLanguage.value === outputLanguage.value;
    translateCheckbox.checked = same ? false : translateCheckbox.checked;
    translateCheckbox.disabled = same;
    translateControl.classList.toggle("is-disabled", same);
  }
  sourceLanguage?.addEventListener("change", syncTranslationChoice); outputLanguage?.addEventListener("change", syncTranslationChoice); setTimeout(syncTranslationChoice, 200);

  loadPlans(); renderAccount();
})();
