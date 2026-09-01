const API = "https://api.lecturesift.com";
const TOKEN_KEY = "lecturesift-billing-token";
const ORDER = ["free", "test", "credit", "lite", "plus", "pro", "max", "business"];
const LOCALE_DATA = window.LECTURESIFT_LOCALE_DATA || {
  countries: [], currencies: ["TRY", "USD", "EUR", "GBP"], currencyForCountry: {},
};
const ZERO_DECIMAL_CURRENCIES = new Set(["JPY", "KRW"]);
const PLANS_I18N = window.LectureSiftI18n || {language:"tr",locale:"tr-TR",t:(key,fallback)=>fallback || key};
const pt = (key, fallback) => PLANS_I18N.t(key, fallback);

function recordPlanAnalytics(name, parameters = {}) {
  if (window.LectureSiftAnalytics?.track) return void window.LectureSiftAnalytics.track(name, parameters);
  window.__lecturesiftAnalyticsQueue = window.__lecturesiftAnalyticsQueue || [];
  window.__lecturesiftAnalyticsQueue.push({type:"event", name, parameters});
}
const COPY = {
  free: ["Ücretsiz", "Denemek ve kısa dersler için"],
  test: ["1 TL Test Paketi", "Canlı kart ödemesini küçük tutarla denemek için"],
  credit: ["Dakika Paketi", "Abonelik olmadan ek kullanım"],
  lite: ["Lite", "Düzenli bireysel çalışma"],
  plus: ["Plus", "Yoğun ders dönemi ve çoklu kaynaklar"],
  pro: ["Pro", "Uzun kayıtlar ve öncelikli işleme"],
  max: ["Max", "En yüksek bireysel kapasite"],
  business: ["Business", "Ekipler ve kurumlar için"],
};
const ALL_SUMMARIES = ["detailed"];
const PLAN_LIMITS = {
  free: {max_files_per_job:3, max_media_upload_mb:100, max_document_upload_mb:25, max_minutes_per_job:30, max_document_pages:50, max_ocr_pages:20, max_document_characters:1500000},
  test: {max_files_per_job:1, max_media_upload_mb:25, max_document_upload_mb:10, max_minutes_per_job:1, max_document_pages:10, max_ocr_pages:5, max_document_characters:1500000},
  credit: {max_files_per_job:8, max_media_upload_mb:500, max_document_upload_mb:50, max_minutes_per_job:180, max_document_pages:150, max_ocr_pages:50, max_document_characters:1500000},
  lite: {max_files_per_job:12, max_media_upload_mb:750, max_document_upload_mb:75, max_minutes_per_job:180, max_document_pages:250, max_ocr_pages:75, max_document_characters:1500000},
  plus: {max_files_per_job:16, max_media_upload_mb:1024, max_document_upload_mb:100, max_minutes_per_job:300, max_document_pages:350, max_ocr_pages:100, max_document_characters:1500000},
  pro: {max_files_per_job:24, max_media_upload_mb:1024, max_document_upload_mb:100, max_minutes_per_job:600, max_document_pages:500, max_ocr_pages:150, max_document_characters:1500000},
  max: {max_files_per_job:24, max_media_upload_mb:1024, max_document_upload_mb:100, max_minutes_per_job:900, max_document_pages:500, max_ocr_pages:150, max_document_characters:1500000},
  business: {max_files_per_job:24, max_media_upload_mb:1024, max_document_upload_mb:100, max_minutes_per_job:1440, max_document_pages:500, max_ocr_pages:150, max_document_characters:1500000},
};
const FALLBACK_META = {
  free: {kind: "free", minutes: 60, priority: "standard", team_seats: 1, featured: false, entitlements: {minutes: 60, quiz_questions: 10, flashcards: 20, export_formats: ["pdf"], summary_profiles: ALL_SUMMARIES, limits: PLAN_LIMITS.free, team_seats: 1, ad_free: false, rewarded_minutes_eligible: true, download_enabled: false}},
  test: {kind: "one_time", minutes: 1, priority: "standard", team_seats: 1, featured: false, entitlements: {minutes: 1, quiz_questions: 1, flashcards: 1, export_formats: ["pdf"], summary_profiles: ALL_SUMMARIES, limits: PLAN_LIMITS.test, team_seats: 1, ad_free: false, rewarded_minutes_eligible: true, download_enabled: true}},
  credit: {kind: "one_time", minutes: 180, priority: "standard", team_seats: 1, featured: false, entitlements: {minutes: 180, quiz_questions: 20, flashcards: 40, export_formats: ["pdf", "docx", "txt"], summary_profiles: ALL_SUMMARIES, limits: PLAN_LIMITS.credit, team_seats: 1, ad_free: false, rewarded_minutes_eligible: true, download_enabled: true}},
  lite: {kind: "subscription", minutes: 600, priority: "standard", team_seats: 1, featured: false, entitlements: {minutes: 600, quiz_questions: 20, flashcards: 40, export_formats: ["pdf", "docx", "txt"], summary_profiles: ALL_SUMMARIES, limits: PLAN_LIMITS.lite, team_seats: 1, ad_free: true, rewarded_minutes_eligible: false, download_enabled: true}},
  plus: {kind: "subscription", minutes: 1800, priority: "standard", team_seats: 1, featured: true, entitlements: {minutes: 1800, quiz_questions: 30, flashcards: 60, export_formats: ["pdf", "docx", "txt"], summary_profiles: ALL_SUMMARIES, limits: PLAN_LIMITS.plus, team_seats: 1, ad_free: true, rewarded_minutes_eligible: false, download_enabled: true}},
  pro: {kind: "subscription", minutes: 5000, priority: "priority", team_seats: 1, featured: false, entitlements: {minutes: 5000, quiz_questions: 30, flashcards: 60, export_formats: ["pdf", "docx", "txt"], summary_profiles: ALL_SUMMARIES, limits: PLAN_LIMITS.pro, team_seats: 1, ad_free: true, rewarded_minutes_eligible: false, download_enabled: true}},
  max: {kind: "subscription", minutes: 12000, priority: "priority", team_seats: 1, featured: false, entitlements: {minutes: 12000, quiz_questions: 30, flashcards: 60, export_formats: ["pdf", "docx", "txt"], summary_profiles: ALL_SUMMARIES, limits: PLAN_LIMITS.max, team_seats: 1, ad_free: true, rewarded_minutes_eligible: false, download_enabled: true}},
  business: {kind: "quote", minutes: null, priority: "priority", team_seats: 10, featured: false, entitlements: {minutes: null, quiz_questions: null, flashcards: null, export_formats: ["pdf", "docx", "txt"], summary_profiles: ALL_SUMMARIES, limits: PLAN_LIMITS.business, team_seats: 10, ad_free: true, rewarded_minutes_eligible: false, download_enabled: true}},
};
const FALLBACK_PRICES = {
  TRY: [0, 100, 19900, 27900, 44900, 99900, 199900, null],
  USD: [0, null, 499, 699, 999, 2499, 4999, null],
  EUR: [0, null, 499, 649, 949, 2399, 4799, null],
  GBP: [0, null, 399, 599, 849, 2099, 4199, null],
  CAD: [0, null, 699, 949, 1349, 3399, 6799, null],
  AUD: [0, null, 799, 1099, 1549, 3799, 7599, null],
  NZD: [0, null, 899, 1199, 1699, 4199, 8399, null],
  JPY: [0, null, 750, 1050, 1500, 3750, 7500, null],
  KRW: [0, null, 6900, 9500, 13900, 34900, 69900, null],
  CNY: [0, null, 3500, 4900, 6900, 17500, 34900, null],
  INR: [0, null, 39900, 54900, 79900, 199900, 399900, null],
  BRL: [0, null, 2499, 3499, 4999, 12499, 24999, null],
  MXN: [0, null, 9900, 13900, 19900, 49900, 99900, null],
  CHF: [0, null, 449, 599, 849, 2199, 4399, null],
  SEK: [0, null, 5299, 7299, 10499, 25999, 51999, null],
  NOK: [0, null, 5499, 7699, 10999, 27499, 54999, null],
  DKK: [0, null, 3499, 4499, 6699, 16999, 33999, null],
  PLN: [0, null, 1999, 2699, 3999, 9999, 19999, null],
  AED: [0, null, 1899, 2599, 3699, 9199, 18399, null],
  SAR: [0, null, 1899, 2599, 3799, 9399, 18799, null],
  SGD: [0, null, 699, 949, 1349, 3399, 6799, null],
  HKD: [0, null, 3899, 5499, 7799, 19499, 38999, null],
};

