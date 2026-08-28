const ADMIN_API = "https://lecturesift-backend.onrender.com";
const admin$ = id => document.getElementById(id);
const adminT = (key, fallback) => window.LectureSiftI18n?.t(key) || fallback || key;
const adminLocale = () => window.LectureSiftI18n?.locale || "tr-TR";
const adminMinuteShort = () => adminT("unit.minuteShort", "dk");
let adminAccessToken = localStorage.getItem("lecturesift-billing-token") || "";
let adminLoading = false;
let adminState = {overview:{counts:{}}, rewards:[], refunds:[], credits:[], contacts:[], jobs:[], billing:null, runtime:null};

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
  const node = admin$("adminNotice");
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

function renderAdminOrders(orders) {
  const rows = orders.map(order => `<tr>
    <td><strong>${adminEscape(order.order_number || order.reference)}</strong><br><small title="${adminEscape(adminDate(order.created_at))}">${adminEscape(adminRelativeDate(order.created_at))}</small></td>
    <td>${adminEscape(order.user?.name || "—")}<br><small>${adminEscape(order.user?.email || "")}</small></td>
    <td>${adminEscape(order.provider === "bank_transfer" ? adminT("payment.bankTransfer", "Banka havalesi") : String(order.provider || "—").toUpperCase())}</td>
    <td>${adminEscape(order.plan_code)} / ${adminEscape(order.interval)}</td><td>${adminMoney(order.amount_minor, order.currency)}</td>
    <td><span class="status-pill ${order.status === "paid" ? "paid" : ""}">${adminEscape(adminStatusLabel(order.status))}</span></td>
    <td>${order.failure_message || order.failure_code ? `${adminEscape(order.failure_message || "Ödeme onaylanmadı")}${order.failure_code ? `<br><small>${adminEscape(order.failure_code)}</small>` : ""}` : "—"}</td>
    <td>${order.provider === "bank_transfer" && order.status === "pending" ? `<span class="admin-actions"><button class="admin-action approve" data-order-decision="${adminEscape(order.reference)}" data-approve="1">${adminEscape(adminT("admin.approve", "Onayla"))}</button><button class="admin-action reject" data-order-decision="${adminEscape(order.reference)}" data-approve="0">${adminEscape(adminT("admin.reject", "Reddet"))}</button></span>` : "—"}</td>
  </tr>`).join("");
  admin$("adminOrders").innerHTML = `<table class="admin-table"><thead><tr><th>${adminT("payment.orderNumber","Sipariş no")}</th><th>${adminT("admin.customer","Müşteri")}</th><th>${adminT("admin.provider","Yöntem")}</th><th>${adminT("admin.plan","Plan")}</th><th>${adminT("payment.amount","Tutar")}</th><th>${adminT("admin.status","Durum")}</th><th>Hata / ret nedeni</th><th>${adminT("admin.action","Işlem")}</th></tr></thead><tbody>${rows || `<tr><td colspan="8">${adminT("admin.noOrders","Sipariş bulunamadı.")}</td></tr>`}</tbody></table>`;
  document.querySelectorAll("[data-order-decision]").forEach(button => button.addEventListener("click", () => decideOrder(button)));
}

function renderAdminRewards(rewards) {
  const rows = rewards.map(reward => `<tr><td><strong>@${adminEscape(reward.handle)}</strong><br><small>${adminEscape(reward.email || "")}</small></td><td>+${Number(reward.minutes || 0).toLocaleString(adminLocale())} ${adminEscape(adminMinuteShort())}</td><td>${adminEscape(adminStatusLabel(reward.status))}</td><td><span class="admin-actions"><button class="admin-action approve" data-reward-decision="${adminEscape(reward.id)}" data-approve="1">Onayla</button><button class="admin-action reject" data-reward-decision="${adminEscape(reward.id)}" data-approve="0">Reddet</button></span></td></tr>`).join("");
  admin$("adminRewards").innerHTML = `<table class="admin-table"><thead><tr><th>Kullanıcı adı</th><th>Dakika</th><th>Durum</th><th>İşlem</th></tr></thead><tbody>${rows || '<tr><td colspan="4">Bekleyen bonus talebi yok.</td></tr>'}</tbody></table>`;
  document.querySelectorAll("[data-reward-decision]").forEach(button => button.addEventListener("click", () => decideReward(button)));
}

