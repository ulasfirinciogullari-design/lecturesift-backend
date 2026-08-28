const ADMIN_API = "https://lecturesift-backend.onrender.com";
const admin$ = id => document.getElementById(id);
const adminT = (key, fallback) => window.LectureSiftI18n?.t(key) || fallback || key;
let adminAccessToken = localStorage.getItem("lecturesift-billing-token") || "";

function adminEscape(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"})[char]);
}

function adminMoney(amountMinor, currency) {
  return new Intl.NumberFormat(window.LectureSiftI18n?.locale || "tr-TR", {
    style: "currency", currency: currency || "TRY",
  }).format(Number(amountMinor || 0) / 100);
}

function adminDate(value) {
  return new Intl.DateTimeFormat(window.LectureSiftI18n?.locale || "tr-TR", {
    dateStyle: "medium", timeStyle: "short",
  }).format(new Date(value));
}

function adminNotice(message, error = false) {
  const node = admin$("adminNotice");
  node.textContent = message;
  node.classList.toggle("error", error);
  node.hidden = false;
}

async function adminRequest(path, options = {}) {
  const response = await fetch(`${ADMIN_API}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${adminAccessToken}`,
      ...(options.headers || {}),
    },
    cache: "no-store",
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body?.detail?.message || adminT("error.request", "İstek tamamlanamadı."));
  return body;
}

function renderAdminOrders(orders) {
  const rows = orders.map(order => `<tr>
    <td><strong>${adminEscape(order.order_number || order.reference)}</strong><br><small>${adminDate(order.created_at)}</small></td>
    <td>${adminEscape(order.user?.name || "—")}<br><small>${adminEscape(order.user?.email || "")}</small></td>
    <td>${adminEscape(order.provider === "bank_transfer" ? adminT("payment.bankTransfer", "Banka havalesi") : String(order.provider || "—").toUpperCase())}</td>
    <td>${adminEscape(order.plan_code)} / ${adminEscape(order.interval)}</td>
    <td>${adminMoney(order.amount_minor, order.currency)}</td>
    <td><span class="status-pill ${order.status === "paid" ? "paid" : ""}">${adminEscape(adminT(`order.${order.status}`, order.status))}</span></td>
    <td>${order.provider === "bank_transfer" && order.status === "pending" ? `<span class="admin-actions"><button class="admin-action approve" data-order-decision="${adminEscape(order.reference)}" data-approve="1">${adminEscape(adminT("admin.approve", "Onayla"))}</button><button class="admin-action reject" data-order-decision="${adminEscape(order.reference)}" data-approve="0">${adminEscape(adminT("admin.reject", "Reddet"))}</button></span>` : "—"}</td>
  </tr>`).join("");
  admin$("adminOrders").innerHTML = `<table class="admin-table"><thead><tr><th>${adminT("payment.orderNumber","Sipariş no")}</th><th>${adminT("admin.customer","Müşteri")}</th><th>${adminT("admin.provider","Yöntem")}</th><th>${adminT("admin.plan","Plan")}</th><th>${adminT("payment.amount","Tutar")}</th><th>${adminT("admin.status","Durum")}</th><th>${adminT("admin.action","İşlem")}</th></tr></thead><tbody>${rows || `<tr><td colspan="7">${adminT("admin.noOrders","Sipariş bulunamadı.")}</td></tr>`}</tbody></table>`;
  document.querySelectorAll("[data-order-decision]").forEach(button => button.addEventListener("click", () => decideOrder(button)));
}

