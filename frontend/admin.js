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
    <td>${adminEscape(order.plan_code)} / ${adminEscape(order.interval)}</td>
    <td>${adminMoney(order.amount_minor, order.currency)}</td>
    <td><span class="status-pill ${order.status === "paid" ? "paid" : ""}">${adminEscape(adminT(`order.${order.status}`, order.status))}</span></td>
    <td>${order.status === "pending" ? `<button data-approve="${adminEscape(order.reference)}">${adminEscape(adminT("admin.approve", "Onayla"))}</button>` : "—"}</td>
  </tr>`).join("");
  admin$("adminOrders").innerHTML = `<table class="admin-table"><thead><tr><th>${adminT("payment.orderNumber","Sipariş no")}</th><th>${adminT("admin.customer","Müşteri")}</th><th>${adminT("admin.plan","Plan")}</th><th>${adminT("payment.amount","Tutar")}</th><th>${adminT("admin.status","Durum")}</th><th>${adminT("admin.action","İşlem")}</th></tr></thead><tbody>${rows || `<tr><td colspan="6">${adminT("admin.noOrders","Sipariş bulunamadı.")}</td></tr>`}</tbody></table>`;
  document.querySelectorAll("[data-approve]").forEach(button => button.addEventListener("click", () => approveOrder(button)));
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
  const body = await adminRequest("/billing/admin/overview?limit=100");
  admin$("adminUsers").textContent = body.counts.users;
  admin$("adminVerified").textContent = body.counts.verified_users;
  admin$("adminPending").textContent = body.counts.pending_orders;
  admin$("adminSubscriptions").textContent = body.counts.active_subscriptions;
  renderAdminOrders(body.orders || []);
  renderAdminUsers(body.users || []);
  admin$("adminLogin").hidden = true;
  admin$("adminPanel").hidden = false;
}

async function approveOrder(button) {
  const reference = button.dataset.approve;
  button.disabled = true;
  try {
    await adminRequest(`/billing/manual-transfer/orders/${encodeURIComponent(reference)}/approve`, {method: "POST"});
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