function renderAdminRefunds(refunds) {
  const rows = refunds.map(item => {
    const note = `<input class="admin-inline-input" data-refund-note="${adminEscape(item.id)}" maxlength="500" placeholder="Yönetici notu (isteğe bağlı)">`;
    let actions = "—";
    if (item.status === "requested") actions = `${note}<span class="admin-actions"><button class="admin-action approve" data-refund-decision="${adminEscape(item.id)}" data-action="approve">Onayla</button><button class="admin-action reject" data-refund-decision="${adminEscape(item.id)}" data-action="reject">Reddet</button></span>`;
    if (item.status === "approved_pending_refund") actions = `${note}<button class="admin-action approve" data-refund-decision="${adminEscape(item.id)}" data-action="complete">İade gönderildi</button>`;
    return `<tr><td><strong>${adminEscape(item.order_reference)}</strong><br><small title="${adminEscape(adminDate(item.created_at))}">${adminEscape(adminRelativeDate(item.created_at))}</small></td><td>${adminEscape(item.user?.name || "—")}<br><small>${adminEscape(item.user?.email || "")}</small></td><td>${adminEscape(item.reason)}</td><td>${adminEscape(adminStatusLabel(item.status))}</td><td>${actions}</td></tr>`;
  }).join("");
  admin$("adminRefunds").innerHTML = `<table class="admin-table"><thead><tr><th>Sipariş no</th><th>Müşteri</th><th>İade nedeni</th><th>Durum</th><th>İşlem</th></tr></thead><tbody>${rows || '<tr><td colspan="5">İade talebi bulunamadı.</td></tr>'}</tbody></table>`;
  document.querySelectorAll("[data-refund-decision]").forEach(button => button.addEventListener("click", () => decideRefund(button)));
}

function renderAdminUsers(users) {
  const rows = users.map(user => `<tr><td>${adminEscape(user.name || "—")}<br><small>${adminEscape(user.email)}</small></td><td>${adminEscape(user.phone || "—")}</td><td>${adminEscape(user.country_code || "—")}</td><td>${user.email_verified ? '<span class="status-pill paid">Doğrulandı</span>' : '<span class="status-pill">Bekliyor</span>'}</td><td>${Number(user.credit_minutes || 0).toLocaleString(adminLocale())} dk</td><td title="${adminEscape(adminDate(user.created_at))}">${adminEscape(adminRelativeDate(user.created_at))}</td><td><div class="admin-credit-editor"><input class="admin-inline-input compact" type="number" min="-10000" max="10000" step="1" data-credit-delta="${adminEscape(user.id)}" placeholder="+ / - dk"><input class="admin-inline-input" maxlength="240" data-credit-reason="${adminEscape(user.id)}" placeholder="İşlem nedeni"><button class="admin-action approve" data-user-credit="${adminEscape(user.id)}">Uygula</button></div></td></tr>`).join("");
  admin$("adminUserList").innerHTML = `<table class="admin-table"><thead><tr><th>Müşteri</th><th>Telefon</th><th>Ülke</th><th>Doğrulama</th><th>Ek kredi</th><th>Kayıt</th><th>Dakika işlemi</th></tr></thead><tbody>${rows || '<tr><td colspan="7">Kullanıcı bulunamadı.</td></tr>'}</tbody></table>`;
  document.querySelectorAll("[data-user-credit]").forEach(button => button.addEventListener("click", () => adjustCredit(button)));
}

function renderAdminCreditEvents(events) {
  const rows = events.map(item => `<tr><td title="${adminEscape(adminDate(item.created_at))}">${adminEscape(adminRelativeDate(item.created_at))}</td><td>${adminEscape(item.email)}</td><td><strong>${item.minutes_delta > 0 ? "+" : ""}${Number(item.minutes_delta).toLocaleString()}</strong></td><td>${Number(item.balance_before).toLocaleString()} → ${Number(item.balance_after).toLocaleString()}</td><td>${adminEscape(item.reason)}</td></tr>`).join("");
  admin$("adminCreditEvents").innerHTML = `<table class="admin-table"><thead><tr><th>Tarih</th><th>Müşteri</th><th>Dakika</th><th>Bakiye değişimi</th><th>Neden</th></tr></thead><tbody>${rows || '<tr><td colspan="5">Henüz yönetici dakika işlemi yok.</td></tr>'}</tbody></table>`;
}

