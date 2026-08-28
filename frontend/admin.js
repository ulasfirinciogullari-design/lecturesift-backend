const ADMIN_API = "https://lecturesift-backend.onrender.com";
const admin$ = id => document.getElementById(id);
const adminT = (key, fallback) => window.LectureSiftI18n?.t(key) || fallback || key;
let adminAccessToken = "";

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

function renderAdminUsers(users) {
  const rows = users.map(user => `<tr>
    <td>${adminEscape(user.name || "—")}<br><small>${adminEscape(user.email)}</small></td>
    <td>${adminEscape(user.phone || "—")}</td><td>${adminEscape(user.country_code || "—")}</td>
    <td>${user.email_verified ? "✓" : "—"}</td><td>${Number(user.credit_minutes || 0).toLocaleString()} dk</td><td>${adminDate(user.created_at)}</td>
  </tr>`).join("");
  admin$("adminUserList").innerHTML = `<table class="admin-table"><thead><tr><th>${adminT("admin.customer","Müşteri")}</th><th>${adminT("field.phone","Telefon")}</th><th>${adminT("field.country","Ülke")}</th><th>${adminT("admin.verified","Doğrulanmış")}</th><th>${adminT("account.creditBalance","Ek kredi")}</th><th>${adminT("admin.created","Kayıt tarihi")}</th></tr></thead><tbody>${rows || `<tr><td colspan="6">${adminT("admin.noUsers","Kullanıcı bulunamadı.")}</td></tr>`}</tbody></table>`;
}

async function loadAdmin() {
  const [body, rewardBody] = await Promise.all([
    adminRequest("/billing/admin/overview?limit=100"),
    adminRequest("/admin/instagram-rewards?status=pending_verification"),
  ]);
  admin$("adminUsers").textContent = body.counts.users;
  admin$("adminVerified").textContent = body.counts.verified_users;
  admin$("adminPending").textContent = body.counts.pending_orders;
  admin$("adminSubscriptions").textContent = body.counts.active_subscriptions;
  renderAdminOrders(body.orders || []);
  renderAdminRewards(rewardBody.rewards || []);
  renderAdminUsers(body.users || []);
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

admin$("adminTokenForm").addEventListener("submit", async event => {
  event.preventDefault();
  adminAccessToken = admin$("adminToken").value.trim();
  try { await loadAdmin(); }
  catch (error) { adminAccessToken = ""; adminNotice(error.message, true); }
});
admin$("adminRefresh").addEventListener("click", () => loadAdmin().catch(error => adminNotice(error.message, true)));