let catalog = null;
let account = null;
let providers = [];
let commerceIdentity = {configured: false};
let manualTransfer = {available: false, bank: null};
let currency = "TRY";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]);

function regionCode() {
  const saved = localStorage.getItem("lecturesift-country");
  if (saved) return saved.toUpperCase();
  return (navigator.language.split("-")[1] || "").toUpperCase();
}

function detectedCurrency() {
  const saved = localStorage.getItem("lecturesift-currency");
  if (LOCALE_DATA.currencies.includes(saved)) return saved;
  return LOCALE_DATA.currencyForCountry[regionCode()] || "USD";
}

function currencyLabel(code) {
  try {
    const parts = new Intl.NumberFormat(navigator.language, {style: "currency", currency: code})
      .formatToParts(0);
    const symbol = parts.find(part => part.type === "currency")?.value || code;
    return `${code} ${symbol}`;
  } catch { return code; }
}

function populateCurrencies() {
  const select = $("billingCurrency");
  if (!select) return;
  const selected = currency || detectedCurrency();
  select.replaceChildren(...LOCALE_DATA.currencies.map(code => new Option(currencyLabel(code), code)));
  select.value = LOCALE_DATA.currencies.includes(selected) ? selected : "USD";
}

function format(amount, code) {
  const divisor = ZERO_DECIMAL_CURRENCIES.has(code) ? 1 : 100;
  return new Intl.NumberFormat(navigator.language, {
    style: "currency", currency: code, maximumFractionDigits: divisor === 1 ? 0 : 2,
  }).format(amount / divisor);
}