function renderAdminRewards(rewards) {
  const rows = rewards.map(reward => `<tr>
    <td><strong>@${adminEscape(reward.handle)}</strong><br><small>${adminEscape(reward.email || "")}</small></td>
    <td>+${Number(reward.minutes || 0).toLocaleString(window.LectureSiftI18n?.locale || "tr-TR")} ${adminEscape(adminT("unit.minuteShort", "dk"))}</td>
    <td>${adminEscape(adminT(`order.${reward.status}`, reward.status))}</td>
    <td><span class="admin-actions"><button class="admin-action approve" data-reward-decision="${adminEscape(reward.id)}" data-approve="1">${adminEscape(adminT("admin.approve", "Onayla"))}</button><button class="admin-action reject" data-reward-decision="${adminEscape(reward.id)}" data-approve="0">${adminEscape(adminT("admin.reject", "Reddet"))}</button></span></td>
  </tr>`).join("");
  admin$("adminRewards").innerHTML = `<table class="admin-table"><thead><tr><th>${adminT("admin.handle","Kullanıcı adı")}</th><th>${adminT("admin.minutes","Dakika")}</th><th>${adminT("admin.status","Durum")}</th><th>${adminT("admin.action","İşlem")}</th></tr></thead><tbody>${rows || `<tr><td colspan="4">${adminT("admin.noRewards","Bekleyen bonus talebi yok.")}</td></tr>`}</tbody></table>`;
  document.querySelectorAll("[data-reward-decision]").forEach(button => button.addEventListener("click", () => decideReward(button)));
}

function renderAdminRefunds(refunds) {
  const rows = refunds.map(item => {
    const note = `<input class="admin-inline-input" data-refund-note="${adminEscape(item.id)}" maxlength="500" placeholder="${adminEscape(adminT("admin.noteOptional", "Yönetici notu (isteğe bağlı)"))}">`;
    let actions = "—";
    if (item.status === "requested") actions = `${note}<span class="admin-actions"><button class="admin-action approve" data-refund-decision="${adminEscape(item.id)}" data-action="approve">${adminEscape(adminT("admin.approve", "Onayla"))}</button><button class="admin-action reject" data-refund-decision="${adminEscape(item.id)}" data-action="reject">${adminEscape(adminT("admin.reject", "Reddet"))}</button></span>`;
    if (item.status === "approved_pending_refund") actions = `${note}<button class="admin-action approve" data-refund-decision="${adminEscape(item.id)}" data-action="complete">${adminEscape(adminT("admin.markRefunded", "İade gönderildi"))}</button>`;
    return `<tr><td><strong>${adminEscape(item.order_reference)}</strong><br><small>${adminDate(item.created_at)}</small></td><td>${adminEscape(item.user?.name || "—")}<br><small>${adminEscape(item.user?.email || "")}</small></td><td>${adminEscape(item.reason)}</td><td>${adminEscape(adminT(`refund.status.${item.status}`, item.status))}</td><td>${actions}</td></tr>`;
  }).join("");
  admin$("adminRefunds").innerHTML = `<table class="admin-table"><thead><tr><th>${adminT("payment.orderNumber","Sipariş no")}</th><th>${adminT("admin.customer","Müşteri")}</th><th>${adminT("refund.reason","İade nedeni")}</th><th>${adminT("admin.status","Durum")}</th><th>${adminT("admin.action","İşlem")}</th></tr></thead><tbody>${rows || `<tr><td colspan="5">${adminT("admin.noRefunds","İade talebi bulunamadı.")}</td></tr>`}</tbody></table>`;
  document.querySelectorAll("[data-refund-decision]").forEach(button => button.addEventListener("click", () => decideRefund(button)));
}

