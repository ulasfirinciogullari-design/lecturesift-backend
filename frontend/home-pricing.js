/* Homepage price cards: the static markup carries the TRY list prices as a
   fallback; the live catalog (GET /billing/plans) replaces price, minutes and
   the entitlement bullets so the homepage never shows stale prices. */
(function () {
  const host = document.querySelector('.home-page [data-home-pricing]');
  if (!host) return;
  const API = 'https://api.lecturesift.com';
  const LOCALE_DATA = window.LECTURESIFT_LOCALE_DATA || {currencies: ['TRY', 'USD', 'EUR', 'GBP'], currencyForCountry: {}};
  const ZERO_DECIMAL_CURRENCIES = new Set(['JPY', 'KRW']);
  const locale = window.LectureSiftI18n?.locale || document.documentElement.lang || 'tr';

  // Same currency choice as plans.js: the saved preference, otherwise the
  // visitor's region, otherwise USD.
  const regionCode = () => {
    const saved = localStorage.getItem('lecturesift-country');
    if (saved) return saved.toUpperCase();
    return (navigator.language.split('-')[1] || '').toUpperCase();
  };
  const currency = (() => {
    const saved = localStorage.getItem('lecturesift-currency');
    if (LOCALE_DATA.currencies.includes(saved)) return saved;
    return LOCALE_DATA.currencyForCountry[regionCode()] || 'USD';
  })();

  const formatPrice = (amountMinor, code) => {
    const divisor = ZERO_DECIMAL_CURRENCIES.has(code) ? 1 : 100;
    const amount = amountMinor / divisor;
    return new Intl.NumberFormat(locale, {
      style: 'currency', currency: code, currencyDisplay: 'narrowSymbol',
      minimumFractionDigits: 0, maximumFractionDigits: divisor === 1 ? 0 : 2,
    }).formatToParts(amount).map(part =>
      part.type === 'currency' ? (LOCALE_DATA.currencySymbols?.[code] || part.value) : part.value
    ).join('');
  };
  const formatMinutes = minutes => new Intl.NumberFormat(locale).format(minutes);
  const setHidden = (node, hidden) => { if (node) node.hidden = hidden; };

  const applyPlan = (card, plan) => {
    let applied = false;
    const price = plan.display_price;
    const priceNode = card.querySelector('[data-plan-value="price"]');
    if (priceNode && price && Number.isFinite(price.amount_minor) && typeof price.currency === 'string') {
      priceNode.textContent = formatPrice(price.amount_minor, price.currency.toUpperCase());
      applied = true;
    }
    const entitlements = plan.entitlements || {};
    const minutes = entitlements.minutes ?? plan.minutes;
    const minutesNode = card.querySelector('[data-plan-value="minutes"]');
    if (minutesNode && Number.isFinite(minutes)) {
      minutesNode.textContent = formatMinutes(minutes);
      applied = true;
    }
    if (Array.isArray(entitlements.export_formats)) {
      const docx = entitlements.export_formats.includes('docx');
      setHidden(card.querySelector('[data-home-export="docx"]'), !docx);
      setHidden(card.querySelector('[data-home-export="pdf"]'), docx);
    }
    if (typeof entitlements.ad_free === 'boolean') {
      setHidden(card.querySelector('[data-home-ad-free]'), !entitlements.ad_free);
      setHidden(card.querySelector('[data-home-ads]'), entitlements.ad_free);
    }
    if (typeof entitlements.priority === 'string') {
      setHidden(card.querySelector('[data-home-priority]'), entitlements.priority !== 'priority');
    }
    return applied;
  };

  const apply = catalog => {
    const plans = new Map((catalog?.plans || []).filter(plan => plan && plan.code).map(plan => [plan.code, plan]));
    let applied = 0;
    host.querySelectorAll('[data-home-plan]').forEach(card => {
      const plan = plans.get(card.dataset.homePlan);
      if (plan && applyPlan(card, plan)) applied += 1;
    });
    // "Most popular" follows the catalog only when the featured subscription
    // is one of the cards shown here; otherwise the static highlight stays.
    const featured = [...plans.values()].find(plan => plan.featured && plan.kind === 'subscription')?.code;
    const cards = [...host.querySelectorAll('.price-card[data-home-plan]')];
    if (featured && cards.some(card => card.dataset.homePlan === featured)) {
      cards.forEach(card => {
        const popular = card.dataset.homePlan === featured;
        card.classList.toggle('featured', popular);
        setHidden(card.querySelector('[data-home-popular]'), !popular);
      });
    }
    host.dataset.pricingSource = applied ? 'live' : 'fallback';
    if (applied) host.dataset.pricingCurrency = String(catalog?.selected_currency || currency).toUpperCase();
  };

  fetch(`${API}/billing/plans?currency=${encodeURIComponent(currency)}`, {cache: 'no-store'})
    .then(response => response.ok ? response.json() : Promise.reject(new Error(`plans-${response.status}`)))
    .then(apply)
    .catch(() => { host.dataset.pricingSource = 'fallback'; });
})();