function renderAdminContactMessages(messages) {
  const rows = messages.map(item => `<tr><td><strong>${adminEscape(item.name)}</strong><br><a href="mailto:${encodeURIComponent(item.email)}">${adminEscape(item.email)}</a><br><small title="${adminEscape(adminDate(item.created_at))}">${adminEscape(adminRelativeDate(item.created_at))}</small></td><td><strong>${adminEscape(item.topic)}</strong>${item.order_reference ? `<br><small>Sipariş no: ${adminEscape(item.order_reference)}</small>` : ""}</td><td class="admin-message-cell">${adminEscape(item.message)}</td><td><span class="status-pill ${item.status === "resolved" ? "paid" : ""}">${adminEscape(adminStatusLabel(item.status))}</span><br><small>${item.email_notified ? "E-posta bildirildi" : "Panelde saklandı"}</small></td><td><span class="admin-actions"><button class="admin-action" data-contact-status="${adminEscape(item.id)}" data-status="read">Okundu</button><button class="admin-action approve" data-contact-status="${adminEscape(item.id)}" data-status="resolved">Çözümlendi</button></span></td></tr>`).join("");
  admin$("adminContactMessages").innerHTML = `<table class="admin-table"><thead><tr><th>Gönderen</th><th>Konu</th><th>Mesaj</th><th>Durum</th><th>İşlem</th></tr></thead><tbody>${rows || '<tr><td colspan="5">Henüz iletişim mesajı yok.</td></tr>'}</tbody></table>`;
  document.querySelectorAll("[data-contact-status]").forEach(button => button.addEventListener("click", () => updateContactMessage(button)));
}

function adminReadinessChecks(billing, runtime) {
  const cardReady = Boolean(billing?.payments?.iyzico?.configured || billing?.payments?.paytr?.configured);
  return [
    {label:"Kalıcı veritabanı", ready:Boolean(billing?.database?.connected && billing?.database?.persistent), severity:"critical", detail:"Hesap, sipariş ve üyelik kayıtları", action:"Render PostgreSQL bağlantısını kontrol et"},
    {label:"E-posta doğrulama", ready:Boolean(billing?.email_delivery_configured), severity:"critical", detail:"Kayıt, kod, bağlantı ve parola sıfırlama", action:"Resend anahtarını ve gönderen alan adını kontrol et"},
    {label:"Satıcı/sağlayıcı kimliği", ready:Boolean(billing?.commerce_identity?.configured), severity:"critical", detail:"Yasal satış ve ödeme açıklamaları", action:"Zorunlu işletme bilgilerini tamamla"},
    {label:"Kartlı ödeme", ready:cardReady, severity:"critical", detail:cardReady ? `Etkin: ${billing?.payments?.iyzico?.configured ? "iyzico" : "PayTR"}` : "Kart sağlayıcısı bağlı değil", action:"Ödeme sağlayıcısı anahtarlarını kontrol et"},
    {label:"Banka havalesi", ready:Boolean(billing?.payments?.bank_transfer?.configured), severity:"recommended", detail:"Sipariş numarasıyla manuel ödeme", action:"IBAN ve alıcı bilgisini kontrol et"},
    {label:"Dayanıklı işleme", ready:Boolean(runtime?.durable_processing_ready), severity:"critical", detail:`Kuyruk ${runtime?.queue?.connected ? "bağlı" : "bağlı değil"} · worker ${runtime?.worker?.workers || 0} · özel depo ${runtime?.storage?.connected ? "bağlı" : "bağlı değil"}`, action:"Redis, worker ve özel dosya deposunu etkinleştir"},
    {label:"Veritabanı kurtarma", ready:Boolean(runtime?.recovery?.database_managed_backup_confirmed), severity:"planned", detail:"Yönetilen yedek doğrulaması", action:"Yedek saklama ve geri alma adımlarını belgele"},
    {label:"Dosya saklama kuralı", ready:Boolean(runtime?.recovery?.object_retention_confirmed), severity:"planned", detail:"Özel depodaki çıktıların yaşam döngüsü", action:"Özel depo açıldıktan sonra saklama kuralını doğrula"},
    {label:"Geri yükleme tatbikatı", ready:Boolean(runtime?.recovery?.restore_drill_confirmed), severity:"planned", detail:"Gerçek kurtarma testi ve kayıt tarihi", action:"Altyapı tamamlanınca kontrollü test yap"},
    {label:"Ücretsiz planda banner reklam", ready:Boolean(runtime?.display_ads_configured), severity:"optional", detail:"Ücretli planlar her durumda reklamsız", action:"Google Ad Manager birimi hazır olduğunda aç"},
  ];
}