function renderAdminUsers(users) {
  const rows = users.map(user => `<tr>
    <td>${adminEscape(user.name || "—")}<br><small>${adminEscape(user.email)}</small></td>
    <td>${adminEscape(user.phone || "—")}</td><td>${adminEscape(user.country_code || "—")}</td>
    <td>${user.email_verified ? "✓" : "—"}</td><td>${Number(user.credit_minutes || 0).toLocaleString()} dk</td><td>${adminDate(user.created_at)}</td>
    <td><div class="admin-credit-editor"><input class="admin-inline-input compact" type="number" min="-10000" max="10000" step="1" data-credit-delta="${adminEscape(user.id)}" placeholder="+ / - dk"><input class="admin-inline-input" maxlength="240" data-credit-reason="${adminEscape(user.id)}" placeholder="${adminEscape(adminT("admin.creditReason", "İşlem nedeni"))}"><button class="admin-action approve" data-user-credit="${adminEscape(user.id)}">${adminEscape(adminT("admin.apply", "Uygula"))}</button></div></td>
  </tr>`).join("");
  admin$("adminUserList").innerHTML = `<table class="admin-table"><thead><tr><th>${adminT("admin.customer","Müşteri")}</th><th>${adminT("field.phone","Telefon")}</th><th>${adminT("field.country","Ülke")}</th><th>${adminT("admin.verified","Doğrulanmış")}</th><th>${adminT("account.creditBalance","Ek kredi")}</th><th>${adminT("admin.created","Kayıt tarihi")}</th><th>${adminT("admin.creditAction","Dakika işlemi")}</th></tr></thead><tbody>${rows || `<tr><td colspan="7">${adminT("admin.noUsers","Kullanıcı bulunamadı.")}</td></tr>`}</tbody></table>`;
  document.querySelectorAll("[data-user-credit]").forEach(button => button.addEventListener("click", () => adjustCredit(button)));
}

function renderAdminCreditEvents(events) {
  const rows = events.map(item => `<tr><td>${adminDate(item.created_at)}</td><td>${adminEscape(item.email)}</td><td><strong>${item.minutes_delta > 0 ? "+" : ""}${Number(item.minutes_delta).toLocaleString()}</strong></td><td>${Number(item.balance_before).toLocaleString()} → ${Number(item.balance_after).toLocaleString()}</td><td>${adminEscape(item.reason)}</td></tr>`).join("");
  admin$("adminCreditEvents").innerHTML = `<table class="admin-table"><thead><tr><th>${adminT("admin.created","Kayıt tarihi")}</th><th>${adminT("admin.customer","Müşteri")}</th><th>${adminT("admin.minutes","Dakika")}</th><th>${adminT("admin.balanceChange","Bakiye değişimi")}</th><th>${adminT("admin.reason","Neden")}</th></tr></thead><tbody>${rows || `<tr><td colspan="5">${adminT("admin.noAudit","Henüz yönetici dakika işlemi yok.")}</td></tr>`}</tbody></table>`;
}

function renderAdminReadiness(billing, runtime) {
  const checks = [
    [adminT("admin.database", "Kalıcı veritabanı"), Boolean(billing?.database?.connected && billing?.database?.persistent)],
    [adminT("admin.emailDelivery", "E-posta gönderimi"), Boolean(billing?.email_delivery_configured)],
    [adminT("admin.cardPayments", "Kartlı ödeme"), Boolean(billing?.payments?.paytr?.configured)],
    [adminT("admin.durableProcessing", "Dayanıklı işleme altyapısı"), Boolean(runtime?.durable_processing_ready)],
    [adminT("admin.databaseRecovery", "Veritabanı geri yükleme"), Boolean(runtime?.recovery?.database_managed_backup_confirmed)],
    [adminT("admin.objectRetention", "Özel dosya saklama kuralı"), Boolean(runtime?.recovery?.object_retention_confirmed)],
    [adminT("admin.restoreDrill", "Geri yükleme tatbikatı"), Boolean(runtime?.recovery?.restore_drill_confirmed)],
  ];
  admin$("adminReadiness").innerHTML = checks.map(([label, ready]) => `<article><span>${adminEscape(label)}</span><strong class="${ready ? "ready" : "missing"}">${adminEscape(ready ? adminT("admin.ready", "Hazır") : adminT("admin.notReady", "Eksik ayar var"))}</strong></article>`).join("");
}