function showError(message, code = "LS-BILL-20") {
  $("errorMessage").textContent = message;
  $("errorCode").textContent = code;
  $("errorBox").hidden = false;
}

function showPaymentRedirectResult() {
  const params = new URLSearchParams(location.search);
  const result = params.get("payment");
  const reference = params.get("order");
  if (!result || !reference) return;
  const orderNumber = (account?.payment_orders || []).find(
    item => item.reference === reference,
  )?.order_number || reference;
  const orderLabel = `${pt("payment.orderNumber", "Sipariş no")}: ${orderNumber}`;
  if (result === "failed") {
    const order = (account?.payment_orders || []).find(item => item.reference === reference);
    showError(
      `${order?.failure_message || pt("payment.declined", "Ödeme banka veya iyzico tarafından onaylanmadı.")} ${orderLabel}`,
      order?.failure_code || "LS-PAY-DECLINED",
    );
  } else if (result === "verification_failed") {
    showError(`${pt("order.failed", "Ödeme sonucu doğrulanamadı.")} ${orderLabel}`, "LS-PAY-VERIFY");
  }
}

function openRequestedPlan() {
  const params = new URLSearchParams(location.search);
  const planCode = params.get("plan");
  if (!planCode || !catalog?.plans?.some(plan => plan.code === planCode)) return;
  const requestedInterval = params.get("interval");
  const plan = catalog.plans.find(item => item.code === planCode);
  const interval = plan?.kind === "one_time"
    ? "one_time"
    : (requestedInterval === "annual" ? "annual" : "monthly");
  params.delete("plan");
  params.delete("interval");
  history.replaceState({}, "", `${location.pathname}${params.size ? `?${params}` : ""}${location.hash}`);
  void buy(planCode, interval);
}

