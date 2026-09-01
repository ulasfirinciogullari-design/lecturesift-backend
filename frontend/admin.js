const ADMIN_API = "https://api.lecturesift.com";
const admin$ = id => document.getElementById(id);
const adminT = (key, fallback) => window.LectureSiftI18n?.t(key) || fallback || key;
const adminLocale = () => window.LectureSiftI18n?.locale || "tr-TR";
const adminMinuteShort = () => adminT("unit.minuteShort", "dk");
const ADMIN_SESSION_TOKEN_KEY = "lecturesift-admin-session-token";
const ADMIN_VIEW_KEY = "lecturesift-admin-view";
const ADMIN_VIEWS = ["overview", "users", "finance", "support", "jobs", "costs", "system", "growth", "audit"];
let adminAccessToken = sessionStorage.getItem(ADMIN_SESSION_TOKEN_KEY) || "";
let adminLoading = false;
let adminState = {overview:{counts:{}}, users:[], userPagination:{page:1,total:0,total_pages:1}, orders:[], orderPagination:{page:1,total:0,total_pages:1}, rewards:[], refunds:[], credits:[], accountEvents:[], contacts:[], jobs:[], costs:null, billing:null, runtime:null, ads:null, analytics:null};
let selectedAdminUsers = new Set();
let adminUserSearchTimer = null;
let adminOrderSearchTimer = null;

