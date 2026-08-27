(() => {
  const API = "https://lecturesift-backend.onrender.com";
  const TOKEN_KEY = "lecturesift-billing-token";
  const DELETION_KEY = "lecturesift-account-deletion-scheduled";
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  let state = null;

  function token() { return localStorage.getItem(TOKEN_KEY) || ""; }
  function format(amountMinor, currency = "TRY") {
    const divisor = new Set(["JPY","KRW"]).has(currency) ? 1 : 100;
    try { return new Intl.NumberFormat(navigator.language || "tr-TR", {style:"currency",currency}).format(Number(amountMinor || 0) / divisor); }
    catch { return `${currency} ${Number(amountMinor || 0) / divisor}`; }
  }
  function date(value) {
    if (!value) return "—";
    try { return new Intl.DateTimeFormat(navigator.language || "tr-TR", {dateStyle:"medium",timeStyle:"short"}).format(new Date(value)); }
    catch { return value; }
  }
  function size(bytes) {
    const value = Number(bytes || 0);
    if (!value) return "—";
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / 1024 / 1024).toFixed(1)} MB`;
  }
  function statusLabel(value) {
    return ({created:"Ödeme başlatıldı",paid:"Ödendi",failed:"Başarısız",review_required:"İncelemede",pending:"Bekliyor",provider_pending:"Ödeme kuruluşunda",refunded:"İade edildi",rejected:"Reddedildi",done:"Hazır",error:"Hata",queued:"Sırada",working:"İşleniyor",expired:"Süresi doldu",deleted:"Silindi"})[value] || value || "—";
  }
  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (!(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
    headers.set("Authorization", `Bearer ${token()}`);
    const response = await fetch(`${API}${path}`, {...options,headers,cache:"no-store"});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = body.detail || body;
      throw Object.assign(new Error(detail.message || "İşlem tamamlanamadı."), {code:detail.code});
    }
    return body;
  }
  function notice(id, message, error = false) {
    const node = $(id);
    if (!node) return;
    node.textContent = message;
    node.hidden = false;
    node.classList.toggle("error", error);
  }

  function renderEntitlements() {
    const ent = state?.entitlements || {};
    $("downloadEntitlement").textContent = ent.download_enabled ? "Açık" : "Mini veya ücretli plan gerekli";
    $("visualTranslationEntitlement").textContent = ent.visual_translation ? "Açık" : "Mini veya ücretli plan gerekli";
  }

  function renderSubscription() {
    const subscription = state?.subscription;
    $("subscriptionCard").hidden = !subscription;
    if (!subscription) return;
    $("subscriptionDetails").innerHTML = `<div><span>Plan</span><strong>${esc(subscription.plan_code.toUpperCase())}</strong></div><div><span>Dönem</span><strong>${subscription.interval === "annual" ? "Yıllık" : "Aylık"}</strong></div><div><span>Yenileme/bitiş</span><strong>${esc(date(subscription.ends_at))}</strong></div><div><span>Yenileme biçimi</span><strong>${subscription.automatic_renewal_available ? "Otomatik" : "Manuel"}</strong></div>`;
    $("subscriptionCancel").hidden = Boolean(subscription.cancel_at_period_end);
    $("subscriptionResume").hidden = !subscription.cancel_at_period_end;
    if (subscription.cancel_at_period_end) notice("subscriptionNotice", `Abonelik ${date(subscription.ends_at)} tarihinde sona erecek.`);
  }

  function renderPurchases() {
    const purchases = state?.purchases || [];
    $("purchaseList").innerHTML = purchases.length ? purchases.map(item => `<div class="commerce-row"><div><strong>${esc(item.plan_code.toUpperCase())} · ${item.interval === "annual" ? "Yıllık" : item.interval === "monthly" ? "Aylık" : "Tek ödeme"}</strong><small>${esc(item.reference)} · ${esc(date(item.created_at))}</small><small>${esc(statusLabel(item.status))}</small></div><div class="row-actions"><strong>${esc(format(item.amount_minor,item.currency))}</strong></div></div>`).join("") : '<p class="commerce-empty">Henüz kartlı ödeme kaydı yok.</p>';
    const documents = state?.documents || [];
    $("documentList").innerHTML = documents.length ? `<h3>Ödeme makbuzları</h3>${documents.map(item => `<div class="commerce-row"><div><strong>${esc(item.document_number)}</strong><small>${esc(item.purchase_reference)} · ${esc(date(item.issued_at))}</small><small>${esc(item.notice)}</small></div><div class="row-actions"><strong>${esc(format(item.amount_minor,item.currency))}</strong></div></div>`).join("")}` : "";
    const refundable = purchases.filter(item => item.status === "paid");
    $("refundPurchase").replaceChildren(new Option("Uygun ödeme seç", ""), ...refundable.map(item => new Option(`${item.plan_code.toUpperCase()} · ${format(item.amount_minor,item.currency)} · ${item.reference}`, item.reference)));
  }

  async function downloadJob(jobId) {
    try {
      const response = await fetch(`${API}/jobs/${encodeURIComponent(jobId)}/download`, {headers:{Authorization:`Bearer ${token()}`},cache:"no-store"});
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body?.detail?.message || "ZIP indirilemedi.");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = "LectureSift_Study_Pack.zip"; document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1500);
    } catch (error) { alert(error.message); }
  }

  async function deleteJob(jobId) {
    if (!confirm("Bu ders kaydı ve kalıcı final ZIP silinsin mi? Bu işlem geri alınamaz.")) return;
    try { await api(`/billing/jobs/${encodeURIComponent(jobId)}`, {method:"DELETE"}); await load(); }
    catch (error) { alert(error.message); }
  }

  function renderJobs() {
    const jobs = state?.jobs || [];
    $("jobHistoryList").innerHTML = jobs.length ? jobs.map(item => `<div class="commerce-row"><div><strong>${esc(item.title || "LectureSift dersi")}</strong><small>${esc(date(item.created_at))} · ${esc(item.output_language.toUpperCase())} · ${esc(statusLabel(item.status))}</small><small>${esc(String(item.media_minutes || 0))} dk · ${esc(size(item.output_size_bytes))} · saklama: ${esc(date(item.retention_until))}</small></div><div class="row-actions">${item.status === "done" && item.download_entitled ? `<button type="button" data-download-job="${esc(item.job_id)}">ZIP indir</button>` : item.status === "done" ? '<a href="/plans.html?plan=mini">İndirmeyi aç</a>' : ""}<button class="danger" type="button" data-delete-job="${esc(item.job_id)}">Sil</button></div></div>`).join("") : '<p class="commerce-empty">Henüz tamamlanan ders kaydı yok.</p>';
    document.querySelectorAll("[data-download-job]").forEach(button => button.onclick = () => downloadJob(button.dataset.downloadJob));
    document.querySelectorAll("[data-delete-job]").forEach(button => button.onclick = () => deleteJob(button.dataset.deleteJob));
  }

  function renderRefunds() {
    const refunds = state?.refunds || [];
    $("refundList").innerHTML = refunds.length ? refunds.map(item => `<div class="commerce-row"><div><strong>${esc(item.purchase_reference)}</strong><small>${esc(statusLabel(item.status))} · ${esc(date(item.updated_at))}</small><small>${esc(item.reason)}</small></div></div>`).join("") : '<p class="commerce-empty">İade talebi yok.</p>';
  }

  function renderDeletion() {
    const scheduled = localStorage.getItem(DELETION_KEY);
    $("scheduleDeletion").hidden = Boolean(scheduled);
    $("cancelDeletion").hidden = !scheduled;
    if (scheduled) notice("deletionNotice", `Hesap silme talebi ${date(scheduled)} tarihinde oluşturuldu. 7 günlük süre içinde geri alabilirsin.`);
  }

  async function load() {
    if (!token()) return;
    try {
      state = await api("/billing/commerce");
      renderEntitlements(); renderSubscription(); renderPurchases(); renderJobs(); renderRefunds(); renderDeletion();
    } catch (error) {
      if (error.code === "LS-BILL-02") { localStorage.removeItem(TOKEN_KEY); location.href = "/login.html?next=/account.html"; return; }
      console.error(error);
    }
  }

  $("subscriptionCancel").onclick = async () => {
    try { await api("/billing/subscription", {method:"PATCH",body:JSON.stringify({cancel_at_period_end:true})}); notice("subscriptionNotice","Abonelik dönem sonunda sona erecek."); await load(); }
    catch (error) { notice("subscriptionNotice",error.message,true); }
  };
  $("subscriptionResume").onclick = async () => {
    try { await api("/billing/subscription", {method:"PATCH",body:JSON.stringify({cancel_at_period_end:false})}); notice("subscriptionNotice","Abonelik yenilemesi sürdürülecek."); await load(); }
    catch (error) { notice("subscriptionNotice",error.message,true); }
  };
  $("refundForm").onsubmit = async event => {
    event.preventDefault();
    try {
      const body = await api("/billing/refunds", {method:"POST",body:JSON.stringify({purchase_reference:$("refundPurchase").value,reason:$("refundReason").value})});
      $("refundReason").value = ""; notice("refundNotice",`İade talebi oluşturuldu: ${body.refund.id}`); await load();
    } catch (error) { notice("refundNotice",error.message,true); }
  };
  $("scheduleDeletion").onclick = async () => {
    if (!confirm("Hesabın 7 gün sonra anonimleştirilecek, kalıcı ZIP’lerin silinecek ve aktif aboneliğin iptal edilecek. Devam edilsin mi?")) return;
    try { const body = await api("/billing/account-deletion", {method:"POST"}); localStorage.setItem(DELETION_KEY, body.requested_at); renderDeletion(); }
    catch (error) { notice("deletionNotice",error.message,true); }
  };
  $("cancelDeletion").onclick = async () => {
    try { await api("/billing/account-deletion", {method:"DELETE"}); localStorage.removeItem(DELETION_KEY); $("deletionNotice").hidden = true; renderDeletion(); }
    catch (error) { notice("deletionNotice",error.message,true); }
  };

  load();
})();
