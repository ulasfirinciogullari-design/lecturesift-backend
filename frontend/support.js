const SUPPORT_API = "https://api.lecturesift.com";
const support$ = id => document.getElementById(id);
const supportT = (key, fallback) => window.LectureSiftI18n?.t(key) || fallback;
const supportEscape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"})[char]);
const supportDate = value => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat(window.LectureSiftI18n?.locale || "tr-TR", {dateStyle:"medium", timeStyle:"short"}).format(date);
};
const params = new URLSearchParams(location.hash.replace(/^#/, ""));
const conversationId = params.get("conversation") || "";
const conversationToken = params.get("token") || "";

function renderSupportConversation(data) {
  const message = data.message || {};
  const entries = [{direction:"user", body:message.message, created_at:message.created_at}, ...(data.replies || [])];
  support$("supportThread").innerHTML = entries.map(item => `<article class="support-bubble ${item.direction === "admin" ? "outgoing" : "incoming"}"><header><strong>${supportEscape(item.direction === "admin" ? "LectureSift" : supportT("support.you", "Sen"))}</strong><time>${supportEscape(supportDate(item.created_at))}</time></header><p>${supportEscape(item.body).replace(/\n/g, "<br>")}</p></article>`).join("");
  support$("supportThread").hidden = false;
  support$("supportReplyForm").hidden = false;
  support$("supportStatus").hidden = true;
}

async function supportRequest(path, options = {}) {
  const response = await fetch(`${SUPPORT_API}${path}`, {cache:"no-store", ...options, headers:{"Content-Type":"application/json", ...(options.headers || {})}});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body?.detail?.message || supportT("support.invalid", "Konuşma bağlantısı geçersiz veya kullanılamıyor."));
  return body;
}

async function loadSupportConversation() {
  if (!conversationId || !conversationToken) throw new Error(supportT("support.invalid", "Konuşma bağlantısı geçersiz veya kullanılamıyor."));
  const body = await supportRequest(`/contact/conversations/${encodeURIComponent(conversationId)}/view`, {method:"POST", body:JSON.stringify({token:conversationToken})});
  renderSupportConversation(body);
}

support$("supportReplyForm")?.addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  const message = String(new FormData(form).get("message") || "").trim();
  button.disabled = true;
  try {
    const body = await supportRequest(`/contact/conversations/${encodeURIComponent(conversationId)}/replies`, {method:"POST", body:JSON.stringify({token:conversationToken, message})});
    form.reset();
    renderSupportConversation(body);
    support$("supportStatus").textContent = body.notice || supportT("support.sent", "Yanıtın destek ekibine ulaştı.");
    support$("supportStatus").hidden = false;
    setTimeout(() => { support$("supportStatus").hidden = true; }, 5000);
  } catch (error) {
    support$("supportStatus").textContent = error.message;
    support$("supportStatus").classList.add("error");
    support$("supportStatus").hidden = false;
  } finally { button.disabled = false; }
});

loadSupportConversation().catch(error => {
  support$("supportStatus").textContent = error.message;
  support$("supportStatus").classList.add("error");
});
