import {test, expect} from './fixtures.mjs';

// The homepage markup carries no prices: price, minutes, the "most popular"
// badge and the entitlement bullets come from GET /billing/plans at runtime,
// so a catalog change can never leave stale numbers on the page. A card whose
// plan is missing from the catalog, or every card when the request fails,
// shows only the plan name and a link to the plans page.
const API_PLANS = url => url.origin === 'https://api.lecturesift.com'
  && url.pathname === '/billing/plans' && url.searchParams.get('currency') === 'TRY';
const CORS = {'Access-Control-Allow-Origin': 'http://127.0.0.1:4173'};
const plan = (code, kind, amount, entitlements, featured = false) => ({
  code, kind, featured,
  display_price: amount == null ? null : {currency: 'TRY', amount_minor: amount},
  entitlements: {history_days: 30, limits: {}, ...entitlements},
});
// Deliberately unlike any list price so a pass proves the live data was used.
const catalog = (featuredCode, {without = []} = {}) => ({
  selected_currency: 'TRY', guest_trial_minutes: 30,
  plans: [
    plan('free', 'free', 0, {minutes: 110, export_formats: ['pdf'], ad_free: false, priority: 'standard'}),
    plan('credit', 'one_time', 20900, {minutes: 320, export_formats: ['pdf', 'docx', 'txt'], ad_free: false, priority: 'standard'}),
    plan('lite', 'subscription', 17900, {minutes: 650, export_formats: ['pdf', 'docx', 'txt'], ad_free: true, priority: 'standard'}, featuredCode === 'lite'),
    plan('pro', 'subscription', 46900, {minutes: 1600, export_formats: ['pdf', 'docx', 'txt'], ad_free: true, priority: 'priority'}, featuredCode === 'pro'),
    plan('business', 'quote', null, {minutes: null, export_formats: ['pdf', 'docx', 'txt'], ad_free: true, priority: 'priority'}),
  ].filter(entry => !without.includes(entry.code)),
});
const serve = (page, status, body) => page.route(API_PLANS, route => route.fulfill({status, contentType: 'application/json', headers: CORS, body: JSON.stringify(body)}));
const values = async block => ({
  price: await block.locator('[data-home-price]').textContent(),
  minutes: await block.locator('[data-home-minutes]').textContent(),
});

test('homepage price cards show only names and a plans link when the catalog is empty or unavailable', async ({page, isolatedNetwork}) => {
  await page.addInitScript(() => localStorage.setItem('lecturesift-currency', 'TRY'));
  await page.goto('/en/');
  const pricing = page.locator('[data-home-pricing]');
  await expect(pricing).toHaveAttribute('data-pricing-source', 'unavailable');
  expect(isolatedNetwork.apiCalls).toContain('/billing/plans');
  const cards = pricing.locator('.price-card');
  await expect(cards).toHaveCount(3);
  await expect(cards.locator('h3')).toHaveText(['Free', 'Student', 'Pro']);
  for (const [index, code] of ['free', 'lite', 'pro'].entries()) {
    const card = cards.nth(index);
    await expect(card).toHaveAttribute('data-home-plan', code);
    await expect(card.locator('[data-home-live]')).toBeHidden();
    await expect(card.locator('.price-badge')).toBeHidden();
    await expect(card.locator('.price-link a')).toBeVisible();
    await expect(card.locator('.price-link a')).toHaveText('See current prices →');
    await expect(card.locator('.price-link a')).toHaveAttribute('href', /^\/en\/plans(?:\.html)?$/);
  }
  await expect(pricing.locator('.pricing-pack')).toBeHidden();
  // Nothing static can go stale: no price or minute figure is in the markup.
  await expect(pricing.locator('[data-home-price], [data-home-minutes]')).toHaveText(['', '', '', '', '', '', '', '']);
  await expect(pricing).not.toContainText(/₺|\b(Lite|Plus|Max)\b/);
  await expect(pricing.locator('button')).toHaveCount(0);
  await expect(pricing.locator('a[href="/en/plans"]')).toHaveCount(4);

  // A failing catalog request leaves the same name-only cards.
  await serve(page, 503, {detail: 'unavailable'});
  await page.reload();
  await expect(pricing).toHaveAttribute('data-pricing-source', 'unavailable');
  await expect(cards.nth(1).locator('[data-home-live]')).toBeHidden();
  await expect(cards.nth(1).locator('.price-link a')).toBeVisible();
});