async function api(path, options = {}) {
  const headers = {"Content-Type": "application/json", ...(options.headers || {})};
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${API}${path}`, {...options, headers, cache: "no-store"});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw Object.assign(
    new Error(body?.detail?.message || body?.message || pt("error.request", "İstek tamamlanamadı.")),
    {code: body?.detail?.code},
  );
  return body;
}

function summaryEntitlement() {
  return pt("plans.alwaysDetailed", "Her zaman ayrıntılı ve kapsamlı");
}

function planLabel(code) { return pt(`plan.${code}`, COPY[code]?.[0] || code); }
function visibleOrder() { return ORDER.filter(code => code !== "test" || currency === "TRY"); }

function renderCompare() {
  const plans = visibleOrder().map(code => catalog?.plans?.find(plan => plan.code === code)).filter(Boolean);
  $("compareHead").innerHTML = `<tr><th>${esc(pt("plans.right", "Hak"))}</th>${plans.map(plan => `<th>${esc(planLabel(plan.code))}</th>`).join("")}</tr>`;
  const yes = pt("common.yes", "Evet"), all = pt("common.all", "Tümü"), standard = pt("priority.standard", "Standart"), priority = pt("priority.priority", "Öncelikli");
  const rows = [
    [pt("plans.billingType", "Ödeme türü"), plan => plan.kind === "subscription" ? pt("plans.subscription", "Aylık abonelik") : plan.kind === "one_time" ? pt("plans.oneTime", "Tek ödeme") : plan.kind === "free" ? pt("plan.free", "Ücretsiz") : pt("plans.quote", "Teklif")],
    [pt("plans.minutes", "İşleme dakikası"), plan => plan.entitlements?.minutes == null ? "∞" : Number(plan.entitlements.minutes).toLocaleString(PLANS_I18N.locale)],
    [pt("plans.singleJobLimit", "Tek iş süre sınırı"), plan => `${Number(plan.entitlements?.limits?.max_minutes_per_job || 0).toLocaleString(PLANS_I18N.locale)} ${pt("unit.minuteShort", "dk")}`],
    [pt("plans.filesPerJob", "Bir işte kaynak sayısı"), plan => Number(plan.entitlements?.limits?.max_files_per_job || 0).toLocaleString(PLANS_I18N.locale)],
    [pt("plans.mediaUploadLimit", "Bir işte toplam medya boyutu"), plan => `${Number(plan.entitlements?.limits?.max_media_upload_mb || 0).toLocaleString(PLANS_I18N.locale)} MB`],
    [pt("plans.documentUploadLimit", "Bir işte toplam belge boyutu"), plan => `${Number(plan.entitlements?.limits?.max_document_upload_mb || 0).toLocaleString(PLANS_I18N.locale)} MB`],
    [pt("plans.documentPageLimit", "Belge sayfası / iş"), plan => Number(plan.entitlements?.limits?.max_document_pages || 0).toLocaleString(PLANS_I18N.locale)],
    [pt("plans.ocrPageLimit", "OCR sayfası / iş"), plan => Number(plan.entitlements?.limits?.max_ocr_pages || 0).toLocaleString(PLANS_I18N.locale)],
    [pt("plans.documentCharacterLimit", "Çıkarılan metin karakteri / iş"), plan => Number(plan.entitlements?.limits?.max_document_characters || 0).toLocaleString(PLANS_I18N.locale)],
    [pt("plans.quiz", "Quiz sorusu / işlem"), plan => plan.entitlements?.quiz_questions ?? "∞"],
    [pt("plans.cards", "Bilgi kartı / işlem"), plan => plan.entitlements?.flashcards ?? "∞"],
    [pt("plans.summaries", "Özet çıktısı"), () => summaryEntitlement()],
    [pt("plans.exports", "Dosya biçimleri"), plan => plan.entitlements?.download_enabled === false ? pt("plans.previewOnly", "Sitede önizleme · dosya indirme yok") : (plan.entitlements?.export_formats || []).join(", ").toUpperCase()],
    [pt("plans.priority", "İşlem önceliği"), plan => plan.priority === "priority" ? priority : standard],
    [pt("plans.teamSeats", "Ekip kullanıcıları"), plan => plan.entitlements?.team_seats || plan.team_seats || 1],
    [pt("plans.multiSource", "Çoklu video ve ayrı ses/slayt"), () => yes],
    [pt("plans.languages", "Kaynak ve çıktı dilleri"), () => `13 ${pt("plans.languagesUnit", "dil")}`],
    [pt("plans.outputs", "Transkript, not ve zaman damgası"), () => all],
    [pt("plans.adExperience", "Reklam deneyimi"), plan => plan.entitlements?.ad_free ? pt("plans.adFree", "Reklamsız kullanım") : pt("plans.rewardedOption", "İsteğe bağlı reklamla ek dakika")],
  ];
  $("compareBody").innerHTML = rows.map(([label, value]) => `<tr><td>${esc(label)}</td>${plans.map(plan => `<td>${esc(value(plan))}</td>`).join("")}</tr>`).join("");
}

function normalizeCatalog(remote, selected) {
  const amounts = FALLBACK_PRICES[selected] || FALLBACK_PRICES.USD;
  const remotePlans = new Map((remote?.plans || []).map(plan => [plan.code, plan]));
  return {
    ...(remote || {}),
    selected_currency: selected,
    supported_currencies: LOCALE_DATA.currencies,
    plans: ORDER.map((code, index) => {
      const fallbackPlan = FALLBACK_META[code];
      const remotePlan = remotePlans.get(code) || {};
      const plan = {
        code, ...fallbackPlan, ...remotePlan,
        entitlements: {...fallbackPlan.entitlements, ...(remotePlan.entitlements || {}), summary_profiles: ALL_SUMMARIES},
      };
      const fallbackAmount = amounts[index];
      const remotePrice = plan.display_price;
      const selectedPrice = remotePrice?.currency === selected
        ? remotePrice
        : (fallbackAmount == null ? null : {currency: selected, amount_minor: fallbackAmount});
      return {...plan, display_price: selectedPrice};
    }),
  };
}

function renderAccount() {
  if (!account) return;
  $("authForm").hidden = true;
  $("accountStatus").hidden = false;
  $("accountEmail").textContent = account.user.email;
  $("accountPlan").textContent = planLabel(account.plan.code);
  $("accountRemaining").textContent = account.remaining_minutes == null ? "∞" : `${account.remaining_minutes} ${pt("plans.minuteUnit", "dakika")}`;
}

function paytrStatus() {
  return providers.find(provider => provider.code === "paytr") || {configured: false, currencies: []};
}

function iyzicoStatus() {
  return providers.find(provider => provider.code === "iyzico") || {configured: false, currencies: []};
}

function automaticBankTransferStatus() {
  const iyzico = iyzicoStatus();
  return {
    ...iyzico,
    configured: Boolean(
      iyzico.configured
      && iyzico.capabilities?.includes("bank_transfer")
      && iyzico.currencies?.includes("TRY")
    ),
  };
}

function cardProviderStatus() {
  const iyzico = iyzicoStatus();
  if (iyzico.configured) return iyzico;
  const paytr = paytrStatus();
  if (paytr.configured) return paytr;
  return iyzico;
}

function pendingCardMessage() {
  return pt("payment.cardPending", "iyzico canlı ödeme anahtarları güvenli sunucu ayarına eklendiğinde kartlı ödeme açılacak.");
}

function renderPaymentStatus() {
  const provider = cardProviderStatus();
  if (!$('cardAvailability')) return;
  $('cardAvailability').textContent = provider.configured
    ? `${provider.code === "iyzico" ? "iyzico" : "PayTR"} · ${pt("payment.providerReady", "Kart ve güvenli ödeme yöntemleri kullanıma hazır.")}`
    : pendingCardMessage();
  $("protectedBankAvailability").textContent = automaticBankTransferStatus().configured
    ? pt("payment.protectedAvailable", "iyzico siparişle otomatik eşleştirir; onay geldiğinde paket otomatik etkinleşir.")
    : pt("payment.protectedNotConfigured", "iyzico Korumalı Havale/EFT henüz kullanıma açık değil.");
  $("bankAvailability").textContent = manualTransfer.available
    ? pt("payment.available", "IBAN havalesi kullanılabilir; sipariş ödeme kontrolünden sonra etkinleşir.")
    : pt("payment.notConfigured", "IBAN havale bilgileri henüz etkin değil.");
}

function renderPlans() {
  const map = new Map((catalog?.plans || []).map(plan => [plan.code, plan]));
  $("plansGrid").innerHTML = visibleOrder().map(code => {
    const plan = map.get(code);
    if (!plan) return "";
    const entitlements = plan.entitlements || {};
    const limits = entitlements.limits || {};
    const price = plan.display_price || plan.manual_price;
    const current = account?.plan?.code === code;
    const priceText = price
      ? format(price.amount_minor, price.currency || currency)
      : (code === "free" ? format(0, currency) : pt("plans.quote", "Teklif"));
    const suffix = plan.kind === "subscription" ? pt("plans.perMonth", "/ ay") : (plan.kind === "one_time" ? pt("plans.oneTimeShort", "tek ödeme") : "");
    const minutes = entitlements.minutes ?? plan.minutes;
    const minutesText = minutes == null
      ? `${entitlements.team_seats || plan.team_seats || 10} ${pt("plans.userUnit", "kullanıcı")}`
      : `${Number(minutes).toLocaleString(PLANS_I18N.locale)} ${plan.kind === "subscription" || plan.kind === "free" ? pt("plans.minutesPerMonth", "dk / ay") : pt("plans.minuteUnit", "dakika")}`;
    const actions = plan.kind === "subscription"
      ? `<div class="plan-card-actions"><button class="plan-action" data-plan="${esc(code)}" data-interval="monthly" ${current ? "disabled" : ""}>${esc(pt("rollout.chooseMonthly", "Aylık seç"))}</button><button class="plan-action" data-plan="${esc(code)}" data-interval="annual" ${current ? "disabled" : ""}>${esc(pt("rollout.annual", "Yıllık"))} · ${esc(price ? format(price.amount_minor * 10, price.currency || currency) : "")}</button></div>`
      : `<button class="plan-action" data-plan="${esc(code)}" data-interval="${plan.kind === "one_time" ? "one_time" : "monthly"}" ${current || code === "free" || code === "business" ? "disabled" : ""}>${esc(current ? pt("plans.current", "Mevcut plan") : (code === "business" ? pt("plans.contact", "Bize ulaş") : pt("plans.select", "Planı seç")))}</button>`;
    return `<article class="plan-card ${plan.featured ? "featured" : ""}">
      ${plan.featured ? `<span class="plan-badge">${esc(pt("plans.popular", "Popüler"))}</span>` : ""}
      <h3>${esc(planLabel(code))}</h3><p>${esc(pt(`plan.${code}.description`, COPY[code][1]))}</p>
      <div class="plan-price">${esc(priceText)} <small>${suffix}</small></div>
      <ul class="plan-features">
        <li>${esc(minutesText)}</li>
        <li>${Number(limits.max_minutes_per_job || 0).toLocaleString(PLANS_I18N.locale)} ${esc(pt("plans.minutesPerJob", "dk / tek iş"))}</li>
        <li>${Number(limits.max_files_per_job || 0).toLocaleString(PLANS_I18N.locale)} ${esc(pt("plans.filesPerJobShort", "kaynak / iş"))}</li>
        <li>${Number(limits.max_media_upload_mb || 0).toLocaleString(PLANS_I18N.locale)} MB ${esc(pt("plans.mediaShort", "medya"))} · ${Number(limits.max_document_upload_mb || 0).toLocaleString(PLANS_I18N.locale)} MB ${esc(pt("plans.documentShort", "belge"))}</li>
        <li>${Number(limits.max_document_pages || 0).toLocaleString(PLANS_I18N.locale)} ${esc(pt("plans.pagesShort", "sayfa"))} · ${Number(limits.max_ocr_pages || 0).toLocaleString(PLANS_I18N.locale)} OCR</li>
        <li>${entitlements.quiz_questions ?? "∞"} ${esc(pt("plans.quizShort", "quiz sorusu"))}</li>
        <li>${entitlements.flashcards ?? "∞"} ${esc(pt("plans.cardsShort", "bilgi kartı"))}</li>
        <li>${esc(summaryEntitlement())} ${esc(pt("plans.summaryShort", "özet"))}</li>
        <li>${esc(entitlements.download_enabled === false ? pt("plans.previewOnly", "Sitede önizleme · dosya indirme yok") : (entitlements.export_formats || []).join(", ").toUpperCase())}</li>
        <li>${esc(plan.priority === "priority" ? pt("priority.priority", "Öncelikli") : pt("priority.standard", "Standart"))} ${esc(pt("plans.processingSuffix", "işleme"))}</li>
        <li>${esc(entitlements.ad_free ? pt("plans.adFree", "Reklamsız kullanım") : pt("plans.rewardedOption", "İsteğe bağlı reklamla ek dakika"))}</li>
      </ul>
      ${actions}
    </article>`;
  }).join("");
  document.querySelectorAll(".plan-action[data-plan]").forEach(button => {
    button.onclick = () => buy(button.dataset.plan, button.dataset.interval);
  });
  renderCompare();
}

async function buy(planCode, interval = "monthly") {
  if (!localStorage.getItem(TOKEN_KEY)) {
    location.href = `/login.html?next=${encodeURIComponent("/plans.html")}`;
    return;
  }
  const provider = cardProviderStatus();
  const cardAvailable = Boolean(provider.configured && provider.currencies?.includes(currency));
  const protectedAvailable = Boolean(automaticBankTransferStatus().configured && currency === "TRY");
  const manualAvailable = Boolean(manualTransfer.available && currency === "TRY");
  if (!cardAvailable && !protectedAvailable && !manualAvailable) {
    showError(pt("plans.globalPending", "Global kart ve yerel ödeme yöntemleri ödeme sağlayıcısı etkinleştiğinde açılacak. Şimdilik havale için TRY seçebilirsin."));
    return;
  }
  $("checkoutPlanCode").value = planCode;
  $("checkoutInterval").value = interval;
  $("checkoutTitle").textContent = planLabel(planCode);
  const plan = catalog?.plans?.find(item => item.code === planCode);
  const price = plan?.display_price || plan?.manual_price;
  const multiplier = interval === "annual" ? 10 : 1;
  recordPlanAnalytics("begin_checkout", {
    currency: price?.currency || currency,
    value: price ? Number(price.amount_minor * multiplier) / 100 : 0,
    items: [{item_id: planCode, item_name: planLabel(planCode), quantity: 1}],
  });
  $("checkoutSummaryPlan").textContent = planLabel(planCode);
  $("checkoutSummaryInterval").textContent = interval === "annual"
    ? pt("rollout.annual", "Yıllık")
    : interval === "one_time"
    ? pt("plans.oneTime", "Tek ödeme")
    : pt("rollout.chooseMonthly", "Aylık");
  $("checkoutSummaryTotal").textContent = price
    ? format(price.amount_minor * multiplier, price.currency || currency)
    : pt("plans.quote", "Teklif");
  $("checkoutPhone").value = account?.user?.phone || "";
  $("checkoutFirstName").value = account?.user?.first_name || "";
  $("checkoutLastName").value = account?.user?.last_name || "";
  $("checkoutTerms").checked = false;
  $("checkoutEarlyPerformance").checked = false;
  $("checkoutCardButton").disabled = !commerceIdentity.configured || !cardAvailable;
  $("checkoutProtectedBankButton").disabled = !commerceIdentity.configured || !protectedAvailable;
  $("checkoutBankButton").disabled = !commerceIdentity.configured || !manualAvailable;
  $("checkoutNotice").textContent = !commerceIdentity.configured
    ? pt("payment.commercePending", "Satıcı/sağlayıcı kimliği ve iletişim bilgileri tamamlanmadan ödeme açılamaz.")
    : provider.configured
    ? pt("payment.providerReady", "Kart ve güvenli ödeme yöntemleri kullanıma hazır.")
    : pendingCardMessage();
  $("checkoutForm").hidden = false;
  $("bankTransferGuide").hidden = true;
  $("paytrFrame").hidden = true;
  $("checkoutPanel").hidden = false;
}

async function startHostedCheckout(preferredMethod = "card") {
  if (!$("checkoutForm").reportValidity()) return;
  const cardButton = $("checkoutCardButton");
  const protectedButton = $("checkoutProtectedBankButton");
  const manualButton = $("checkoutBankButton");
  const continueButton = $("bankTransferContinue");
  if ((preferredMethod === "card" && cardButton.disabled)
    || (preferredMethod === "bank_transfer" && protectedButton.disabled)) return;
  cardButton.disabled = true;
  protectedButton.disabled = true;
  manualButton.disabled = true;
  continueButton.disabled = true;
  $("checkoutNotice").textContent = preferredMethod === "bank_transfer"
    ? pt("payment.openingTransfer", "iyzico açılıyor; güvenli sayfada Havale/EFT seçeneğini seç.")
    : pt("payment.opening", "Güvenli ödeme açılıyor…");
  try {
    const planCode = $("checkoutPlanCode").value;
    recordPlanAnalytics("add_payment_info", {
      payment_type: preferredMethod === "bank_transfer" ? "iyzico_protected_bank_transfer" : "card",
      items: [{item_id: planCode, item_name: planLabel(planCode), quantity: 1}],
    });
    const body = await api("/billing/checkout", {
      method: "POST",
      body: JSON.stringify({
        plan_code: planCode,
        interval: $("checkoutInterval").value,
        currency,
        first_name: $("checkoutFirstName").value.trim(),
        last_name: $("checkoutLastName").value.trim(),
        billing_address: $("checkoutAddress").value.trim(),
        billing_city: $("checkoutCity").value.trim(),
        billing_zip_code: $("checkoutZipCode").value.trim(),
        phone: $("checkoutPhone").value.trim(),
        language: PLANS_I18N.language,
        terms_accepted:$("checkoutTerms").checked,
        early_performance_requested:$("checkoutEarlyPerformance").checked,
      }),
    });
    $("checkoutNotice").textContent = `${pt("payment.orderNumber", "Sipariş no")}: ${body.order.order_number}`;
    if (body.display_mode === "redirect" || body.provider === "iyzico") {
      location.assign(body.checkout_url);
      return;
    }
    $("checkoutForm").hidden = true;
    $("paytrFrame").src = body.checkout_url;
    $("paytrFrame").hidden = false;
  } catch (error) {
    $("checkoutNotice").textContent = error.message;
    const provider = cardProviderStatus();
    cardButton.disabled = !commerceIdentity.configured || !provider.configured || !provider.currencies?.includes(currency);
    protectedButton.disabled = !commerceIdentity.configured || !automaticBankTransferStatus().configured || currency !== "TRY";
    manualButton.disabled = !commerceIdentity.configured || currency !== "TRY" || !manualTransfer.available;
    continueButton.disabled = false;
  }
}

function showBankTransferGuide() {
  if (!$("checkoutForm").reportValidity() || $("checkoutProtectedBankButton").disabled) return;
  $("checkoutForm").hidden = true;
  $("bankTransferGuide").hidden = false;
  $("bankTransferContinue").focus();
}

function hideBankTransferGuide() {
  $("bankTransferGuide").hidden = true;
  $("checkoutForm").hidden = false;
  $("checkoutProtectedBankButton").focus();
}

async function createTransfer() {
  const bankButton = $("checkoutBankButton");
  if (bankButton.disabled || !$("checkoutForm").reportValidity()) return;
  const cardButton = $("checkoutCardButton");
  const protectedButton = $("checkoutProtectedBankButton");
  bankButton.disabled = true;
  cardButton.disabled = true;
  protectedButton.disabled = true;
  $("checkoutNotice").textContent = pt("payment.creatingTransfer", "Havale siparişi oluşturuluyor…");
  try {
    const body = await api("/billing/manual-transfer/orders", {
      method: "POST",
      body: JSON.stringify({
        plan_code: $("checkoutPlanCode").value,
        interval: $("checkoutInterval").value,
        first_name: $("checkoutFirstName").value.trim(),
        last_name: $("checkoutLastName").value.trim(),
        terms_accepted: $("checkoutTerms").checked,
        early_performance_requested: $("checkoutEarlyPerformance").checked,
        language: PLANS_I18N.language,
      }),
    });
    const order = body.order;
    $("transferReference").textContent = order.order_number || order.reference;
    $("transferAmount").textContent = format(order.amount_minor, order.currency || "TRY");
    $("transferIban").textContent = String(order.bank?.iban || "").replace(/(.{4})/g, "$1 ").trim();
    $("transferHolder").textContent = order.bank?.account_holder || "—";
    $("transferBank").textContent = order.bank?.bank_name || "—";
    $("transferInstruction").textContent = pt(
      "payment.transferReferenceInstruction",
      "Havale açıklamasına yalnızca sipariş numarasını yaz. Tutar ve açıklama eşleşmezse onay gecikebilir.",
    );
    if (order.support_email) {
      $("transferSupport").href = `mailto:${encodeURIComponent(order.support_email)}?subject=${encodeURIComponent(`LectureSift ${order.reference}`)}`;
      $("transferSupport").textContent = `${pt("payment.sendReceiptTo", "Ödeme desteği")}: ${order.support_email}`;
      $("transferSupport").hidden = false;
    } else {
      $("transferSupport").hidden = true;
    }
    recordPlanAnalytics("add_payment_info", {
      payment_type: "manual_bank_transfer",
      items: [{item_id: order.plan_code, item_name: planLabel(order.plan_code), quantity: 1}],
    });
    $("checkoutPanel").hidden = true;
    $("transferPanel").hidden = false;
    $("transferPanel").scrollIntoView({behavior: "smooth", block: "start"});
    try {
      account = (await api("/billing/me")).account;
      renderAccount();
    } catch { /* The order already exists; account refresh is non-critical. */ }
  } catch (error) {
    showError(error.message, error.code);
    const provider = cardProviderStatus();
    cardButton.disabled = !commerceIdentity.configured || !provider.configured || !provider.currencies?.includes(currency);
    protectedButton.disabled = !commerceIdentity.configured || !automaticBankTransferStatus().configured || currency !== "TRY";
    bankButton.disabled = !commerceIdentity.configured || currency !== "TRY" || !manualTransfer.available;
  }
}

async function load() {
  const selected = currency || detectedCurrency();
  currency = selected;
  populateCurrencies();
  $("billingCurrency").value = selected;
  try {
    const [remote, providerBody, transferBody] = await Promise.all([
      api(`/billing/plans?currency=${encodeURIComponent(selected)}`),
      api("/billing/providers"),
      api("/billing/manual-transfer"),
    ]);
    providers = providerBody.providers || [];
    commerceIdentity = providerBody.commerce_identity || {configured:false};
    manualTransfer = transferBody || {available:false, bank:null};
    catalog = normalizeCatalog(remote, selected);
    try { account = (await api("/billing/me")).account; } catch { account = null; }
    renderAccount();
    renderPlans();
    renderPaymentStatus();
    showPaymentRedirectResult();
    openRequestedPlan();
  } catch (error) { showError(error.message, error.code); }
}

$("billingCurrency").addEventListener("change", () => {
  currency = $("billingCurrency").value;
  localStorage.setItem("lecturesift-currency", currency);
  load();
});
$("closeError").onclick = () => { $("errorBox").hidden = true; };
$("checkoutClose").onclick = $("checkoutCancel").onclick = () => {
  $("checkoutPanel").hidden = true;
  $("checkoutForm").hidden = false;
  $("bankTransferGuide").hidden = true;
  $("paytrFrame").src = "about:blank";
};
$("checkoutBankButton").onclick = createTransfer;
$("checkoutProtectedBankButton").onclick = showBankTransferGuide;
$("bankTransferBack").onclick = hideBankTransferGuide;
$("bankTransferContinue").onclick = () => startHostedCheckout("bank_transfer");
$("checkoutForm").addEventListener("submit", async event => {
  event.preventDefault();
  await startHostedCheckout("card");
});
document.querySelectorAll("[data-copy-target]").forEach(button => {
  button.addEventListener("click", async () => {
    const target = $(button.dataset.copyTarget);
    if (!target) return;
    try {
      await navigator.clipboard.writeText(target.textContent.replace(/\s+/g, "").trim());
      const original = button.textContent;
      button.textContent = pt("common.copied", "Kopyalandı");
      setTimeout(() => { button.textContent = original; }, 1400);
    } catch { showError(pt("error.copy", "Bilgi kopyalanamadı."), "LS-UI-COPY"); }
  });
});
currency = detectedCurrency();
load();
