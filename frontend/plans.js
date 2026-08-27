const API = "https://lecturesift-backend.onrender.com";
const TOKEN_KEY = "lecturesift-billing-token";
const ORDER = ["free", "mini", "credit", "lite", "plus", "pro", "max", "business"];
const ZERO_DECIMAL = new Set(["JPY", "KRW"]);
const LOCALE = window.LECTURESIFT_LOCALE_DATA || {currencies:["TRY","USD","EUR","GBP"], currencyForCountry:{}};
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);

const COPY = {
  free: ["Ücretsiz", "Sınırlı sonuç önizlemesi; dosya ve ZIP indirme kapalıdır."],
  mini: ["Mini", "İlk indirme veya tek kısa ders için sembolik tek seferlik paket."],
  credit: ["Dakika Paketi", "Abonelik olmadan daha fazla dakika ve indirme hakkı."],
  lite: ["Lite", "Düzenli bireysel çalışma için."],
  plus: ["Plus", "Yoğun ders dönemi ve çoklu kaynaklar için."],
  pro: ["Pro", "Uzun kayıtlar ve öncelikli işleme için."],
  max: ["Max", "En yüksek bireysel kapasite için."],
  business: ["Business", "Ekipler, kurumlar ve özel kapasite için."],
};
const FALLBACK_MINUTES = {free:60,mini:60,credit:180,lite:600,plus:2400,pro:6000,max:15000,business:null};
const FALLBACK_MONTHLY = {
  TRY:{free:0,mini:4900,credit:14900,lite:34900,plus:69900,pro:129900,max:249900},
  USD:{free:0,mini:200,credit:500,lite:900,plus:1800,pro:3300,max:6300},
  EUR:{free:0,mini:200,credit:500,lite:900,plus:1700,pro:3100,max:5900},
  GBP:{free:0,mini:150,credit:400,lite:800,plus:1500,pro:2700,max:5200},
};

let token = localStorage.getItem(TOKEN_KEY) || "";
let catalog = null;
let account = null;
let health = null;
let interval = new URLSearchParams(location.search).get("interval") === "annual" ? "annual" : "monthly";
let currency = localStorage.getItem("lecturesift-currency") || detectedCurrency();
let activePurchase = "";
let pollTimer = null;

function detectedCurrency() {
  const savedCountry = localStorage.getItem("lecturesift-country") || "";
  const region = (navigator.language.split("-")[1] || "").toUpperCase();
  return LOCALE.currencyForCountry?.[(savedCountry || region).toUpperCase()] || (region === "TR" ? "TRY" : "USD");
}

function format(amountMinor, code) {
  const divisor = ZERO_DECIMAL.has(code) ? 1 : 100;
  try {
    return new Intl.NumberFormat(navigator.language || "tr-TR", {style:"currency",currency:code,maximumFractionDigits:divisor === 1 ? 0 : 2}).format(Number(amountMinor || 0) / divisor);
  } catch {
    return `${code} ${Number(amountMinor || 0) / divisor}`;
  }
}

function showError(message, code = "LS-PAY-00") {
  $("errorMessage").textContent = message;
  $("errorCode").textContent = code;
  $("errorBox").hidden = false;
}