function renderAdminReadiness(billing, runtime) {
  const checks = adminReadinessChecks(billing, runtime);
  const stateText = item => item.ready ? "Hazır" : item.severity === "optional" ? "Opsiyonel · kapalı" : item.severity === "planned" ? "Planlandı" : item.severity === "recommended" ? "Önerilen ayar" : "Kritik eksik";
  admin$("adminReadiness").innerHTML = checks.map(item => `<article class="readiness-${item.ready ? "ready" : item.severity}"><div><span>${adminEscape(item.label)}</span><small>${adminEscape(item.detail)}</small>${!item.ready ? `<em>${adminEscape(item.action)}</em>` : ""}</div><strong class="${item.ready ? "ready" : item.severity}">${adminEscape(stateText(item))}</strong></article>`).join("");
  const payment = billing?.payments || {};
  admin$("adminPaymentSummary").innerHTML = `<article><small>Öncelikli kart sağlayıcısı</small><strong>${payment.iyzico?.configured ? "iyzico" : payment.paytr?.configured ? "PayTR" : "Bağlı değil"}</strong></article><article><small>iyzico</small><strong class="${payment.iyzico?.configured ? "ready" : "muted"}">${payment.iyzico?.configured ? "Canlı" : "Kapalı"}</strong></article><article><small>PayTR</small><strong class="${payment.paytr?.configured ? "ready" : "muted"}">${payment.paytr?.configured ? "Canlı" : "Opsiyonel"}</strong></article><article><small>Havale</small><strong class="${payment.bank_transfer?.configured ? "ready" : "muted"}">${payment.bank_transfer?.configured ? "Canlı" : "Kapalı"}</strong></article>`;
  return checks;
}

function renderAdminJobs(jobs) {
  const rows = jobs.map(job => `<tr><td><strong>${adminEscape(job.job_id)}</strong><br><small>${adminEscape(job.owner_id ? `Hesap: ${job.owner_id.slice(0, 8)}…` : "Misafir/hesapsız")}</small></td><td><span class="status-pill ${job.status === "done" ? "paid" : ""}">${adminEscape(adminStatusLabel(job.status))}</span></td><td><div class="admin-progress"><span style="width:${Math.max(0, Math.min(100, Number(job.percent || 0)))}%"></span></div><small>%${Number(job.percent || 0)} · ${adminEscape(job.stage || "—")}</small></td><td title="${adminEscape(adminDate(job.created))}">${adminEscape(adminRelativeDate(job.created))}</td><td>${adminEscape(job.error_code || job.public_error || job.error || "—")}</td></tr>`).join("");
  admin$("adminJobs").innerHTML = `<table class="admin-table"><thead><tr><th>İş kimliği</th><th>Durum</th><th>İlerleme / aşama</th><th>Başlangıç</th><th>Hata</th></tr></thead><tbody>${rows || '<tr><td colspan="5">Kayıtlı iş bulunamadı.</td></tr>'}</tbody></table>`;
}

function buildTimeline() {
  const events = [];
  (adminState.overview.orders || []).forEach(item => events.push({kind:"order", at:item.created_at, title:`${item.provider === "bank_transfer" ? "Havale" : String(item.provider || "Kart").toUpperCase()} siparişi`, detail:`${item.reference} · ${adminMoney(item.amount_minor, item.currency)} · ${adminStatusLabel(item.status)}`, actor:item.user?.email || ""}));
  (adminState.contacts || []).forEach(item => events.push({kind:"contact", at:item.created_at, title:`Destek mesajı: ${item.topic}`, detail:item.message, actor:item.email}));
  (adminState.refunds || []).forEach(item => events.push({kind:"refund", at:item.created_at, title:`İade talebi · ${adminStatusLabel(item.status)}`, detail:`${item.order_reference} · ${item.reason}`, actor:item.user?.email || ""}));
  (adminState.rewards || []).forEach(item => events.push({kind:"reward", at:item.created_at, title:`Instagram bonusu · ${adminStatusLabel(item.status)}`, detail:`@${item.handle} · +${item.minutes} dk`, actor:item.email || ""}));
  (adminState.credits || []).forEach(item => events.push({kind:"credit", at:item.created_at, title:`Dakika işlemi ${item.minutes_delta > 0 ? "+" : ""}${item.minutes_delta}`, detail:item.reason, actor:item.email || ""}));
  (adminState.overview.users || []).forEach(item => events.push({kind:"user", at:item.created_at, title:"Yeni kullanıcı hesabı", detail:item.email_verified ? "E-posta doğrulandı" : "E-posta doğrulaması bekliyor", actor:item.email}));
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
}

function normalizeSearch(value) { return String(value || "").toLocaleLowerCase("tr-TR"); }