function adminViewFromHash() {
  const hash = window.location.hash.replace(/^#/, "");
  const legacyViews = {adminUsersSection:"users", adminOrdersSection:"finance", adminMessagesSection:"support", adminJobsSection:"jobs", adminSystemSection:"system", adminAuditSection:"audit", adminTimelineSection:"overview"};
  if (legacyViews[hash]) return legacyViews[hash];
  return hash.startsWith("admin-") && ADMIN_VIEWS.includes(hash.slice(6)) ? hash.slice(6) : "";
}

function activateAdminView(requestedView, {focus = false, updateHash = true} = {}) {
  const view = ADMIN_VIEWS.includes(requestedView) ? requestedView : "overview";
  document.querySelectorAll("[data-admin-view]").forEach(panel => {
    const selected = panel.dataset.adminView === view;
    panel.hidden = !selected;
    panel.setAttribute("aria-hidden", String(!selected));
  });
  document.querySelectorAll("[data-admin-view-button]").forEach(button => {
    const selected = button.dataset.adminViewButton === view;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    if (selected && focus) {
      button.focus({preventScroll:true});
      button.scrollIntoView({behavior:"smooth", block:"nearest", inline:"center"});
    }
  });
  sessionStorage.setItem(ADMIN_VIEW_KEY, view);
  if (updateHash) history.replaceState(null, "", `${location.pathname}${location.search}#admin-${view}`);
}

function setupAdminNavigation() {
  const buttons = [...document.querySelectorAll("[data-admin-view-button]")];
  buttons.forEach((button, index) => {
    const panel = document.querySelector(`[data-admin-view="${button.dataset.adminViewButton}"]`);
    button.id = `adminViewTab-${button.dataset.adminViewButton}`;
    panel?.setAttribute("aria-labelledby", button.id);
    button.addEventListener("click", () => activateAdminView(button.dataset.adminViewButton, {focus:true}));
    button.addEventListener("keydown", event => {
      const keyMoves = {ArrowDown:1, ArrowRight:1, ArrowUp:-1, ArrowLeft:-1};
      let nextIndex = keyMoves[event.key] === undefined ? index : (index + keyMoves[event.key] + buttons.length) % buttons.length;
      if (event.key === "Home") nextIndex = 0;
      else if (event.key === "End") nextIndex = buttons.length - 1;
      else if (keyMoves[event.key] === undefined) return;
      event.preventDefault();
      activateAdminView(buttons[nextIndex].dataset.adminViewButton, {focus:true});
    });
  });
  const initialView = adminViewFromHash() || sessionStorage.getItem(ADMIN_VIEW_KEY) || "overview";
  activateAdminView(initialView, {updateHash:false});
  window.addEventListener("hashchange", () => {
    const view = adminViewFromHash();
    if (view) activateAdminView(view, {updateHash:false});
  });
}

function adminEscape(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"})[char]);
}

function adminMoney(amountMinor, currency) {
  try {
    return new Intl.NumberFormat(adminLocale(), {style:"currency", currency:currency || "TRY"}).format(Number(amountMinor || 0) / 100);
  } catch (_) {
    return `${(Number(amountMinor || 0) / 100).toLocaleString(adminLocale())} ${currency || "TRY"}`;
  }
}

function adminUsd(value, maximumFractionDigits = 4) {
  return new Intl.NumberFormat(adminLocale(), {style:"currency", currency:"USD", minimumFractionDigits:2, maximumFractionDigits}).format(Number(value || 0));
}

function adminDateObject(value) {
  if (typeof value === "number" || (/^\d+(\.\d+)?$/.test(String(value || "")))) return new Date(Number(value) * 1000);
  return new Date(value);
}

function adminDate(value) {
  const date = adminDateObject(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(adminLocale(), {dateStyle:"medium", timeStyle:"short", timeZone:"Europe/Istanbul"}).format(date);
}

function adminRelativeDate(value) {
  const date = adminDateObject(value);
  if (Number.isNaN(date.getTime())) return "—";
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const abs = Math.abs(seconds);
  const [amount, unit] = abs < 60 ? [seconds, "second"] : abs < 3600 ? [Math.round(seconds / 60), "minute"] : abs < 86400 ? [Math.round(seconds / 3600), "hour"] : [Math.round(seconds / 86400), "day"];
  return new Intl.RelativeTimeFormat(adminLocale(), {numeric:"auto"}).format(amount, unit);
}

function adminNotice(message, error = false) {
  const panelNotice = admin$("adminOperationNotice");
  const node = panelNotice && !admin$("adminPanel")?.hidden ? panelNotice : admin$("adminNotice");
  node.textContent = message;
  node.classList.toggle("error", error);
  node.hidden = false;
}

async function adminRequest(path, options = {}) {
  const response = await fetch(`${ADMIN_API}${path}`, {
    ...options,
    headers:{"Content-Type":"application/json", Authorization:`Bearer ${adminAccessToken}`, ...(options.headers || {})},
    cache:"no-store",
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body?.detail?.message || adminT("error.request", "İstek tamamlanamadı."));
  return body;
}

async function adminPublicRequest(path) {
  return fetch(`${ADMIN_API}${path}`, {cache:"no-store"}).then(response => response.ok ? response.json() : null).catch(() => null);
}

function adminStatusLabel(status) {
  const labels = {pending:"Bekliyor", created:"Başlatıldı", paid:"Ödendi", failed:"Hatalı", token_failed:"Ödeme başlatılamadı", rejected:"Reddedildi", cancelled:"İptal", requested:"İncelenecek", approved_pending_refund:"İade bekliyor", completed:"Tamamlandı", pending_verification:"Doğrulanacak", approved:"Onaylandı", queued:"Kuyrukta", working:"Çalışıyor", done:"Tamamlandı", new:"Yeni", read:"Okundu", resolved:"Çözümlendi"};
  return labels[status] || adminT(`order.${status}`, status || "—");
}

function renderAdminPagination(containerId, pagination, onPage) {
  const container = admin$(containerId);
  if (!container) return;
  const page = Number(pagination?.page || 1);
  const totalPages = Number(pagination?.total_pages || 1);
  const total = Number(pagination?.total || 0);
  container.innerHTML = `<span>${total.toLocaleString(adminLocale())} kayıttan ${total ? ((page - 1) * Number(pagination?.page_size || 50) + 1).toLocaleString(adminLocale()) : 0}–${Math.min(total, page * Number(pagination?.page_size || 50)).toLocaleString(adminLocale())}</span><div><button class="admin-action" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>← Önceki</button><strong>${page.toLocaleString(adminLocale())} / ${totalPages.toLocaleString(adminLocale())}</strong><button class="admin-action" data-page="${page + 1}" ${page >= totalPages ? "disabled" : ""}>Sonraki →</button></div>`;
  container.querySelectorAll("[data-page]:not([disabled])").forEach(button => button.addEventListener("click", () => onPage(Number(button.dataset.page))));
}

function renderAdminOrders(orders) {
  const rows = orders.map(order => {
    const methodName = order.payment_method === "bank_transfer"
      ? (order.provider === "iyzico" ? "iyzico Korumalı Havale/EFT" : "IBAN / manuel havale")
      : order.payment_method === "unknown"
      ? "iyzico yöntemi (eski kayıt)"
      : `${String(order.provider || "kart").toUpperCase()} kart`;
    const method = order.payment_method_confirmed === false
      ? `Tercih / doğrulama bekliyor: ${methodName}`
      : methodName;
    const activity = order.user?.last_activity;
    const details = `<details class="admin-row-details"><summary>Detay</summary><dl><div><dt>Sipariş oluşturma</dt><dd>${adminEscape(adminDate(order.created_at))}</dd></div><div><dt>Son güncelleme</dt><dd>${adminEscape(adminDate(order.updated_at))}</dd></div><div><dt>Ödeme yolu</dt><dd>${adminEscape(method)}</dd></div><div><dt>Kullanıcı ağı</dt><dd>${adminEscape(activity?.ip_network || "Yeni kayıtlarda oluşacak")}</dd></div><div><dt>Son kullanıcı hareketi</dt><dd>${adminEscape(activity?.created_at ? adminDate(activity.created_at) : "—")}</dd></div><div><dt>Onay izi</dt><dd>${adminEscape(order.consent?.ip_fingerprint || "—")}</dd></div></dl></details>`;
    return `<tr>
    <td data-label="Sipariş"><strong>${adminEscape(order.order_number || order.reference)}</strong><br><small>${adminEscape(adminDate(order.created_at))}</small></td>
    <td data-label="Müşteri">${adminEscape(order.user?.name || "—")}<br><small>${adminEscape(order.user?.email || "")}</small></td>
    <td data-label="Ödeme"><span class="status-pill ${order.payment_method_confirmed === false || order.payment_method === "bank_transfer" ? "" : "paid"}">${adminEscape(method)}</span></td>
    <td data-label="Plan">${adminEscape(order.plan_code)} / ${adminEscape(order.interval)}</td><td data-label="Tutar">${adminMoney(order.amount_minor, order.currency)}</td>
    <td data-label="Durum"><span class="status-pill ${order.status === "paid" ? "paid" : ""}">${adminEscape(adminStatusLabel(order.status))}</span></td>
    <td data-label="Hata">${order.failure_message || order.failure_code ? `${adminEscape(order.failure_message || "Ödeme onaylanmadı")}${order.failure_code ? `<br><small>${adminEscape(order.failure_code)}</small>` : ""}` : "—"}</td>
    <td data-label="İşlem">${order.provider === "bank_transfer" && order.status === "pending" ? `<span class="admin-actions"><button class="admin-action approve" data-order-decision="${adminEscape(order.reference)}" data-approve="1">${adminEscape(adminT("admin.approve", "Onayla"))}</button><button class="admin-action reject" data-order-decision="${adminEscape(order.reference)}" data-approve="0">${adminEscape(adminT("admin.reject", "Reddet"))}</button></span>${details}` : details}</td>
  </tr>`;
  }).join("");
  admin$("adminOrders").innerHTML = `<table class="admin-table admin-record-table"><thead><tr><th>${adminT("payment.orderNumber","Sipariş no")}</th><th>${adminT("admin.customer","Müşteri")}</th><th>${adminT("admin.provider","Yöntem")}</th><th>${adminT("admin.plan","Plan")}</th><th>${adminT("payment.amount","Tutar")}</th><th>${adminT("admin.status","Durum")}</th><th>Hata / ret nedeni</th><th>${adminT("admin.action","İşlem")}</th></tr></thead><tbody>${rows || `<tr><td colspan="8">${adminT("admin.noOrders","Sipariş bulunamadı.")}</td></tr>`}</tbody></table>`;
  admin$("adminOrdersResultCount").textContent = `${Number(adminState.orderPagination.total || 0).toLocaleString(adminLocale())} kayıt`;
  renderAdminPagination("adminOrdersPagination", adminState.orderPagination, page => loadAdminOrders(page));
  document.querySelectorAll("[data-order-decision]").forEach(button => button.addEventListener("click", () => decideOrder(button)));
}

function renderAdminRewards(rewards) {
  const rows = rewards.map(reward => `<tr><td data-label="Kullanıcı"><strong>@${adminEscape(reward.handle)}</strong><br><small>${adminEscape(reward.email || "")}</small></td><td data-label="Dakika">+${Number(reward.minutes || 0).toLocaleString(adminLocale())} ${adminEscape(adminMinuteShort())}</td><td data-label="Durum">${adminEscape(adminStatusLabel(reward.status))}</td><td data-label="İşlem"><span class="admin-actions"><button class="admin-action approve" data-reward-decision="${adminEscape(reward.id)}" data-approve="1">Onayla</button><button class="admin-action reject" data-reward-decision="${adminEscape(reward.id)}" data-approve="0">Reddet</button></span></td></tr>`).join("");
  admin$("adminRewards").innerHTML = `<table class="admin-table admin-record-table"><thead><tr><th>Kullanıcı adı</th><th>Dakika</th><th>Durum</th><th>İşlem</th></tr></thead><tbody>${rows || '<tr><td colspan="4">Bekleyen bonus talebi yok.</td></tr>'}</tbody></table>`;
  document.querySelectorAll("[data-reward-decision]").forEach(button => button.addEventListener("click", () => decideReward(button)));
}

function renderAdminRefunds(refunds) {
  const rows = refunds.map(item => {
    const note = `<input class="admin-inline-input" data-refund-note="${adminEscape(item.id)}" maxlength="500" placeholder="Yönetici notu (isteğe bağlı)">`;
    let actions = "—";
    if (item.status === "requested") actions = `${note}<span class="admin-actions"><button class="admin-action approve" data-refund-decision="${adminEscape(item.id)}" data-action="approve">Onayla</button><button class="admin-action reject" data-refund-decision="${adminEscape(item.id)}" data-action="reject">Reddet</button></span>`;
    if (item.status === "approved_pending_refund") actions = `${note}<button class="admin-action approve" data-refund-decision="${adminEscape(item.id)}" data-action="complete">İade gönderildi</button>`;
    return `<tr><td data-label="Sipariş"><strong>${adminEscape(item.order_reference)}</strong><br><small title="${adminEscape(adminDate(item.created_at))}">${adminEscape(adminRelativeDate(item.created_at))}</small></td><td data-label="Müşteri">${adminEscape(item.user?.name || "—")}<br><small>${adminEscape(item.user?.email || "")}</small></td><td data-label="Neden">${adminEscape(item.reason)}</td><td data-label="Durum">${adminEscape(adminStatusLabel(item.status))}</td><td data-label="İşlem">${actions}</td></tr>`;
  }).join("");
  admin$("adminRefunds").innerHTML = `<table class="admin-table admin-record-table"><thead><tr><th>Sipariş no</th><th>Müşteri</th><th>İade nedeni</th><th>Durum</th><th>İşlem</th></tr></thead><tbody>${rows || '<tr><td colspan="5">İade talebi bulunamadı.</td></tr>'}</tbody></table>`;
  document.querySelectorAll("[data-refund-decision]").forEach(button => button.addEventListener("click", () => decideRefund(button)));
}

function renderAdminUsers(users) {
  const rows = users.map(user => {
    const activity = user.last_activity;
    return `<tr>
      <td data-label="Seç"><input type="checkbox" data-user-select="${adminEscape(user.id)}" aria-label="${adminEscape(user.email)} hesabını seç" ${selectedAdminUsers.has(user.id) ? "checked" : ""}></td>
      <td data-label="Kullanıcı"><button class="admin-user-link" data-user-open="${adminEscape(user.id)}"><strong>${adminEscape(user.name || "İsimsiz kullanıcı")}</strong><small>${adminEscape(user.email)}</small></button>${user.is_protected ? '<span class="status-pill paid">Korunan</span>' : ""}</td>
      <td data-label="Durum"><span class="status-pill ${user.email_verified ? "paid" : ""}">${user.email_verified ? "Doğrulandı" : "Bekliyor"}</span></td>
      <td data-label="Plan"><strong>${adminEscape(user.plan_code || "free")}</strong>${user.subscription ? `<br><small>${adminEscape(adminDate(user.subscription.ends_at))} bitiş</small>` : ""}</td>
      <td data-label="Dakika">${Number(user.credit_minutes || 0).toLocaleString(adminLocale())} ${adminEscape(adminMinuteShort())}<br><small>${Number(user.total_usage_minutes || 0).toLocaleString(adminLocale())} dk kullanıldı</small></td>
      <td data-label="Kayıt"><span title="${adminEscape(adminDate(user.created_at))}">${adminEscape(adminDate(user.created_at))}</span></td>
      <td data-label="Son hareket">${activity ? `<strong>${adminEscape(adminRelativeDate(activity.created_at))}</strong><br><small>${adminEscape(activity.ip_network)} · ${adminEscape(activity.event_type)}</small>` : '<small>Yeni kayıtlarda izlenecek</small>'}</td>
      <td data-label="İşlem"><button class="admin-action" data-user-open="${adminEscape(user.id)}">Aç ve düzenle</button></td>
    </tr>`;
  }).join("");
  admin$("adminUserList").innerHTML = `<table class="admin-table admin-record-table"><thead><tr><th><input id="adminSelectVisibleUsers" type="checkbox" aria-label="Bu sayfadaki kullanıcıları seç"></th><th>Kullanıcı</th><th>Doğrulama</th><th>Plan</th><th>Dakika</th><th>Kayıt zamanı</th><th>Son hareket / ağ</th><th>İşlem</th></tr></thead><tbody>${rows || '<tr><td colspan="8">Kullanıcı bulunamadı.</td></tr>'}</tbody></table>`;
  admin$("adminUsersResultCount").textContent = `${Number(adminState.userPagination.total || 0).toLocaleString(adminLocale())} kayıt`;
  renderAdminPagination("adminUsersPagination", adminState.userPagination, page => loadAdminUsers(page));
  admin$("adminSelectVisibleUsers")?.addEventListener("change", event => {
    users.forEach(user => event.target.checked ? selectedAdminUsers.add(user.id) : selectedAdminUsers.delete(user.id));
    renderAdminUsers(users);
  });
  document.querySelectorAll("[data-user-select]").forEach(input => input.addEventListener("change", () => {
    input.checked ? selectedAdminUsers.add(input.dataset.userSelect) : selectedAdminUsers.delete(input.dataset.userSelect);
    updateAdminBulkToolbar();
  }));
  document.querySelectorAll("[data-user-open]").forEach(button => button.addEventListener("click", () => openAdminUserDialog(button.dataset.userOpen)));
  updateAdminBulkToolbar();
}

function updateAdminBulkToolbar() {
  const count = selectedAdminUsers.size;
  admin$("adminBulkToolbar").hidden = count === 0;
  admin$("adminSelectedCount").textContent = `${count.toLocaleString(adminLocale())} kullanıcı seçildi`;
}

function openAdminUserDialog(userId) {
  const user = adminState.users.find(item => item.id === userId);
  if (!user) return;
  const languages = [["tr","Türkçe"],["en","English"],["de","Deutsch"],["fr","Français"],["es","Español"],["it","Italiano"],["pt","Português"],["ru","Русский"],["ar","العربية"],["zh","中文"],["ja","日本語"],["ko","한국어"],["hi","हिन्दी"]];
  const plans = [["free","Ücretsiz"],["lite","Lite"],["plus","Plus"],["pro","Pro"],["max","Max"],["business","Business"]];
  const subscription = user.subscription || null;
  const languageOptions = languages.map(([value,label]) => `<option value="${value}" ${value === (user.preferred_language || "tr") ? "selected" : ""}>${label}</option>`).join("");
  const planOptions = plans.map(([value,label]) => `<option value="${value}" ${value === (user.plan_code || "free") ? "selected" : ""}>${label}</option>`).join("");
  const activity = user.last_activity;
  admin$("adminUserDialogTitle").textContent = user.name || user.email;
  admin$("adminUserDialogBody").innerHTML = `<div class="admin-detail-summary">
      <article><small>E-posta</small><strong>${adminEscape(user.email)}</strong></article><article><small>Hesap oluşturma</small><strong>${adminEscape(adminDate(user.created_at))}</strong></article><article><small>Son güncelleme</small><strong>${adminEscape(adminDate(user.updated_at))}</strong></article><article><small>Son güvenli ağ</small><strong>${adminEscape(activity?.ip_network || "Henüz kaydedilmedi")}</strong></article><article><small>Son hareket</small><strong>${adminEscape(activity?.created_at ? adminDate(activity.created_at) : "—")}</strong></article><article><small>Cihaz bilgisi</small><strong>${adminEscape(activity?.user_agent || "—")}</strong></article>
    </div><div class="admin-user-tools">
      <section class="admin-user-form"><h3>Yakın hesap hareketleri</h3><p>Güvenlik için tam IP tutulmaz; /24 veya /64 maskeli ağ, tek yönlü iz ve cihaz bilgisi sınırlı süre saklanır.</p><div id="adminUserActivity"><p class="empty-copy">Hareketler yükleniyor…</p></div></section>
      <form class="admin-user-form" data-user-profile-form="${adminEscape(user.id)}"><h3>Profil ve doğrulama</h3><div class="admin-form-grid"><label><span>Ad</span><input name="first_name" value="${adminEscape(user.first_name || "")}" minlength="2" maxlength="80" required></label><label><span>Soyad</span><input name="last_name" value="${adminEscape(user.last_name || "")}" minlength="2" maxlength="80" required></label><label class="wide"><span>E-posta</span><input name="email" type="email" value="${adminEscape(user.email)}" required></label><label><span>Telefon</span><input name="phone" value="${adminEscape(user.phone || "")}" maxlength="32"></label><label><span>Ülke kodu</span><input name="country_code" value="${adminEscape(user.country_code || "TR")}" minlength="2" maxlength="2" required></label><label><span>Arayüz dili</span><select name="preferred_language">${languageOptions}</select></label><label class="admin-check"><input name="email_verified" type="checkbox" ${user.email_verified ? "checked" : ""}><span>E-posta doğrulandı</span></label></div><button class="admin-action approve" type="submit">Profili kaydet</button></form>
      <form class="admin-user-form" data-user-credit-form="${adminEscape(user.id)}"><h3>Dakika bakiyesi</h3><p>Mevcut ek bakiye: ${Number(user.credit_minutes || 0).toLocaleString(adminLocale())} dk.</p><div class="admin-form-grid compact-grid"><label><span>Dakika</span><input name="minutes_delta" type="number" min="-10000" max="10000" required></label><label class="wide"><span>İşlem nedeni</span><input name="reason" minlength="4" maxlength="240" required></label></div><button class="admin-action approve" type="submit">Dakikayı uygula</button></form>
      <form class="admin-user-form" data-user-subscription-form="${adminEscape(user.id)}"><h3>Abonelik ve plan</h3><div class="admin-form-grid"><label><span>Plan</span><select name="plan_code">${planOptions}</select></label><label><span>Dönem</span><select name="interval"><option value="monthly" ${subscription?.interval !== "annual" ? "selected" : ""}>Aylık</option><option value="annual" ${subscription?.interval === "annual" ? "selected" : ""}>Yıllık</option></select></label><label><span>Erişim süresi (gün)</span><input name="duration_days" type="number" min="1" max="3660" value="${subscription?.interval === "annual" ? 365 : 30}" required></label></div><button class="admin-action approve" type="submit">Planı kaydet</button></form>
      <section class="admin-user-form admin-security-tools"><h3>Güvenlik</h3><p>Kullanıcı tüm cihazlarda yeniden giriş yapmak zorunda kalır.</p><button class="admin-action" type="button" data-user-revoke="${adminEscape(user.id)}">Tüm oturumları kapat</button></section>
      ${user.is_protected ? '<section class="admin-user-form"><h3>Korunan hesap</h3><p>Bu işletme hesabı panelden kapatılamaz.</p></section>' : `<form class="admin-user-form danger-zone" data-user-close-form="${adminEscape(user.id)}" data-user-email="${adminEscape(user.email)}"><h3>Hesabı kapat ve anonimleştir</h3><p>Onay için yalnızca SİL yaz. Bu işlem oturumları kapatır ve ders dosyalarını siler.</p><div class="admin-form-grid"><label><span>Onay</span><input name="confirmation_word" autocomplete="off" placeholder="SİL" required></label><label><span>Neden</span><input name="reason" minlength="4" maxlength="500" required></label></div><button class="admin-action reject" type="submit">Hesabı kapat</button></form>`}
    </div>`;
  const dialog = admin$("adminUserDialog");
  dialog.showModal();
  document.querySelectorAll("[data-user-profile-form]").forEach(form => form.addEventListener("submit", event => saveAdminUser(event, form)));
  document.querySelectorAll("[data-user-credit-form]").forEach(form => form.addEventListener("submit", event => adjustAdminUserCredit(event, form)));
  document.querySelectorAll("[data-user-subscription-form]").forEach(form => form.addEventListener("submit", event => saveAdminSubscription(event, form)));
  document.querySelectorAll("[data-user-revoke]").forEach(button => button.addEventListener("click", () => revokeAdminSessions(button)));
  document.querySelectorAll("[data-user-close-form]").forEach(form => form.addEventListener("submit", event => closeAdminUser(event, form)));
  void loadAdminUserActivity(user.id);
}

async function loadAdminUserActivity(userId) {
  const container = admin$("adminUserActivity");
  if (!container) return;
  try {
    const body = await adminRequest(`/billing/admin/users/${encodeURIComponent(userId)}/activity?limit=30`);
    const rows = (body.activity || []).map(item => `<tr><td data-label="Zaman"><strong>${adminEscape(adminDate(item.created_at))}</strong><br><small>${adminEscape(adminRelativeDate(item.created_at))}</small></td><td data-label="Hareket">${adminEscape(item.event_type)}</td><td data-label="Maskeli ağ">${adminEscape(item.ip_network)}<br><small>İz: ${adminEscape(item.ip_fingerprint)}</small></td><td data-label="Cihaz">${adminEscape(item.user_agent)}</td></tr>`).join("");
    container.innerHTML = `<div class="admin-table-wrap"><table class="admin-table admin-record-table"><thead><tr><th>Zaman</th><th>Hareket</th><th>Maskeli ağ</th><th>Cihaz / tarayıcı</th></tr></thead><tbody>${rows || '<tr><td colspan="4">Henüz giriş hareketi bulunmuyor.</td></tr>'}</tbody></table></div>`;
  } catch (error) {
    container.innerHTML = `<p class="empty-copy">${adminEscape(error.message)}</p>`;
  }
}

function renderAdminCreditEvents(events) {
  const rows = events.map(item => `<tr><td data-label="Tarih" title="${adminEscape(adminDate(item.created_at))}">${adminEscape(adminRelativeDate(item.created_at))}</td><td data-label="Müşteri">${adminEscape(item.email)}</td><td data-label="Dakika"><strong>${item.minutes_delta > 0 ? "+" : ""}${Number(item.minutes_delta).toLocaleString()}</strong></td><td data-label="Bakiye">${Number(item.balance_before).toLocaleString()} → ${Number(item.balance_after).toLocaleString()}</td><td data-label="Neden">${adminEscape(item.reason)}</td></tr>`).join("");
  admin$("adminCreditEvents").innerHTML = `<table class="admin-table admin-record-table"><thead><tr><th>Tarih</th><th>Müşteri</th><th>Dakika</th><th>Bakiye değişimi</th><th>Neden</th></tr></thead><tbody>${rows || '<tr><td colspan="5">Henüz yönetici dakika işlemi yok.</td></tr>'}</tbody></table>`;
}

function renderAdminAccountEvents(events) {
  const rows = events.map(item => `<tr><td data-label="Tarih" title="${adminEscape(adminDate(item.created_at))}">${adminEscape(adminRelativeDate(item.created_at))}</td><td data-label="Kullanıcı">${adminEscape(item.subject_email)}</td><td data-label="İşlem">${adminEscape(item.action)}</td><td data-label="Açıklama">${adminEscape(item.summary)}</td><td data-label="Yapan">${adminEscape(item.actor)}</td></tr>`).join("");
  admin$("adminAccountEvents").innerHTML = `<table class="admin-table admin-record-table"><thead><tr><th>Tarih</th><th>Kullanıcı</th><th>İşlem</th><th>Açıklama</th><th>Yapan</th></tr></thead><tbody>${rows || '<tr><td colspan="5">Henüz yönetici hesap işlemi yok.</td></tr>'}</tbody></table>`;
}

function renderAdminContactMessages(messages) {
  const rows = messages.map(item => `<tr><td data-label="Gönderen"><strong>${adminEscape(item.name)}</strong><br><a href="mailto:${encodeURIComponent(item.email)}">${adminEscape(item.email)}</a><br><small title="${adminEscape(adminDate(item.created_at))}">${adminEscape(adminRelativeDate(item.updated_at || item.created_at))}</small></td><td data-label="Konu"><strong>${adminEscape(item.topic)}</strong>${item.order_reference ? `<br><small>Sipariş no: ${adminEscape(item.order_reference)}</small>` : ""}</td><td data-label="Mesaj" class="admin-message-cell">${adminEscape(item.message)}</td><td data-label="Durum"><span class="status-pill ${item.status === "resolved" ? "paid" : ""}">${adminEscape(adminStatusLabel(item.status))}</span><br><small>${Number(item.reply_count || 0)} yanıt · ${item.email_notified ? "bildirim açık" : "panel kaydı"}</small></td><td data-label="İşlem"><span class="admin-actions"><button class="admin-action approve" data-contact-open="${adminEscape(item.id)}">Konuşmayı aç</button><button class="admin-action" data-contact-status="${adminEscape(item.id)}" data-status="resolved">Çöz</button></span></td></tr>`).join("");
  admin$("adminContactMessages").innerHTML = `<table class="admin-table admin-record-table"><thead><tr><th>Gönderen</th><th>Konu</th><th>Mesaj</th><th>Durum</th><th>İşlem</th></tr></thead><tbody>${rows || '<tr><td colspan="5">Henüz iletişim mesajı yok.</td></tr>'}</tbody></table>`;
  document.querySelectorAll("[data-contact-status]").forEach(button => button.addEventListener("click", () => updateContactMessage(button)));
  document.querySelectorAll("[data-contact-open]").forEach(button => button.addEventListener("click", () => openContactConversation(button.dataset.contactOpen)));
}

function renderContactConversation(conversation) {
  const message = conversation.message || {};
  const replies = conversation.replies || [];
  admin$("adminContactDialogTitle").textContent = `${message.topic || "Destek"} · ${message.name || "Kullanıcı"}`;
  const bubbles = [
    {direction:"user", sender:message.name, body:message.message, created_at:message.created_at, delivery_status:"received"},
    ...replies,
  ].map(item => `<article class="support-bubble ${item.direction === "admin" ? "outgoing" : "incoming"}"><header><strong>${adminEscape(item.direction === "admin" ? "LectureSift Destek" : message.name || "Kullanıcı")}</strong><time>${adminEscape(adminDate(item.created_at))}</time></header><p>${adminEscape(item.body).replace(/\n/g, "<br>")}</p>${item.direction === "admin" ? `<small class="delivery-${adminEscape(item.delivery_status)}">${item.delivery_status === "sent" ? "E-posta gönderildi" : item.delivery_status === "failed" ? "Gönderilemedi · yeniden yanıtla" : "Gönderiliyor"}</small>` : ""}</article>`).join("");
  admin$("adminContactDialogBody").innerHTML = `<section class="admin-contact-summary"><a href="mailto:${encodeURIComponent(message.email || "")}">${adminEscape(message.email || "")}</a>${message.order_reference ? `<span>Sipariş: ${adminEscape(message.order_reference)}</span>` : ""}<span class="status-pill ${message.status === "resolved" ? "paid" : ""}">${adminEscape(adminStatusLabel(message.status))}</span></section><section class="support-thread" aria-live="polite">${bubbles}</section><form class="admin-contact-reply" data-contact-reply-form="${adminEscape(message.id)}"><label class="field"><span>Yanıtın</span><textarea name="message" minlength="2" maxlength="4000" placeholder="Kullanıcıya gönderilecek yanıtı yaz…" required></textarea></label><div class="admin-actions"><button class="admin-action approve" type="submit">E-postayla gönder</button><button class="admin-action" type="button" data-contact-dialog-status="${adminEscape(message.id)}" data-status="${message.status === "resolved" ? "read" : "resolved"}">${message.status === "resolved" ? "Konuşmayı yeniden aç" : "Çözümlendi olarak işaretle"}</button></div><p class="empty-copy">Yanıt gönderilince kullanıcıya güvenli konuşma bağlantısı da iletilir. Gönderim sonucu burada kalıcı olarak görünür.</p></form>`;
  admin$("adminContactDialogBody").querySelector("[data-contact-reply-form]")?.addEventListener("submit", submitContactReply);
  admin$("adminContactDialogBody").querySelector("[data-contact-dialog-status]")?.addEventListener("click", async event => {
    await updateContactMessage(event.currentTarget, {refresh:false});
    await openContactConversation(message.id);
  });
  const thread = admin$("adminContactDialogBody").querySelector(".support-thread");
  if (thread) thread.scrollTop = thread.scrollHeight;
}

async function openContactConversation(messageId) {
  try {
    const body = await adminRequest(`/billing/admin/contact-messages/${encodeURIComponent(messageId)}`);
    renderContactConversation(body);
    const dialog = admin$("adminContactDialog");
    if (!dialog.open) dialog.showModal();
  } catch (error) { adminNotice(error.message, true); }
}

async function submitContactReply(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = form.querySelector('button[type="submit"]');
  const messageId = form.dataset.contactReplyForm;
  const bodyText = String(new FormData(form).get("message") || "").trim();
  submit.disabled = true;
  submit.textContent = "Gönderiliyor…";
  try {
    const body = await adminRequest(`/billing/admin/contact-messages/${encodeURIComponent(messageId)}/reply`, {method:"POST", body:JSON.stringify({message:bodyText})});
    adminNotice(body.notice);
    renderContactConversation(body);
    const item = adminState.contacts.find(value => value.id === messageId);
    if (item) Object.assign(item, body.message, {reply_count:(body.replies || []).length, last_reply_at:(body.replies || []).at(-1)?.created_at});
    applyAdminFilters();
  } catch (error) {
    adminNotice(error.message, true);
    await openContactConversation(messageId);
  } finally {
    submit.disabled = false;
    submit.textContent = "E-postayla gönder";
  }
}

function adminReadinessChecks(billing, runtime) {
  const cardReady = Boolean(billing?.payments?.iyzico?.configured || billing?.payments?.paytr?.configured);
  const iyzicoSignatureReady = Boolean(
    billing?.payments?.iyzico?.configured && billing?.payments?.iyzico?.webhook_signature?.required
  );
  return [
    {label:"Kalıcı veritabanı", ready:Boolean(billing?.database?.connected && billing?.database?.persistent), severity:"critical", detail:"Hesap, sipariş ve üyelik kayıtları", action:"Render PostgreSQL bağlantısını kontrol et"},
    {label:"E-posta doğrulama", ready:Boolean(billing?.email_delivery_configured), severity:"critical", detail:"Kayıt, kod, bağlantı ve parola sıfırlama", action:"Resend anahtarını ve gönderen alan adını kontrol et"},
    {label:"Satıcı/sağlayıcı kimliği", ready:Boolean(billing?.commerce_identity?.configured), severity:"critical", detail:"Yasal satış ve ödeme açıklamaları", action:"Zorunlu işletme bilgilerini tamamla"},
    {label:"Kartlı ödeme", ready:cardReady, severity:"critical", detail:cardReady ? `Etkin: ${billing?.payments?.iyzico?.configured ? "iyzico" : "PayTR"}` : "Kart sağlayıcısı bağlı değil", action:"Ödeme sağlayıcısı anahtarlarını kontrol et"},
    {label:"iyzico webhook imzası", ready:iyzicoSignatureReady, severity:"critical", detail:iyzicoSignatureReady ? "X-IYZ-SIGNATURE-V3 zorunlu; geçersiz bildirim reddedilir" : "Canlı v3 imza doğrulaması hazır değil", action:"iyzico anahtarlarını ve canlı webhook ayarını kontrol et"},
    {label:"iyzico Havale/EFT", ready:Boolean(billing?.payments?.bank_transfer?.configured), severity:"recommended", detail:"İmzalı webhook ile otomatik eşleşme", action:"iyzico ve webhook durumunu kontrol et"},
    {label:"Dayanıklı işleme", ready:Boolean(runtime?.durable_processing_ready), severity:"critical", detail:`Kuyruk ${runtime?.queue?.connected ? "bağlı" : "bağlı değil"} · worker ${runtime?.worker?.workers || 0} · özel depo ${runtime?.storage?.connected ? "bağlı" : "bağlı değil"}`, action:"Redis, worker ve özel dosya deposunu etkinleştir"},
    {label:"Veritabanı kurtarma", ready:Boolean(runtime?.recovery?.database_managed_backup_confirmed), severity:"planned", detail:"Yönetilen yedek doğrulaması", action:"Yedek saklama ve geri alma adımlarını belgele"},
    {label:"Dosya saklama kuralı", ready:Boolean(runtime?.recovery?.object_retention_confirmed), severity:"planned", detail:"Özel depodaki çıktıların yaşam döngüsü", action:"Özel depo açıldıktan sonra saklama kuralını doğrula"},
    {label:"Geri yükleme tatbikatı", ready:Boolean(runtime?.recovery?.restore_drill_confirmed), severity:"planned", detail:"Gerçek kurtarma testi ve kayıt tarihi", action:"Altyapı tamamlanınca kontrollü test yap"},
    {label:"Ücretsiz planda banner reklam", ready:Boolean(runtime?.display_ads_configured), severity:"optional", detail:"Ücretli planlar her durumda reklamsız", action:"AdSense yayıncı kimliğini veya Ad Manager birimini kontrol et"},
    {label:"GA4 ölçümü", ready:Boolean(runtime?.analytics_configured), severity:"recommended", detail:"İzin veren ziyaretçiler için toplu site ölçümü", action:"GA4 ölçüm kimliğini Render’da doğrula"},
    {label:"Google Ads dönüşümleri", ready:Boolean(runtime?.google_ads_conversion_configured), severity:"optional", detail:"Kayıt ve doğrulanmış satın alma dönüşümleri", action:"Google Ads hesabı ve dönüşüm etiketleri hazır olunca Render’a ekle"},
  ];
}

function renderAdminReadiness(billing, runtime) {
  const checks = adminReadinessChecks(billing, runtime);
  const stateText = item => item.ready ? "Hazır" : item.severity === "optional" ? "Opsiyonel · kapalı" : item.severity === "planned" ? "Planlandı" : item.severity === "recommended" ? "Önerilen ayar" : "Kritik eksik";
  admin$("adminReadiness").innerHTML = checks.map(item => `<article class="readiness-${item.ready ? "ready" : item.severity}"><div><span>${adminEscape(item.label)}</span><small>${adminEscape(item.detail)}</small>${!item.ready ? `<em>${adminEscape(item.action)}</em>` : ""}</div><strong class="${item.ready ? "ready" : item.severity}">${adminEscape(stateText(item))}</strong></article>`).join("");
  const payment = billing?.payments || {};
  admin$("adminPaymentSummary").innerHTML = `<article><small>Öncelikli kart sağlayıcısı</small><strong>${payment.iyzico?.configured ? "iyzico" : payment.paytr?.configured ? "PayTR" : "Bağlı değil"}</strong></article><article><small>iyzico</small><strong class="${payment.iyzico?.configured ? "ready" : "muted"}">${payment.iyzico?.configured ? "Canlı" : "Kapalı"}</strong></article><article><small>Webhook güvenliği</small><strong class="${payment.iyzico?.webhook_signature?.required ? "ready" : "muted"}">${payment.iyzico?.webhook_signature?.required ? "V3 zorunlu" : "Kapalı"}</strong></article><article><small>PayTR</small><strong class="${payment.paytr?.configured ? "ready" : "muted"}">${payment.paytr?.configured ? "Canlı" : "Opsiyonel"}</strong></article><article><small>Havale</small><strong class="${payment.bank_transfer?.configured ? "ready" : "muted"}">${payment.bank_transfer?.configured ? "Canlı" : "Kapalı"}</strong></article>`;
  return checks;
}

function renderAdminJobs(jobs) {
  const rows = jobs.map(job => `<tr><td data-label="İş"><strong>${adminEscape(job.job_id)}</strong><br><small>${adminEscape(job.owner_id ? `Hesap: ${job.owner_id.slice(0, 8)}…` : "Misafir/hesapsız")}</small></td><td data-label="Durum"><span class="status-pill ${job.status === "done" ? "paid" : ""}">${adminEscape(adminStatusLabel(job.status))}</span></td><td data-label="İlerleme"><div class="admin-progress"><span style="width:${Math.max(0, Math.min(100, Number(job.percent || 0)))}%"></span></div><small>%${Number(job.percent || 0)} · ${adminEscape(job.stage || "—")}</small></td><td data-label="Başlangıç" title="${adminEscape(adminDate(job.created))}">${adminEscape(adminRelativeDate(job.created))}</td><td data-label="Hata">${adminEscape(job.error_code || job.public_error || job.error || "—")}</td></tr>`).join("");
  admin$("adminJobs").innerHTML = `<table class="admin-table admin-record-table"><thead><tr><th>İş kimliği</th><th>Durum</th><th>İlerleme / aşama</th><th>Başlangıç</th><th>Hata</th></tr></thead><tbody>${rows || '<tr><td colspan="5">Kayıtlı iş bulunamadı.</td></tr>'}</tbody></table>`;
}

function renderAdminCosts() {
  const data = adminState.costs || {};
  const totals = data.totals || {};
  const fx = data.currency || {};
  const accuracy = data.accuracy || {};
  const economics = data.unit_economics || {};
  const percent = value => value == null ? "—" : `%${Number(value).toLocaleString(adminLocale(), {maximumFractionDigits:2})}`;
  const compactUsd = value => value == null ? "—" : adminUsd(value, 8);
  const actualNativeTotal = (data.actual_by_currency || []).map(item => `${(Number(item.total_minor || 0) / 100).toLocaleString(adminLocale(), {style:"currency", currency:item.currency})}`).join(" + ") || "—";
  const metrics = [
    ["Operasyon tahmini", adminUsd(totals.combined_usd), `${Number(totals.combined_try || 0).toLocaleString(adminLocale(), {maximumFractionDigits:2})} TL · fatura değildir`],
    ["Örtüşen fatura kayıtları", actualNativeTotal, `${adminUsd(totals.actual_invoice_usd)} güncel kurla gösterge · dönem tahminiyle kıyaslanmaz`],
    ["Değişken liste maliyeti", adminUsd(totals.variable_usd, 6), `${Number(data.period_days || 30)} günlük ölçülen kullanım`],
    ["Sabit bütçe", adminUsd(totals.period_fixed_usd), `${adminUsd(totals.period_confirmed_fixed_usd)} doğrulandı`],
    ["USD / TRY", Number(fx.usd_try || 0).toLocaleString(adminLocale(), {maximumFractionDigits:4}), fx.source || "Kur bilgisi yok"],
  ];
  admin$("adminCostMetrics").innerHTML = metrics.map(([label,value,note]) => `<article><small>${adminEscape(label)}</small><strong>${adminEscape(value)}</strong><span>${adminEscape(note)}</span></article>`).join("");
  admin$("adminCostDisclaimer").textContent = data.disclaimer || "Sağlayıcı faturaları kesin kaynaktır.";
  admin$("adminCostAccuracyBadge").textContent = `%${Number(accuracy.coverage_percent || 0).toLocaleString(adminLocale(), {maximumFractionDigits:1})} doğrulandı`;
  const accuracyCards = [
    ["Durum", accuracy.status === "verified" ? "Faturalar eşleşti" : accuracy.status === "partial" ? "Kısmen doğrulandı" : "Doğrulanmadı", accuracy.rule || ""],
    ["Aktif sağlayıcı", Number((accuracy.active_providers || []).length).toLocaleString(adminLocale()), (accuracy.active_providers || []).join(", ") || "Dönemde aktif sağlayıcı yok"],
    ["Doğrulanan", Number((accuracy.verified_providers || []).length).toLocaleString(adminLocale()), (accuracy.verified_providers || []).join(", ") || "Henüz fatura girilmedi"],
    ["Kısmi dönem", Number((accuracy.partially_verified_providers || []).length).toLocaleString(adminLocale()), (accuracy.partially_verified_providers || []).join(", ") || "Kısmi fatura kapsamı yok"],
    ["Mutabakat bekleyen", Number((accuracy.unreconciled_providers || []).length).toLocaleString(adminLocale()), (accuracy.unreconciled_providers || []).join(", ") || "Eksik sağlayıcı yok"],
    ["Sağlayıcı ölçümü", Number(accuracy.provider_reported_events || 0).toLocaleString(adminLocale()), "API tarafından bildirilen kullanım kayıtları"],
    ["Tahmini/uygulama ölçümü", Number(accuracy.estimated_events || 0).toLocaleString(adminLocale()), "Fatura değildir; ücretsiz kota ve yuvarlama farklı olabilir"],
  ];
  admin$("adminCostAccuracy").innerHTML = accuracyCards.map(([label,value,note], index) => `<article class="${index === 0 && accuracy.status === "verified" ? "ready" : accuracy.status === "unverified" ? "missing" : ""}"><header><strong>${adminEscape(label)}</strong><span>${adminEscape(value)}</span></header><p>${adminEscape(note)}</p></article>`).join("");
  const economyMetrics = [
    ["İşlenen dakika", Number(economics.processed_minutes || 0).toLocaleString(adminLocale()), "Üyelik kullanım kayıtları"],
    ["Değişken / dakika", compactUsd(economics.variable_cost_per_minute_usd), "Sadece ölçülen değişken gider"],
    ["Toplam / dakika", compactUsd(economics.operating_cost_per_minute_usd), "Sabit gider dağıtılmış tahmin"],
    ["Toplam / iş", compactUsd(economics.operating_cost_per_job_usd), `${Number(economics.costed_jobs || 0).toLocaleString(adminLocale())} maliyetlendirilmiş iş`],
    ["Bilinen gelir", adminUsd(economics.known_revenue_usd), "TRY gelir güncel kurla USD'ye çevrilir"],
    ["Komisyon öncesi katkı", adminUsd(economics.contribution_before_fees_tax_usd), percent(economics.contribution_margin_percent)],
  ];
  admin$("adminCostEconomics").innerHTML = economyMetrics.map(([label,value,note]) => `<article><small>${adminEscape(label)}</small><strong>${adminEscape(value)}</strong><span>${adminEscape(note)}</span></article>`).join("");
  admin$("adminCostEconomicsNote").textContent = economics.warning || "Bu değer muhasebe kârı değildir.";
  const providers = (data.by_provider || []).map(item => `<tr><td data-label="Sağlayıcı"><strong>${adminEscape(item.provider)}</strong></td><td data-label="Hizmet">${adminEscape(item.service)}</td><td data-label="Kayıt">${Number(item.events || 0).toLocaleString(adminLocale())}</td><td data-label="Brüt liste maliyeti">${adminEscape(adminUsd(item.cost_usd, 6))}</td></tr>`).join("");
  admin$("adminProviderCosts").innerHTML = `<table class="admin-table admin-record-table"><thead><tr><th>Sağlayıcı</th><th>Hizmet</th><th>Ölçüm kaydı</th><th>Brüt liste maliyeti</th></tr></thead><tbody>${providers || '<tr><td colspan="4">Bu dönemde ölçülmüş değişken kullanım yok.</td></tr>'}</tbody></table>`;
  const resources = (data.by_resource || []).map(item => `<tr><td data-label="Kaynak"><strong>${adminEscape(item.resource)}</strong><br><small>${adminEscape(`${item.provider} / ${item.service}`)}</small></td><td data-label="Miktar">${Number(item.quantity || 0).toLocaleString(adminLocale(), {maximumFractionDigits:6})} ${adminEscape(item.unit)}</td><td data-label="Ölçüm"><span class="admin-cost-status ${item.estimation === "provider_usage" ? "verified" : ""}">${adminEscape(item.estimation)}</span></td><td data-label="Kayıt">${Number(item.events || 0).toLocaleString(adminLocale())}</td><td data-label="Maliyet">${adminEscape(adminUsd(item.cost_usd, 6))}</td><td data-label="Fiyat kaynağı"><span class="admin-cost-source">${adminEscape(item.pricing_source)}</span><small>${adminEscape(item.pricing_effective_at || "")}</small></td></tr>`).join("");
  admin$("adminResourceCosts").innerHTML = `<table class="admin-table admin-record-table"><thead><tr><th>Kaynak / model</th><th>Miktar</th><th>Ölçüm türü</th><th>Kayıt</th><th>Brüt maliyet</th><th>Fiyat kaynağı</th></tr></thead><tbody>${resources || '<tr><td colspan="6">Kaynak bazlı kullanım oluşmadı.</td></tr>'}</tbody></table>`;
  const jobs = (data.jobs || []).map(item => `<tr><td data-label="İş kimliği"><strong>${adminEscape(item.job_id)}</strong></td><td data-label="İlk kullanım">${adminEscape(adminDate(item.started_at))}</td><td data-label="Ölçüm">${Number(item.events || 0).toLocaleString(adminLocale())}</td><td data-label="USD">${adminEscape(adminUsd(item.cost_usd, 6))}</td><td data-label="TRY">${Number(item.cost_try || 0).toLocaleString(adminLocale(), {style:"currency",currency:"TRY",maximumFractionDigits:4})}</td></tr>`).join("");
  admin$("adminJobCosts").innerHTML = `<table class="admin-table admin-record-table"><thead><tr><th>İş kimliği</th><th>İlk kullanım</th><th>Ölçüm</th><th>USD</th><th>TRY</th></tr></thead><tbody>${jobs || '<tr><td colspan="5">Henüz iş bazlı maliyet oluşmadı.</td></tr>'}</tbody></table>`;
  admin$("adminFixedCosts").innerHTML = (data.fixed_services || []).map(item => `<article class="${item.confirmed ? "ready" : "missing"}"><header><strong>${adminEscape(item.provider)}</strong><span>${item.confirmed ? "Doğrulandı" : "Doğrulama gerekli"}</span></header><p>${adminEscape(item.label)} · ${adminEscape(adminUsd(item.monthly_usd))}/ay</p><code>${adminEscape(item.configuration_key)} + ${adminEscape(item.confirmation_key)}</code></article>`).join("");
  const actuals = (data.actual_costs || []).map(item => `<tr><td data-label="Sağlayıcı"><strong>${adminEscape(item.provider)}</strong><br><small>${adminEscape(item.service)}</small></td><td data-label="Dönem">${adminEscape(item.period_start)} – ${adminEscape(item.period_end)}</td><td data-label="Tutar">${(Number(item.total_minor || 0) / 100).toLocaleString(adminLocale(), {style:"currency",currency:item.currency})}<br><small>Vergi: ${(Number(item.tax_minor || 0) / 100).toLocaleString(adminLocale(), {style:"currency",currency:item.currency})}</small></td><td data-label="USD karşılığı">${adminEscape(adminUsd(item.total_usd, 4))}</td><td data-label="Kaynak">${adminEscape(item.label)}<br><small>${adminEscape(item.source_reference)}</small></td><td data-label="İşlem"><button type="button" class="admin-action reject" data-delete-actual-cost="${adminEscape(item.id)}">Sil</button></td></tr>`).join("");
  admin$("adminActualCostCount").textContent = `${Number((data.actual_costs || []).length).toLocaleString(adminLocale())} kayıt`;
  admin$("adminActualCosts").innerHTML = `<table class="admin-table admin-record-table"><thead><tr><th>Sağlayıcı</th><th>Dönem</th><th>Fatura toplamı</th><th>USD karşılığı</th><th>Referans</th><th>İşlem</th></tr></thead><tbody>${actuals || '<tr><td colspan="6">Bu dönemle örtüşen doğrulanmış fatura/mutabakat kaydı yok.</td></tr>'}</tbody></table>`;
  admin$("adminExternalCosts").innerHTML = (data.external_invoice_sources || []).map(item => `<article class="missing"><header><strong>${adminEscape(item.provider)}</strong><span>Fatura / mutabakat</span></header><p>${adminEscape(item.label)}</p><small>${adminEscape(item.reason)}</small></article>`).join("");
}

async function loadAdminCosts() {
  const days = Number(admin$("adminCostDays")?.value || 30);
  adminState.costs = await adminRequest(`/billing/admin/costs?days=${encodeURIComponent(days)}&limit=250`);
  renderAdminCosts();
}

async function saveAdminActualCost(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = form.querySelector('button[type="submit"]');
  const values = new FormData(form);
  const toMinor = name => Math.round(Number(values.get(name) || 0) * 100);
  submit.disabled = true;
  try {
    const body = await adminRequest("/billing/admin/costs/actuals", {
      method:"POST",
      body:JSON.stringify({
        provider:String(values.get("provider") || "").trim(),
        service:String(values.get("service") || "").trim(),
        period_start:String(values.get("period_start") || ""),
        period_end:String(values.get("period_end") || ""),
        currency:String(values.get("currency") || "USD"),
        subtotal_minor:toMinor("subtotal"),
        tax_minor:toMinor("tax"),
        label:String(values.get("label") || "").trim(),
        source_reference:String(values.get("source_reference") || "").trim(),
      }),
    });
    adminNotice(body.message);
    form.reset();
    form.querySelector('[name="tax"]').value = "0";
    await loadAdminCosts();
  } catch (error) { adminNotice(error.message, true); }
  finally { submit.disabled = false; }
}

async function deleteAdminActualCost(button) {
  if (!window.confirm("Bu fatura/mutabakat gideri silinsin mi?")) return;
  button.disabled = true;
  try {
    const body = await adminRequest(`/billing/admin/costs/actuals/${encodeURIComponent(button.dataset.deleteActualCost)}`, {method:"DELETE"});
    adminNotice(body.message);
    await loadAdminCosts();
  } catch (error) { adminNotice(error.message, true); button.disabled = false; }
}

function buildTimeline() {
  const events = [];
  (adminState.orders.length ? adminState.orders : (adminState.overview.orders || [])).forEach(item => events.push({kind:"order", at:item.created_at, title:`${item.payment_method === "bank_transfer" ? (item.provider === "iyzico" ? "iyzico Korumalı Havale/EFT" : "Manuel havale") : item.payment_method === "unknown" ? "Eski iyzico" : String(item.provider || "Kart").toUpperCase()} siparişi`, detail:`${item.reference} · ${adminMoney(item.amount_minor, item.currency)} · ${adminStatusLabel(item.status)}`, actor:item.user?.email || ""}));
  (adminState.contacts || []).forEach(item => events.push({kind:"contact", at:item.created_at, title:`Destek mesajı: ${item.topic}`, detail:item.message, actor:item.email}));
  (adminState.refunds || []).forEach(item => events.push({kind:"refund", at:item.created_at, title:`İade talebi · ${adminStatusLabel(item.status)}`, detail:`${item.order_reference} · ${item.reason}`, actor:item.user?.email || ""}));
  (adminState.rewards || []).forEach(item => events.push({kind:"reward", at:item.created_at, title:`Instagram bonusu · ${adminStatusLabel(item.status)}`, detail:`@${item.handle} · +${item.minutes} dk`, actor:item.email || ""}));
  (adminState.credits || []).forEach(item => events.push({kind:"credit", at:item.created_at, title:`Dakika işlemi ${item.minutes_delta > 0 ? "+" : ""}${item.minutes_delta}`, detail:item.reason, actor:item.email || ""}));
  (adminState.users.length ? adminState.users : (adminState.overview.users || [])).forEach(item => events.push({kind:"user", at:item.created_at, title:"Yeni kullanıcı hesabı", detail:item.email_verified ? "E-posta doğrulandı" : "E-posta doğrulaması bekliyor", actor:item.email}));
  (adminState.jobs || []).forEach(item => events.push({kind:"job", at:item.updated || item.created, title:`İşleme işi · ${adminStatusLabel(item.status)}`, detail:`${item.job_id} · %${Number(item.percent || 0)} · ${item.stage || "—"}`, actor:item.owner_id ? `${item.owner_id.slice(0, 8)}…` : ""}));
  return events.sort((a, b) => adminDateObject(b.at) - adminDateObject(a.at)).slice(0, 100);
}

function renderAdminTimeline() {
  const selected = admin$("adminTimelineFilter")?.value || "all";
  const events = buildTimeline().filter(item => selected === "all" || item.kind === selected);
  const icons = {order:"₺", contact:"✉", refund:"↩", reward:"◎", credit:"+", user:"●", job:"▶"};
  admin$("adminTimeline").innerHTML = events.map(item => `<article><span class="admin-timeline-icon kind-${adminEscape(item.kind)}">${icons[item.kind] || "•"}</span><div><strong>${adminEscape(item.title)}</strong><p>${adminEscape(item.detail)}</p>${item.actor ? `<small>${adminEscape(item.actor)}</small>` : ""}</div><time datetime="${adminEscape(String(item.at))}" title="${adminEscape(adminDate(item.at))}">${adminEscape(adminRelativeDate(item.at))}<small>${adminEscape(adminDate(item.at))}</small></time></article>`).join("") || '<p class="empty-copy">Bu filtrede hareket bulunamadı.</p>';
}

function renderAdminAlerts(checks) {
  const critical = checks.filter(item => !item.ready && item.severity === "critical");
  const newMessages = adminState.contacts.filter(item => item.status === "new").length;
  const openRefunds = adminState.refunds.filter(item => ["requested", "approved_pending_refund"].includes(item.status)).length;
  const failedJobs = adminState.jobs.filter(item => item.status === "failed").length;
  const alerts = [];
  if (critical.length) alerts.push({level:"critical", title:`${critical.length} kritik altyapı işi`, detail:critical.map(item => item.label).join(" · ")});
  if (Number(adminState.overview.counts?.pending_orders || 0)) alerts.push({level:"attention", title:`${adminState.overview.counts.pending_orders} ödeme bekliyor`, detail:"Havale dekontlarını veya yarım kalan kart işlemlerini incele."});
  if (newMessages) alerts.push({level:"attention", title:`${newMessages} yeni destek mesajı`, detail:"Yanıt bekleyen mesajları destek bölümünden aç."});
  if (openRefunds) alerts.push({level:"attention", title:`${openRefunds} açık iade talebi`, detail:"Sağlayıcı üzerinden para gönderildikten sonra tamamlandı olarak işaretle."});
  if (failedJobs) alerts.push({level:"critical", title:`${failedJobs} hatalı işleme işi`, detail:"Hata kodunu işleme işleri tablosundan incele."});
  if (!alerts.length) alerts.push({level:"ok", title:"Acil operasyon uyarısı yok", detail:"Kritik servisler ve bekleyen işlemler normal görünüyor."});
  admin$("adminAlerts").innerHTML = alerts.map(item => `<article class="${item.level}"><strong>${adminEscape(item.title)}</strong><span>${adminEscape(item.detail)}</span></article>`).join("");
}

function renderMetrics() {
  const counts = adminState.overview.counts || {};
  const orders = adminState.overview.orders || [];
  const users = adminState.overview.users || [];
  const newMessages = adminState.contacts.filter(item => item.status === "new").length;
  const openRefunds = adminState.refunds.filter(item => ["requested", "approved_pending_refund"].includes(item.status)).length;
  const activeJobs = adminState.jobs.filter(item => ["queued", "working"].includes(item.status)).length;
  const failedJobs = adminState.jobs.filter(item => item.status === "failed").length;
  const revenueMap = {...(adminState.overview.revenue_by_currency || {})};
  if (!Object.keys(revenueMap).length) orders.filter(item => item.status === "paid").forEach(item => { const currency = item.currency || "TRY"; revenueMap[currency] = Number(revenueMap[currency] || 0) + Number(item.amount_minor || 0); });
  const revenues = Object.entries(revenueMap).map(([currency, amount]) => adminMoney(amount, currency));
  const verifiedUsers = Number(counts.verified_users || 0);
  const totalUsers = Number(counts.users || 0);
  const verificationRate = adminState.overview.verification_rate ?? (totalUsers ? verifiedUsers / totalUsers * 100 : 0);
  const users24h = counts.users_24h ?? users.filter(item => Date.now() - adminDateObject(item.created_at).getTime() <= 86400000).length;
  admin$("adminUsers").textContent = counts.users || 0;
  admin$("adminUsersGrowth").textContent = `Son 7 gün: ${counts.users_7d || 0} · 30 gün: ${counts.users_30d || 0}`;
  admin$("adminVerified").textContent = counts.verified_users || 0;
  admin$("adminVerificationRate").textContent = `%${Number(verificationRate || 0).toLocaleString(adminLocale())} doğrulama`;
  admin$("adminPaidOrders").textContent = counts.paid_orders ?? orders.filter(item => item.status === "paid").length;
  admin$("adminRevenue").textContent = revenues.join(" + ") || adminMoney(0, "TRY");
  admin$("adminPending").textContent = counts.pending_orders || 0;
  admin$("adminFailedOrders").textContent = `Hatalı: ${counts.failed_orders || 0}`;
  admin$("adminSubscriptions").textContent = counts.active_subscriptions || 0;
  admin$("adminNewMessages").textContent = newMessages;
  admin$("adminOpenRefunds").textContent = `Açık iade: ${openRefunds}`;
  admin$("adminActiveJobs").textContent = activeJobs;
  admin$("adminFailedJobs").textContent = `Hatalı: ${failedJobs}`;
  admin$("adminUsers24h").textContent = users24h;
  admin$("adminRefundBadge").textContent = `${openRefunds} açık`;
  admin$("adminRewardBadge").textContent = `${adminState.rewards.filter(item => item.status === "pending_verification").length} bekliyor`;
  renderPlanDistribution();
}

function renderPlanDistribution() {
  const distribution = adminState.overview.plan_distribution || {};
  const entries = Object.entries(distribution).sort((left, right) => Number(right[1]) - Number(left[1]));
  const total = Math.max(1, entries.reduce((sum, [, count]) => sum + Number(count || 0), 0));
  admin$("adminPlanDistribution").innerHTML = entries.map(([plan, count]) => {
    const percent = Math.round(Number(count || 0) / total * 100);
    return `<article><div><strong>${adminEscape(plan)}</strong><span>${Number(count).toLocaleString(adminLocale())} hesap · %${percent}</span></div><i><b style="width:${percent}%"></b></i></article>`;
  }).join("") || '<p class="empty-copy">Henüz plan verisi yok.</p>';
}

function renderAdminGrowth() {
  const ads = adminState.ads || {};
  const analytics = adminState.analytics || {};
  const cards = [
    {title:"LectureSift kampanya bannerı", ready:Boolean(ads.house_campaign?.enabled), detail:ads.house_campaign?.enabled ? "Birinci taraf kampanya bannerı yayına hazır." : "İç kampanya kapalı.", key:"LECTURESIFT_SITE_BANNER_ENABLED"},
    {title:"Google AdSense Auto ads", ready:Boolean(ads.adsense_auto_ads?.enabled), detail:ads.adsense_auto_ads?.enabled ? "Yayıncı kimliği bağlı; reklam izni veren ücretsiz ziyaretçilerde Auto ads kodu yüklenir." : "Geçerli AdSense yayıncı kimliği bekleniyor.", key:"LECTURESIFT_ADSENSE_PUBLISHER_ID"},
    {title:"Google banner reklamı", ready:Boolean(ads.enabled), detail:ads.enabled ? "Google GPT yayın birimi etkin." : "Geçerli Google Ad Manager banner yayın birimi bekleniyor.", key:"LECTURESIFT_DISPLAY_ADS_ENABLED + LECTURESIFT_DISPLAY_AD_UNIT_PATH"},
    {title:"Reklam karşılığı dakika", ready:Boolean(adminState.runtime?.rewarded_ads_configured), detail:adminState.runtime?.rewarded_ads_configured ? "Ödüllü reklam ve günlük dakika sınırı etkin." : "Google Ad Manager ödüllü reklam yayın birimi bekleniyor.", key:"LECTURESIFT_REWARDED_ADS_ENABLED + LECTURESIFT_REWARDED_AD_UNIT_PATH"},
    {title:"GA4 ölçümü", ready:Boolean(analytics.enabled), detail:analytics.enabled ? `Ölçüm etkin: ${analytics.measurement_id}` : "Geçerli GA4 ölçüm kimliği bekleniyor.", key:"LECTURESIFT_ANALYTICS_ENABLED + LECTURESIFT_GA_MEASUREMENT_ID"},
    {title:"Google Ads dönüşümleri", ready:Boolean(analytics.google_ads?.enabled), detail:analytics.google_ads?.enabled ? "Kayıt ve satın alma dönüşümleri etkin." : "Google Ads kimliği ve iki dönüşüm etiketi bekleniyor.", key:"LECTURESIFT_GOOGLE_ADS_ID + SIGNUP_LABEL + PURCHASE_LABEL"},
    {title:"Ücretli planlarda reklamsız", ready:true, detail:"Ücretli planların ad_free hakkı uygulanıyor; reklam yalnızca uygun ücretsiz hesaplarda gösterilir.", key:"Plan hakları"},
  ];
  admin$("adminGrowthStatus").innerHTML = cards.map(item => `<article class="${item.ready ? "ready" : "missing"}"><header><strong>${adminEscape(item.title)}</strong><span>${item.ready ? "Hazır" : "Eksik ayar"}</span></header><p>${adminEscape(item.detail)}</p><code>${adminEscape(item.key)}</code></article>`).join("");
}

function userQuery(page = 1) {
  const params = new URLSearchParams({
    search:admin$("adminUserSearch")?.value.trim() || "",
    verification:admin$("adminUserStatus")?.value || "all",
    plan:admin$("adminUserPlan")?.value || "all",
    sort:admin$("adminUserSort")?.value || "created_desc",
    page:String(page),
    page_size:admin$("adminUserPageSize")?.value || "50",
  });
  return params.toString();
}

async function loadAdminUsers(page = 1) {
  const body = await adminRequest(`/billing/admin/users?${userQuery(page)}`);
  const pagination = body.pagination || {page,total:0,total_pages:1};
  if (Number(pagination.page) > Number(pagination.total_pages || 1)) {
    return loadAdminUsers(Number(pagination.total_pages || 1));
  }
  adminState.users = body.users || [];
  adminState.userPagination = pagination;
  renderAdminUsers(adminState.users);
}

function orderQuery(page = 1) {
  const params = new URLSearchParams({
    search:admin$("adminOrderSearch")?.value.trim() || "",
    status:admin$("adminOrderStatus")?.value || "all",
    provider:admin$("adminOrderProvider")?.value || "all",
    page:String(page),
    page_size:admin$("adminOrderPageSize")?.value || "50",
  });
  return params.toString();
}

async function loadAdminOrders(page = 1) {
  const body = await adminRequest(`/billing/admin/orders?${orderQuery(page)}`);
  const pagination = body.pagination || {page,total:0,total_pages:1};
  if (Number(pagination.page) > Number(pagination.total_pages || 1)) {
    return loadAdminOrders(Number(pagination.total_pages || 1));
  }
  adminState.orders = body.orders || [];
  adminState.orderPagination = pagination;
  renderAdminOrders(adminState.orders);
}

function normalizeSearch(value) { return String(value || "").toLocaleLowerCase("tr-TR"); }

function applyAdminFilters() {
  const messageQuery = normalizeSearch(admin$("adminMessageSearch")?.value);
  const messageStatus = admin$("adminMessageStatus")?.value || "all";
  renderAdminContactMessages(adminState.contacts.filter(item => (messageStatus === "all" || item.status === messageStatus) && normalizeSearch(`${item.name} ${item.email} ${item.topic} ${item.order_reference} ${item.message}`).includes(messageQuery)));
  const jobStatus = admin$("adminJobStatus")?.value || "all";
  renderAdminJobs(adminState.jobs.filter(item => jobStatus === "all" || item.status === jobStatus));
  renderAdminTimeline();
}

function downloadAdminCsv(filename, rows) {
  if (!rows.length) return adminNotice("Dışa aktarılacak kayıt bulunamadı.", true);
  const headers = Object.keys(rows[0]);
  const safeCell = value => {
    let text = value == null ? "" : String(value);
    if (/^[=+\-@]/.test(text)) text = `'${text}`;
    return `"${text.replace(/"/g, '""')}"`;
  };
  const csv = `\uFEFF${headers.map(safeCell).join(",")}\n${rows.map(row => headers.map(key => safeCell(row[key])).join(",")).join("\n")}`;
  const url = URL.createObjectURL(new Blob([csv], {type:"text/csv;charset=utf-8"}));
  const link = document.createElement("a"); link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url);
}

async function loadAdmin({silent = false} = {}) {
  if (adminLoading) return;
  adminLoading = true;
  admin$("adminRefresh").disabled = true;
  try {
    const overview = await adminRequest("/billing/admin/overview?limit=250");
    const optional = await Promise.all([
      adminRequest("/admin/instagram-rewards?status=").catch(() => ({rewards:[]})),
      adminRequest("/billing/admin/refund-requests").catch(() => ({requests:[]})),
      adminRequest("/billing/admin/credit-events?limit=250").catch(() => ({events:[]})),
      adminRequest("/billing/admin/contact-messages?limit=250").catch(() => ({messages:[]})),
      adminRequest("/billing/admin/jobs?limit=250").catch(() => ({jobs:[], counts:{}})),
      adminRequest("/billing/admin/account-events?limit=250").catch(() => ({events:[]})),
      adminPublicRequest("/billing/health"), adminPublicRequest("/rollout/health"),
      adminPublicRequest("/ads/config"), adminPublicRequest("/analytics/config"),
      adminRequest(`/billing/admin/costs?days=${encodeURIComponent(admin$("adminCostDays")?.value || 30)}&limit=250`).catch(() => null),
    ]);
    adminState = {
      ...adminState,
      overview,
      rewards:optional[0].rewards || [],
      refunds:optional[1].requests || [],
      credits:optional[2].events || [],
      contacts:optional[3].messages || [],
      jobs:optional[4].jobs || [],
      accountEvents:optional[5].events || [],
      billing:optional[6],
      runtime:optional[7],
      ads:optional[8],
      analytics:optional[9],
      costs:optional[10],
    };
    await Promise.all([
      loadAdminUsers(adminState.userPagination.page || 1),
      loadAdminOrders(adminState.orderPagination.page || 1),
    ]);
    renderMetrics();
    renderAdminRewards(adminState.rewards.filter(item => item.status === "pending_verification"));
    renderAdminRefunds(adminState.refunds);
    renderAdminCreditEvents(adminState.credits);
    renderAdminAccountEvents(adminState.accountEvents);
    applyAdminFilters();
    const checks = renderAdminReadiness(adminState.billing, adminState.runtime);
    renderAdminAlerts(checks);
    renderAdminGrowth();
    renderAdminCosts();
    admin$("adminLastUpdated").textContent = `Son güncelleme: ${adminDate(new Date().toISOString())} · Türkiye saati`;
    admin$("adminLogin").hidden = true;
    admin$("adminPanel").hidden = false;
    if (!silent) admin$("adminNotice").hidden = true;
  } finally {
    adminLoading = false;
    admin$("adminRefresh").disabled = false;
  }
}

async function decideOrder(button) {
  button.disabled = true;
  try { await adminRequest(`/admin/manual-orders/${encodeURIComponent(button.dataset.orderDecision)}/decision`, {method:"POST", body:JSON.stringify({approve:button.dataset.approve === "1"})}); await loadAdmin(); }
  catch (error) { adminNotice(error.message, true); button.disabled = false; }
}

async function decideReward(button) {
  button.disabled = true;
  try { await adminRequest(`/admin/instagram-rewards/${encodeURIComponent(button.dataset.rewardDecision)}/decision`, {method:"POST", body:JSON.stringify({approve:button.dataset.approve === "1"})}); await loadAdmin(); }
  catch (error) { adminNotice(error.message, true); button.disabled = false; }
}

async function decideRefund(button) {
  const requestId = button.dataset.refundDecision;
  const note = document.querySelector(`[data-refund-note="${CSS.escape(requestId)}"]`)?.value || "";
  button.disabled = true;
  try { await adminRequest(`/billing/admin/refund-requests/${encodeURIComponent(requestId)}/decision`, {method:"POST", body:JSON.stringify({action:button.dataset.action, note})}); await loadAdmin(); }
  catch (error) { adminNotice(error.message, true); button.disabled = false; }
}

async function adjustCredit(button) {
  const userId = button.dataset.userCredit;
  const delta = Number(document.querySelector(`[data-credit-delta="${CSS.escape(userId)}"]`)?.value || 0);
  const reason = document.querySelector(`[data-credit-reason="${CSS.escape(userId)}"]`)?.value?.trim() || "";
  button.disabled = true;
  try { const body = await adminRequest(`/billing/admin/users/${encodeURIComponent(userId)}/credit-adjustment`, {method:"POST", body:JSON.stringify({minutes_delta:delta, reason})}); adminNotice(body.message); await loadAdmin({silent:true}); }
  catch (error) { adminNotice(error.message, true); button.disabled = false; }
}

async function saveAdminUser(event, form) {
  event.preventDefault();
  const userId = form.dataset.userProfileForm;
  const submit = form.querySelector('button[type="submit"]');
  const data = new FormData(form);
  submit.disabled = true;
  try {
    const body = await adminRequest(`/billing/admin/users/${encodeURIComponent(userId)}`, {
      method:"PATCH",
      body:JSON.stringify({
        email:String(data.get("email") || "").trim(),
        first_name:String(data.get("first_name") || "").trim(),
        last_name:String(data.get("last_name") || "").trim(),
        phone:String(data.get("phone") || "").trim(),
        country_code:String(data.get("country_code") || "TR").trim().toUpperCase(),
        preferred_language:String(data.get("preferred_language") || "tr"),
        email_verified:data.get("email_verified") === "on",
      }),
    });
    adminNotice(body.message);
    admin$("adminUserDialog")?.close();
    await loadAdmin({silent:true});
  } catch (error) { adminNotice(error.message, true); submit.disabled = false; }
}

async function adjustAdminUserCredit(event, form) {
  event.preventDefault();
  const userId = form.dataset.userCreditForm;
  const submit = form.querySelector('button[type="submit"]');
  const data = new FormData(form);
  submit.disabled = true;
  try {
    const body = await adminRequest(`/billing/admin/users/${encodeURIComponent(userId)}/credit-adjustment`, {
      method:"POST",
      body:JSON.stringify({minutes_delta:Number(data.get("minutes_delta") || 0), reason:String(data.get("reason") || "").trim()}),
    });
    adminNotice(body.message);
    admin$("adminUserDialog")?.close();
    await loadAdmin({silent:true});
  } catch (error) { adminNotice(error.message, true); submit.disabled = false; }
}

async function saveAdminSubscription(event, form) {
  event.preventDefault();
  const userId = form.dataset.userSubscriptionForm;
  const submit = form.querySelector('button[type="submit"]');
  const data = new FormData(form);
  const planCode = String(data.get("plan_code") || "free");
  const confirmation = planCode === "free"
    ? "Aktif ücretli abonelik hemen kapatılacak ve kullanıcı ücretsiz plana geçirilecek. Devam edilsin mi?"
    : `${planCode.toUpperCase()} planı kullanıcıya hemen atanacak. Devam edilsin mi?`;
  if (!window.confirm(confirmation)) return;
  submit.disabled = true;
  try {
    const body = await adminRequest(`/billing/admin/users/${encodeURIComponent(userId)}/subscription`, {
      method:"POST",
      body:JSON.stringify({plan_code:planCode, interval:String(data.get("interval") || "monthly"), duration_days:Number(data.get("duration_days") || 30)}),
    });
    adminNotice(body.message);
    admin$("adminUserDialog")?.close();
    await loadAdmin({silent:true});
  } catch (error) { adminNotice(error.message, true); submit.disabled = false; }
}

async function revokeAdminSessions(button) {
  if (!window.confirm("Bu kullanıcının tüm cihazlardaki oturumları kapatılsın mı?")) return;
  button.disabled = true;
  try {
    const body = await adminRequest(`/billing/admin/users/${encodeURIComponent(button.dataset.userRevoke)}/revoke-sessions`, {method:"POST", body:"{}"});
    adminNotice(body.message);
    admin$("adminUserDialog")?.close();
    await loadAdmin({silent:true});
  } catch (error) { adminNotice(error.message, true); button.disabled = false; }
}

async function closeAdminUser(event, form) {
  event.preventDefault();
  const userId = form.dataset.userCloseForm;
  const submit = form.querySelector('button[type="submit"]');
  const data = new FormData(form);
  const confirmation = String(data.get("confirmation_word") || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("tr-TR").trim();
  const email = String(form.dataset.userEmail || "").trim();
  if (confirmation !== "sil") return adminNotice("Hesabı kapatmak için onay alanına SİL yaz.", true);
  if (!window.confirm(`${email} hesabı kapatılacak, oturumları iptal edilecek ve ders dosyaları silinecek. Bu işlem geri alınamaz. Devam edilsin mi?`)) return;
  submit.disabled = true;
  try {
    const body = await adminRequest(`/billing/admin/users/${encodeURIComponent(userId)}`, {
      method:"DELETE",
      body:JSON.stringify({confirmation_email:email, reason:String(data.get("reason") || "").trim()}),
    });
    adminNotice(body.message);
    admin$("adminUserDialog")?.close();
    await loadAdmin({silent:true});
  } catch (error) { adminNotice(error.message, true); submit.disabled = false; }
}

function syncAdminBulkFields() {
  const action = admin$("adminBulkAction").value;
  admin$("adminBulkMinutes").hidden = action !== "credit";
  admin$("adminBulkPlan").hidden = action !== "subscription";
  admin$("adminBulkReason").hidden = !["credit", "delete"].includes(action);
  admin$("adminBulkConfirmation").hidden = action !== "delete";
  admin$("adminBulkApply").classList.toggle("reject", action === "delete");
  admin$("adminBulkApply").classList.toggle("approve", action !== "delete");
}

async function applyAdminBulkAction() {
  const ids = [...selectedAdminUsers];
  if (!ids.length) return adminNotice("En az bir kullanıcı seç.", true);
  const action = admin$("adminBulkAction").value;
  const confirmation = admin$("adminBulkConfirmation").value.trim();
  if (action === "delete" && confirmation.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("tr-TR") !== "sil") {
    return adminNotice("Toplu hesap kapatma için SİL yaz.", true);
  }
  const labels = {credit:"dakika bakiyesi güncellenecek", subscription:"plan atanacak", revoke_sessions:"tüm oturumlar kapatılacak", delete:"hesaplar kapatılıp anonimleştirilecek"};
  if (!window.confirm(`${ids.length} kullanıcı için ${labels[action]}. Devam edilsin mi?`)) return;
  const button = admin$("adminBulkApply");
  button.disabled = true;
  try {
    const body = await adminRequest("/billing/admin/users/bulk-action", {
      method:"POST",
      body:JSON.stringify({
        user_ids:ids,
        action,
        confirmation,
        reason:admin$("adminBulkReason").value.trim(),
        minutes_delta:Number(admin$("adminBulkMinutes").value || 0),
        plan_code:admin$("adminBulkPlan").value,
        interval:"monthly",
        duration_days:30,
      }),
    });
    selectedAdminUsers.clear();
    adminNotice(body.message, Boolean(body.failed));
    await loadAdmin({silent:true});
  } catch (error) { adminNotice(error.message, true); }
  finally { button.disabled = false; }
}

async function updateContactMessage(button, {refresh = true} = {}) {
  button.disabled = true;
  const messageId = button.dataset.contactStatus || button.dataset.contactDialogStatus;
  try { await adminRequest(`/billing/admin/contact-messages/${encodeURIComponent(messageId)}/status`, {method:"POST", body:JSON.stringify({status:button.dataset.status})}); if (refresh) await loadAdmin(); }
  catch (error) { adminNotice(error.message, true); button.disabled = false; }
}

setupAdminNavigation();
admin$("adminTokenForm").addEventListener("submit", async event => {
  event.preventDefault(); adminAccessToken = admin$("adminToken").value.trim();
  try { await loadAdmin(); sessionStorage.setItem(ADMIN_SESSION_TOKEN_KEY, adminAccessToken); }
  catch (error) { sessionStorage.removeItem(ADMIN_SESSION_TOKEN_KEY); adminAccessToken = ""; adminNotice(error.message, true); }
});
admin$("adminRefresh").addEventListener("click", () => loadAdmin().catch(error => adminNotice(error.message, true)));
["adminMessageSearch","adminMessageStatus","adminJobStatus","adminTimelineFilter"].forEach(id => admin$(id)?.addEventListener(id.includes("Search") ? "input" : "change", applyAdminFilters));
admin$("adminCostDays")?.addEventListener("change", () => loadAdminCosts().catch(error => adminNotice(error.message, true)));
admin$("adminActualCostForm")?.addEventListener("submit", saveAdminActualCost);
admin$("adminActualCosts")?.addEventListener("click", event => {
  const button = event.target.closest("[data-delete-actual-cost]");
  if (button) deleteAdminActualCost(button);
});
admin$("adminUserSearch").addEventListener("input", () => { clearTimeout(adminUserSearchTimer); adminUserSearchTimer = setTimeout(() => loadAdminUsers(1).catch(error => adminNotice(error.message, true)), 350); });
["adminUserStatus","adminUserPlan","adminUserSort","adminUserPageSize"].forEach(id => admin$(id)?.addEventListener("change", () => loadAdminUsers(1).catch(error => adminNotice(error.message, true))));
admin$("adminOrderSearch").addEventListener("input", () => { clearTimeout(adminOrderSearchTimer); adminOrderSearchTimer = setTimeout(() => loadAdminOrders(1).catch(error => adminNotice(error.message, true)), 350); });
["adminOrderStatus","adminOrderProvider","adminOrderPageSize"].forEach(id => admin$(id)?.addEventListener("change", () => loadAdminOrders(1).catch(error => adminNotice(error.message, true))));
admin$("adminBulkAction").addEventListener("change", syncAdminBulkFields);
admin$("adminBulkApply").addEventListener("click", applyAdminBulkAction);
admin$("adminClearSelection").addEventListener("click", () => { selectedAdminUsers.clear(); renderAdminUsers(adminState.users); });
syncAdminBulkFields();
admin$("adminExportOrders").addEventListener("click", () => downloadAdminCsv("lecturesift-siparisler.csv", adminState.orders.map(item => ({siparis_no:item.order_number || item.reference, olusturma_zamani:item.created_at, son_guncelleme:item.updated_at, musteri:item.user?.name || "", eposta:item.user?.email || "", odeme_yontemi:item.payment_method, saglayici:item.provider, plan:item.plan_code, donem:item.interval, tutar_minor:item.amount_minor, para_birimi:item.currency, durum:item.status, guvenli_ag:item.user?.last_activity?.ip_network || ""}))));
admin$("adminExportMessages").addEventListener("click", () => downloadAdminCsv("lecturesift-mesajlar.csv", adminState.contacts.map(item => ({tarih:item.created_at, ad_soyad:item.name, eposta:item.email, konu:item.topic, siparis_no:item.order_reference || "", durum:item.status, mesaj:item.message}))));
admin$("adminExportUsers").addEventListener("click", () => downloadAdminCsv("lecturesift-kullanicilar.csv", adminState.users.map(item => ({kayit_tarihi:item.created_at, son_guncelleme:item.updated_at, ad_soyad:item.name, eposta:item.email, telefon:item.phone || "", ulke:item.country_code || "", eposta_dogrulandi:item.email_verified ? "evet" : "hayir", plan:item.plan_code || "free", kredi_dakika:item.credit_minutes, son_guvenli_ag:item.last_activity?.ip_network || ""}))));
setInterval(() => { if (adminAccessToken && admin$("adminAutoRefresh").checked && document.visibilityState === "visible") loadAdmin({silent:true}).catch(() => {}); }, 60000);
if (adminAccessToken) loadAdmin().catch(() => { sessionStorage.removeItem(ADMIN_SESSION_TOKEN_KEY); adminAccessToken = ""; admin$("adminLogin").hidden = false; });