async function api(path, options = {}, useToken = true) {
  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  if (useToken && token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API}${path}`, {...options, headers, cache:"no-store"});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail || body;
    throw Object.assign(new Error(detail.message || "İşlem tamamlanamadı."), {code:detail.code || "LS-PAY-00", unlockPlan:detail.unlock_plan});
  }
  return body;
}

function populateCurrencies() {
  const supported = catalog?.supported_currencies || LOCALE.currencies || ["TRY","USD","EUR","GBP"];
  $("billingCurrency").replaceChildren(...supported.map(code => new Option(code, code)));
  if (!supported.includes(currency)) currency = supported.includes("USD") ? "USD" : supported[0];
  $("billingCurrency").value = currency;
}

function priceFor(plan) {
  const exact = plan.interval_prices?.[plan.kind === "subscription" ? interval : "one_time"];
  if (exact) return exact;
  const display = plan.display_price || plan.manual_price;
  if (!display) {
    const amount = FALLBACK_MONTHLY[currency]?.[plan.code];
    if (amount == null) return null;
    return {currency, amount_minor:plan.kind === "subscription" && interval === "annual" ? amount * 10 : amount};
  }
  return {
    currency: display.currency || currency,
    amount_minor: plan.kind === "subscription" && interval === "annual" ? Number(display.amount_minor) * 10 : Number(display.amount_minor),
  };
}

function monthlyEquivalent(plan, price) {
  if (!price || plan.kind !== "subscription" || interval !== "annual") return "";
  return `${format(Math.round(price.amount_minor / 12), price.currency)} aylık karşılığı`;
}

function featureList(plan) {
  const entitlement = plan.entitlements || {};
  const minutes = entitlement.minutes ?? plan.minutes ?? FALLBACK_MINUTES[plan.code];
  const formats = entitlement.export_formats || plan.export_formats || [];
  const download = entitlement.download_enabled ?? plan.download_enabled ?? plan.export_enabled;
  const visual = entitlement.visual_translation ?? plan.visual_translation;
  const list = [
    minutes == null ? `${entitlement.team_seats || plan.team_seats || 10} kullanıcı` : `${Number(minutes).toLocaleString("tr-TR")} ${plan.kind === "subscription" || plan.kind === "free" ? "dk / ay" : "dakika"}`,
    download ? `Tam sonuç · ${formats.join(", ").toUpperCase()} · ZIP` : "Sınırlı önizleme · indirme yok",
    `${entitlement.quiz_questions ?? plan.quiz_questions ?? "∞"} quiz sorusu`,
    `${entitlement.flashcards ?? plan.flashcards ?? "∞"} bilgi kartı`,
    visual ? "Slayt/ekran yazılarını çevirme" : "Görsel içi çeviri yok",
    plan.priority === "priority" ? "Öncelikli işleme" : "Standart işleme",
  ];
  return list;
}

function renderAccount() {
  const logged = Boolean(token && account);
  $("authForm").hidden = logged;
  $("accountStatus").hidden = !logged;
  $("accountLink").textContent = logged ? (account.user.first_name || "Hesabım") : "Giriş";
  $("accountLink").href = logged ? "/account.html" : "/login.html?next=/plans.html";
  if (!logged) return;
  $("accountEmail").textContent = account.user.email;
  $("accountPlan").textContent = `Mevcut plan: ${COPY[account.plan.code]?.[0] || account.plan.code}`;
  $("accountRemaining").textContent = account.remaining_minutes == null ? "∞" : `${Number(account.remaining_minutes).toLocaleString("tr-TR")} dk`;
}

function renderHealth() {
  if (!health) return;
  const note = $("commerceReadiness");
  const configured = Boolean(health.paytr?.configured);
  if (configured) {
    note.hidden = false;
    note.innerHTML = `<strong>Güvenli kart ödeme ekranı hazır.</strong> Kart bilgileri PayTR ekranında işlenir. ${health.paytr.automatic_renewal ? "Otomatik yenileme etkin." : "Otomatik yenileme, PayTR kart saklama yetkisi açılana kadar manuel durumdadır."}`;
  } else {
    note.hidden = false;
    note.innerHTML = `<strong>Kartlı ödeme yapılandırma aşamasında.</strong> TRY seçildiğinde havale/EFT siparişi oluşturabilirsin. Plan ve fiyat ekranı hazırdır; canlı satış yalnız ödeme ve resmî işletme bilgileri tamamlandığında açılır.`;
  }
}

function renderPlans() {
  if (!catalog) return;
  const map = new Map((catalog.plans || []).map(plan => [plan.code, plan]));
  $("plansGrid").innerHTML = ORDER.map(code => {
    const plan = map.get(code);
    if (!plan) return "";
    const price = priceFor(plan);
    const current = account?.plan?.code === code;
    const priceText = price ? format(price.amount_minor, price.currency) : (code === "free" ? format(0, currency) : "Teklif");
    const suffix = plan.kind === "subscription" ? (interval === "annual" ? "/ yıl" : "/ ay") : (plan.kind === "one_time" ? "tek ödeme" : "");
    const annual = plan.kind === "subscription" && interval === "annual" ? '<span class="annual-saving">12 ay erişim · 10 aylık ücret</span>' : "";
    const currentText = current ? "Mevcut plan" : code === "business" ? "Bize ulaş" : "Satın al";
    const disabled = current || code === "free";
    return `<article id="plan-${esc(code)}" class="plan-card ${plan.featured ? "featured" : ""}">
      ${plan.featured ? '<span class="plan-badge">Popüler</span>' : ""}
      <h3>${esc(COPY[code]?.[0] || code)}</h3><p>${esc(COPY[code]?.[1] || "")}</p>
      <div class="plan-price">${esc(priceText)} <small>${esc(suffix)}</small><small class="monthly-equivalent">${esc(monthlyEquivalent(plan, price))}</small>${annual}</div>
      <ul class="plan-features">${featureList(plan).map(value => `<li>${esc(value)}</li>`).join("")}</ul>
      <button class="plan-action" type="button" data-plan="${esc(code)}" ${disabled ? "disabled" : ""}>${esc(currentText)}</button>
    </article>`;
  }).join("");
  document.querySelectorAll(".plan-action[data-plan]").forEach(button => button.onclick = () => buy(button.dataset.plan));
  const requested = new URLSearchParams(location.search).get("plan");
  if (requested && map.has(requested)) setTimeout(() => document.getElementById(`plan-${requested}`)?.scrollIntoView({behavior:"smooth",block:"center"}), 50);
}

function showTransfer(order) {
  $("transferReference").textContent = order.reference;
  $("transferAmount").textContent = format(order.amount_minor, order.currency || "TRY");
  $("transferIban").textContent = String(order.bank.iban || "").replace(/(.{4})/g, "$1 ").trim();
  $("transferHolder").textContent = order.bank.account_holder || "";
  $("transferInstruction").textContent = `${order.instruction} Sipariş referansı: ${order.reference}`;
  $("transferSupport").href = `mailto:${encodeURIComponent(order.support_email)}?subject=${encodeURIComponent(`LectureSift ${order.reference}`)}`;
  $("transferPanel").hidden = false;
  $("transferPanel").scrollIntoView({behavior:"smooth",block:"center"});
}

async function manualTransfer(plan, selectedInterval) {
  if (currency !== "TRY") throw Object.assign(new Error("Havale/EFT yalnız TRY siparişlerinde kullanılabilir. Kart ödeme yapılandırıldığında seçili para birimiyle devam edebilirsin."), {code:"LS-BILL-20"});
  const body = await api("/billing/manual-transfer/orders", {method:"POST", body:JSON.stringify({plan_code:plan.code, interval:selectedInterval})});
  showTransfer(body.order);
}

function openCheckout(body) {
  activePurchase = body.purchase.reference;
  $("checkoutTitle").textContent = `${COPY[body.purchase.plan_code]?.[0] || body.purchase.plan_code} · ${format(body.purchase.amount_minor, body.purchase.currency)}`;
  $("checkoutStatus").textContent = "PayTR güvenli kart ekranı açıldı. Ödeme sonucu sunucu bildirimiyle doğrulanacaktır.";
  $("checkoutFrame").src = body.iframe.url;
  $("checkoutOverlay").hidden = false;
}

function closeCheckout() {
  $("checkoutOverlay").hidden = true;
  $("checkoutFrame").src = "about:blank";
  activePurchase = "";
  clearTimeout(pollTimer);
}

async function pollPurchase(reference, attempt = 0) {
  if (!reference || attempt > 45) {
    $("checkoutStatus").textContent = "Ödeme sonucu henüz doğrulanmadı. Hesabım sayfasındaki ödeme geçmişinden kontrol edebilirsin.";
    return;
  }
  try {
    const body = await api(`/billing/purchases/${encodeURIComponent(reference)}`);
    const status = body.purchase.status;
    if (status === "paid") {
      $("checkoutStatus").textContent = "Ödeme doğrulandı. Plan ve indirme hakların açıldı.";
      await loadAccount();
      renderPlans();
      setTimeout(closeCheckout, 1400);
      return;
    }
    if (["failed","review_required"].includes(status)) {
      $("checkoutStatus").textContent = status === "review_required" ? "Ödeme tutarı kontrol için incelemeye alındı." : "Ödeme tamamlanamadı.";
      return;
    }
  } catch {}
  pollTimer = setTimeout(() => pollPurchase(reference, attempt + 1), 2000);
}

async function buy(planCode) {
  if (!token) {
    location.href = `/login.html?next=${encodeURIComponent(`/plans.html?plan=${planCode}&interval=${interval}`)}`;
    return;
  }
  const plan = catalog.plans.find(item => item.code === planCode);
  if (!plan) return;
  if (plan.kind === "quote") { location.href = "/contact.html?subject=business"; return; }
  const selectedInterval = plan.kind === "one_time" ? "one_time" : interval;
  try {
    if (health?.paytr?.configured) {
      const body = await api("/billing/paytr/checkout", {method:"POST", body:JSON.stringify({plan_code:plan.code, interval:selectedInterval, currency})});
      openCheckout(body);
      pollPurchase(body.purchase.reference);
      return;
    }
    await manualTransfer(plan, selectedInterval);
  } catch (error) {
    if (currency === "TRY" && error.code?.startsWith("LS-PAY")) {
      try { await manualTransfer(plan, selectedInterval); return; } catch (fallback) { showError(fallback.message, fallback.code); return; }
    }
    showError(error.message, error.code);
  }
}

async function loadAccount() {
  if (!token) { account = null; renderAccount(); return; }
  try {
    account = (await api("/billing/me")).account;
  } catch {
    token = "";
    account = null;
    localStorage.removeItem(TOKEN_KEY);
  }
  renderAccount();
}

async function load() {
  try {
    const [catalogBody, healthBody] = await Promise.all([
      api(`/billing/plans?currency=${encodeURIComponent(currency)}`, {}, false),
      api("/billing/commerce/health", {}, false),
    ]);
    catalog = catalogBody;
    health = healthBody;
  } catch (error) {
    showError(error.message, error.code);
    const fallback = FALLBACK_MONTHLY[currency] || FALLBACK_MONTHLY.USD;
    catalog = {supported_currencies:Object.keys(FALLBACK_MONTHLY), plans:ORDER.map(code => ({code,kind:["mini","credit"].includes(code)?"one_time":["lite","plus","pro","max"].includes(code)?"subscription":code === "business"?"quote":"free",minutes:FALLBACK_MINUTES[code],priority:["pro","max","business"].includes(code)?"priority":"standard",featured:code === "plus",download_enabled:code !== "free",visual_translation:code !== "free",display_price:fallback[code] == null?null:{currency,amount_minor:fallback[code]},interval_prices:["lite","plus","pro","max"].includes(code)&&fallback[code]!=null?{monthly:{currency,amount_minor:fallback[code]},annual:{currency,amount_minor:fallback[code]*10}}:{one_time:fallback[code]==null?null:{currency,amount_minor:fallback[code]}},entitlements:{minutes:FALLBACK_MINUTES[code],quiz_questions:code === "free"?10:code === "mini"||code === "credit"||code === "lite"?20:30,flashcards:code === "free"?20:code === "mini"||code === "credit"||code === "lite"?40:60,export_formats:code === "free"?[]:["pdf","docx","txt"],download_enabled:code !== "free",visual_translation:code !== "free"}}))};
    health = {paytr:{configured:false,automatic_renewal:false}};
  }
  populateCurrencies();
  await loadAccount();
  renderHealth();
  renderPlans();
}

$("billingCycle").querySelectorAll("button").forEach(button => button.onclick = () => {
  interval = button.dataset.interval;
  $("billingCycle").querySelectorAll("button").forEach(item => item.classList.toggle("active", item === button));
  const params = new URLSearchParams(location.search); params.set("interval", interval); history.replaceState(null,"",`${location.pathname}?${params}`);
  renderPlans();
});
$("billingCycle").querySelector(`[data-interval="${interval}"]`)?.click();
$("billingCurrency").onchange = () => { currency = $("billingCurrency").value; localStorage.setItem("lecturesift-currency", currency); load(); };
$("closeError").onclick = () => { $("errorBox").hidden = true; };
$("closeCheckout").onclick = closeCheckout;
$("checkoutOverlay").addEventListener("click", event => { if (event.target === $("checkoutOverlay")) closeCheckout(); });
window.addEventListener("message", event => {
  if (event.origin !== location.origin || event.data?.type !== "lecturesift-payment-return") return;
  const reference = event.data.order || activePurchase;
  $("checkoutStatus").textContent = event.data.status === "failed" ? "Ödeme ekranı başarısız sonuç döndürdü; sunucu bildirimi kontrol ediliyor." : "Ödeme ekranı tamamlandı; sunucu bildirimi doğrulanıyor.";
  pollPurchase(reference);
});

load();