function applyAdminFilters() {
  const orderQuery = normalizeSearch(admin$("adminOrderSearch")?.value);
  const orderStatus = admin$("adminOrderStatus")?.value || "all";
  renderAdminOrders((adminState.overview.orders || []).filter(item => (orderStatus === "all" || item.status === orderStatus || (orderStatus === "failed" && ["failed","token_failed","cancelled"].includes(item.status))) && normalizeSearch(`${item.reference} ${item.user?.email} ${item.user?.name} ${item.provider}`).includes(orderQuery)));
  const messageQuery = normalizeSearch(admin$("adminMessageSearch")?.value);
  const messageStatus = admin$("adminMessageStatus")?.value || "all";
  renderAdminContactMessages(adminState.contacts.filter(item => (messageStatus === "all" || item.status === messageStatus) && normalizeSearch(`${item.name} ${item.email} ${item.topic} ${item.order_reference} ${item.message}`).includes(messageQuery)));
  const userQuery = normalizeSearch(admin$("adminUserSearch")?.value);
  const userStatus = admin$("adminUserStatus")?.value || "all";
  renderAdminUsers((adminState.overview.users || []).filter(item => (userStatus === "all" || (userStatus === "verified") === Boolean(item.email_verified)) && normalizeSearch(`${item.name} ${item.email} ${item.phone} ${item.country_code}`).includes(userQuery)));
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
      adminPublicRequest("/billing/health"), adminPublicRequest("/rollout/health"),
    ]);
    adminState = {overview, rewards:optional[0].rewards || [], refunds:optional[1].requests || [], credits:optional[2].events || [], contacts:optional[3].messages || [], jobs:optional[4].jobs || [], billing:optional[5], runtime:optional[6]};
    renderMetrics();
    renderAdminRewards(adminState.rewards.filter(item => item.status === "pending_verification"));
    renderAdminRefunds(adminState.refunds);
    renderAdminCreditEvents(adminState.credits);
    applyAdminFilters();
    const checks = renderAdminReadiness(adminState.billing, adminState.runtime);
    renderAdminAlerts(checks);
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

async function updateContactMessage(button) {
  button.disabled = true;
  try { await adminRequest(`/billing/admin/contact-messages/${encodeURIComponent(button.dataset.contactStatus)}/status`, {method:"POST", body:JSON.stringify({status:button.dataset.status})}); await loadAdmin(); }
  catch (error) { adminNotice(error.message, true); button.disabled = false; }
}

admin$("adminTokenForm").addEventListener("submit", async event => {
  event.preventDefault(); adminAccessToken = admin$("adminToken").value.trim();
  try { await loadAdmin(); } catch (error) { adminAccessToken = ""; adminNotice(error.message, true); }
});
admin$("adminRefresh").addEventListener("click", () => loadAdmin().catch(error => adminNotice(error.message, true)));
["adminOrderSearch","adminOrderStatus","adminMessageSearch","adminMessageStatus","adminUserSearch","adminUserStatus","adminJobStatus","adminTimelineFilter"].forEach(id => admin$(id)?.addEventListener(id.includes("Search") ? "input" : "change", applyAdminFilters));
admin$("adminExportOrders").addEventListener("click", () => downloadAdminCsv("lecturesift-siparisler.csv", (adminState.overview.orders || []).map(item => ({siparis_no:item.reference, tarih:item.created_at, musteri:item.user?.name || "", eposta:item.user?.email || "", yontem:item.provider, plan:item.plan_code, donem:item.interval, tutar_minor:item.amount_minor, para_birimi:item.currency, durum:item.status}))));
admin$("adminExportMessages").addEventListener("click", () => downloadAdminCsv("lecturesift-mesajlar.csv", adminState.contacts.map(item => ({tarih:item.created_at, ad_soyad:item.name, eposta:item.email, konu:item.topic, siparis_no:item.order_reference || "", durum:item.status, mesaj:item.message}))));
admin$("adminExportUsers").addEventListener("click", () => downloadAdminCsv("lecturesift-kullanicilar.csv", (adminState.overview.users || []).map(item => ({kayit_tarihi:item.created_at, ad_soyad:item.name, eposta:item.email, telefon:item.phone || "", ulke:item.country_code || "", eposta_dogrulandi:item.email_verified ? "evet" : "hayir", kredi_dakika:item.credit_minutes}))));
setInterval(() => { if (adminAccessToken && admin$("adminAutoRefresh").checked && document.visibilityState === "visible") loadAdmin({silent:true}).catch(() => {}); }, 60000);
if (adminAccessToken) loadAdmin().catch(() => { adminAccessToken = ""; admin$("adminLogin").hidden = false; });