async function loadAdmin() {
  const [body, rewardBody, refundBody, creditBody, billingHealth, runtimeHealth] = await Promise.all([
    adminRequest("/billing/admin/overview?limit=100"),
    adminRequest("/admin/instagram-rewards?status=pending_verification"),
    adminRequest("/billing/admin/refund-requests"),
    adminRequest("/billing/admin/credit-events?limit=100"),
    fetch(`${ADMIN_API}/billing/health`, {cache:"no-store"}).then(response => response.ok ? response.json() : null).catch(() => null),
    fetch(`${ADMIN_API}/rollout/health`, {cache:"no-store"}).then(response => response.ok ? response.json() : null).catch(() => null),
  ]);
  admin$("adminUsers").textContent = body.counts.users;
  admin$("adminVerified").textContent = body.counts.verified_users;
  admin$("adminPending").textContent = body.counts.pending_orders;
  admin$("adminSubscriptions").textContent = body.counts.active_subscriptions;
  renderAdminOrders(body.orders || []);
  renderAdminRewards(rewardBody.rewards || []);
  renderAdminRefunds(refundBody.requests || []);
  renderAdminUsers(body.users || []);
  renderAdminCreditEvents(creditBody.events || []);
  renderAdminReadiness(billingHealth, runtimeHealth);
  admin$("adminLogin").hidden = true;
  admin$("adminPanel").hidden = false;
}

async function decideOrder(button) {
  const reference = button.dataset.orderDecision;
  button.disabled = true;
  try {
    await adminRequest(`/admin/manual-orders/${encodeURIComponent(reference)}/decision`, {method: "POST", body: JSON.stringify({approve: button.dataset.approve === "1"})});
    await loadAdmin();
  } catch (error) {
    adminNotice(error.message, true);
    button.disabled = false;
  }
}

async function decideReward(button) {
  const rewardId = button.dataset.rewardDecision;
  button.disabled = true;
  try {
    await adminRequest(`/admin/instagram-rewards/${encodeURIComponent(rewardId)}/decision`, {method: "POST", body: JSON.stringify({approve: button.dataset.approve === "1"})});
    await loadAdmin();
  } catch (error) {
    adminNotice(error.message, true);
    button.disabled = false;
  }
}

async function decideRefund(button) {
  const requestId = button.dataset.refundDecision;
  const note = document.querySelector(`[data-refund-note="${CSS.escape(requestId)}"]`)?.value || "";
  button.disabled = true;
  try {
    await adminRequest(`/billing/admin/refund-requests/${encodeURIComponent(requestId)}/decision`, {method:"POST", body:JSON.stringify({action:button.dataset.action, note})});
    await loadAdmin();
  } catch (error) {
    adminNotice(error.message, true);
    button.disabled = false;
  }
}

async function adjustCredit(button) {
  const userId = button.dataset.userCredit;
  const delta = Number(document.querySelector(`[data-credit-delta="${CSS.escape(userId)}"]`)?.value || 0);
  const reason = document.querySelector(`[data-credit-reason="${CSS.escape(userId)}"]`)?.value?.trim() || "";
  button.disabled = true;
  try {
    const body = await adminRequest(`/billing/admin/users/${encodeURIComponent(userId)}/credit-adjustment`, {method:"POST", body:JSON.stringify({minutes_delta:delta, reason})});
    adminNotice(body.message);
    await loadAdmin();
  } catch (error) {
    adminNotice(error.message, true);
    button.disabled = false;
  }
}

admin$("adminTokenForm").addEventListener("submit", async event => {
  event.preventDefault();
  adminAccessToken = admin$("adminToken").value.trim();
  try { await loadAdmin(); }
  catch (error) { adminAccessToken = ""; adminNotice(error.message, true); }
});
admin$("adminRefresh").addEventListener("click", () => loadAdmin().catch(error => adminNotice(error.message, true)));
if (adminAccessToken) {
  loadAdmin().catch(() => {
    adminAccessToken = "";
    admin$("adminLogin").hidden = false;
  });
}