test('homepage price cards render the live catalog, its featured plan and entitlement bullets', async ({page}) => {
  await page.addInitScript(() => localStorage.setItem('lecturesift-currency', 'TRY'));
  await serve(page, 200, catalog('lite'));
  await page.goto('/en/');
  const pricing = page.locator('[data-home-pricing]');
  await expect(pricing).toHaveAttribute('data-pricing-source', 'live');
  await expect(pricing).toHaveAttribute('data-pricing-currency', 'TRY');
  const [free, lite, pro] = [0, 1, 2].map(index => pricing.locator('.price-card').nth(index));
  for (const card of [free, lite, pro]) {
    await expect(card.locator('[data-home-live]')).toBeVisible();
    await expect(card.locator('.price-link')).toBeHidden();
  }
  expect(await values(free)).toEqual({price: '₺0', minutes: '110'});
  expect(await values(lite)).toEqual({price: '₺179', minutes: '650'});
  expect(await values(pro)).toEqual({price: '₺469', minutes: '1,600'});
  await expect(pricing.locator('.pricing-pack')).toBeVisible();
  expect(await values(pricing.locator('.pricing-pack'))).toEqual({price: '₺209', minutes: '320'});
  // Bullets follow the entitlements: export formats, ads and priority.
  await expect(free.locator('[data-home-export="pdf"]')).toBeVisible();
  await expect(free.locator('[data-home-export="docx"]')).toBeHidden();
  // The fixture's ad provider is off, so neither ad bullet is shown.
  await expect(free.locator('[data-home-ads]')).toBeHidden();
  await expect(free.locator('[data-home-ad-free]')).toBeHidden();
  await expect(lite.locator('[data-home-export="docx"]')).toBeVisible();
  await expect(lite.locator('[data-home-export="pdf"]')).toBeHidden();
  await expect(lite.locator('[data-home-ad-free]')).toBeHidden();
  await expect(lite.locator('[data-home-ads]')).toBeHidden();
  await expect(pro.locator('[data-home-priority]')).toBeVisible();
  await expect(lite).toHaveClass(/featured/);
  await expect(lite.locator('.price-badge')).toHaveText('Most popular');
  await expect(pro.locator('.price-badge')).toBeHidden();
  await expect(pricing.locator('.price-card h3')).toHaveText(['Free', 'Student', 'Pro']);

  // The "most popular" badge follows the catalog's featured subscription.
  await page.unroute(API_PLANS);
  await serve(page, 200, catalog('pro'));
  await page.reload();
  await expect(pricing).toHaveAttribute('data-pricing-source', 'live');
  await expect(pro).toHaveClass(/featured/);
  await expect(pro.locator('.price-badge')).toBeVisible();
  await expect(lite).not.toHaveClass(/featured/);
  await expect(lite.locator('.price-badge')).toBeHidden();

  // With the ad provider on, the ad bullets follow each plan's entitlement.
  await page.route('https://api.lecturesift.com/ads/config', route => route.fulfill({status: 200, contentType: 'application/json', headers: CORS, body: JSON.stringify({enabled: true, provider: null})}));
  await page.reload();
  await expect(pricing).toHaveAttribute('data-pricing-source', 'live');
  await expect(free.locator('[data-home-ads]')).toBeVisible();
  await expect(free.locator('[data-home-ad-free]')).toBeHidden();
  await expect(lite.locator('[data-home-ad-free]')).toBeVisible();
  await expect(lite.locator('[data-home-ads]')).toBeHidden();
  await page.unroute('https://api.lecturesift.com/ads/config');

  // A plan missing from the catalog keeps its name and the plans link while
  // the other cards stay live.
  await page.unroute(API_PLANS);
  await serve(page, 200, catalog('lite', {without: ['pro', 'credit']}));
  await page.reload();
  await expect(pricing).toHaveAttribute('data-pricing-source', 'live');
  await expect(lite.locator('[data-home-live]')).toBeVisible();
  await expect(lite.locator('.price-badge')).toBeVisible();
  await expect(pro.locator('[data-home-live]')).toBeHidden();
  await expect(pro.locator('.price-link a')).toBeVisible();
  await expect(pricing.locator('.pricing-pack')).toBeHidden();
});
