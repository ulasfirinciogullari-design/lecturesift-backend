/* Homepage price cards. The markup carries no prices: price, minutes, the
   "most popular" badge and the entitlement bullets come from GET /billing/plans
   at runtime, so the homepage can never show a stale list price. A card whose
   plan is missing from the catalog, or every card when the request fails,
   shows only the plan name and a link to the plans page. */
(function () {
  const host = document.querySelector('.home-page [data-home-pricing]');
  if (!host) return;
  host.dataset.pricingSource = 'loading';
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

  // Each block is a price card or the one-line minute pack. `live` holds the
  // catalog-filled content, `offline` the name-only fallback link.
  const blocks = [...host.querySelectorAll('[data-home-plan]')].map(node => ({
    node,
    code: node.dataset.homePlan,
    live: node.hasAttribute('data-home-live') ? node : node.querySelector('[data-home-live]'),
    offline: node.querySelector('[data-home-offline]'),
    applied: false,
  }));

  const fill = (block, plan, adsEnabled) => {
    if (!plan || !block.live) return false;
    const price = plan.display_price;
    const entitlements = plan.entitlements || {};
    const minutes = entitlements.minutes ?? plan.minutes;
    const priced = price && Number.isFinite(price.amount_minor) && typeof price.currency === 'string';
    if (!priced || !Number.isFinite(minutes)) return false;
    block.live.querySelector('[data-home-price]').textContent = formatPrice(price.amount_minor, price.currency.toUpperCase());
    block.live.querySelector('[data-home-minutes]').textContent = formatMinutes(minutes);
    if (Array.isArray(entitlements.export_formats)) {
      const docx = entitlements.export_formats.includes('docx');
      setHidden(block.live.querySelector('[data-home-export="docx"]'), !docx);
      setHidden(block.live.querySelector('[data-home-export="pdf"]'), docx);
    }
    // Ad bullets exist only while LectureSift shows ads at all (GET
    // /ads/config); with the provider off they would describe nothing.
    if (typeof entitlements.ad_free === 'boolean') {
      setHidden(block.live.querySelector('[data-home-ad-free]'), !adsEnabled || !entitlements.ad_free);
      setHidden(block.live.querySelector('[data-home-ads]'), !adsEnabled || entitlements.ad_free);
    }
    if (typeof entitlements.priority === 'string') {
      setHidden(block.live.querySelector('[data-home-priority]'), entitlements.priority !== 'priority');
    }
    return true;
  };

  const show = (block, live) => {
    block.applied = live;
    setHidden(block.live, !live);
    setHidden(block.offline, live);
  };

  const apply = ([catalog, adsEnabled]) => {
    const plans = new Map((catalog?.plans || []).filter(plan => plan && plan.code).map(plan => [plan.code, plan]));
    blocks.forEach(block => show(block, fill(block, plans.get(block.code), adsEnabled)));
    // "Most popular" follows the catalog's featured subscription when that plan
    // is one of the cards shown here and has live data.
    const featured = [...plans.values()].find(plan => plan.featured && plan.kind === 'subscription')?.code;
    const cards = blocks.filter(block => block.node.classList.contains('price-card'));
    if (cards.some(card => card.code === featured && card.applied)) {
      cards.forEach(card => {
        const popular = card.code === featured;
        card.node.classList.toggle('featured', popular);
        setHidden(card.node.querySelector('[data-home-popular]'), !popular);
      });
    }
    const applied = blocks.some(block => block.applied);
    host.dataset.pricingSource = applied ? 'live' : 'unavailable';
    if (applied) host.dataset.pricingCurrency = String(catalog?.selected_currency || currency).toUpperCase();
  };

  const unavailable = () => {
    blocks.forEach(block => show(block, false));
    host.dataset.pricingSource = 'unavailable';
  };

  const plans = fetch(`${API}/billing/plans?currency=${encodeURIComponent(currency)}`, {cache: 'no-store'})
    .then(response => response.ok ? response.json() : Promise.reject(new Error(`plans-${response.status}`)));
  // An unreachable ads config keeps the bullets, as before this check existed.
  const adsEnabled = fetch(`${API}/ads/config`, {cache: 'no-store'})
    .then(response => response.ok ? response.json() : null)
    .then(config => config?.enabled !== false)
    .catch(() => true);
  Promise.all([plans, adsEnabled]).then(apply).catch(unavailable);
})();
