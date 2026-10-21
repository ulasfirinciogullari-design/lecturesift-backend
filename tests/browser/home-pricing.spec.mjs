import {test, expect} from './fixtures.mjs';

// The homepage price cards carry the TRY list prices as static fallbacks and
// take price, minutes, the "most popular" badge and the entitlement bullets
// from GET /billing/plans, so a catalog change never leaves stale numbers.
const API_PLANS = url => url.origin === 'https://api.lecturesift.com'
  && url.pathname === '/billing/plans' && url.searchParams.get('currency') === 'TRY';
const CORS = {'Access-Control-Allow-Origin': 'http://127.0.0.1:4173'};
const plan = (code, kind, amount, entitlements, featured = false) => ({
  code, kind, featured,
  display_price: amount == null ? null : {currency: 'TRY', amount_minor: amount},
  entitlements: {history_days: 30, limits: {}, ...entitlements},
});
// Deliberately different from the static markup so a pass proves the live data won.
const catalog = featuredCode => ({
  selected_currency: 'TRY', guest_trial_minutes: 30,
  plans: [
    plan('free', 'free', 0, {minutes: 110, export_formats: ['pdf'], ad_free: false, priority: 'standard'}),
    plan('credit', 'one_time', 20900, {minutes: 320, export_formats: ['pdf', 'docx', 'txt'], ad_free: false, priority: 'standard'}),
    plan('lite', 'subscription', 17900, {minutes: 650, export_formats: ['pdf', 'docx', 'txt'], ad_free: true, priority: 'standard'}, featuredCode === 'lite'),
    plan('pro', 'subscription', 46900, {minutes: 1600, export_formats: ['pdf', 'docx', 'txt'], ad_free: true, priority: 'priority'}, featuredCode === 'pro'),
    plan('business', 'quote', null, {minutes: null, export_formats: ['pdf', 'docx', 'txt'], ad_free: true, priority: 'priority'}),
  ],
});
const values = async card => ({
  price: await card.locator('[data-plan-value="price"]').textContent(),
  minutes: await card.locator('[data-plan-value="minutes"]').textContent(),
});

test('homepage price cards keep the static list prices when the catalog is empty or unavailable', async ({page, isolatedNetwork}) => {
  await page.addInitScript(() => localStorage.setItem('lecturesift-currency', 'TRY'));
  await page.goto('/en/');
  const pricing = page.locator('[data-home-pricing]');
  await expect(pricing).toHaveAttribute('data-pricing-source', 'fallback');
  expect(isolatedNetwork.apiCalls).toContain('/billing/plans');
  const cards = pricing.locator('.price-card');
  await expect(cards).toHaveCount(3);
  for (const [index, code] of ['free', 'lite', 'pro'].entries()) await expect(cards.nth(index)).toHaveAttribute('data-home-plan', code);
  expect(await values(cards.nth(0))).toEqual({price: '₺0', minutes: '120'});
  expect(await values(cards.nth(1))).toEqual({price: '₺149', minutes: '600'});
  expect(await values(cards.nth(2))).toEqual({price: '₺449', minutes: '1,500'});
  expect(await values(pricing.locator('.pricing-pack'))).toEqual({price: '₺199', minutes: '300'});
  await expect(cards.nth(1)).toHaveClass(/featured/);
  await expect(cards.nth(1).locator('.price-badge')).toHaveText('Most popular');
  await expect(cards.nth(2).locator('.price-badge')).toBeHidden();
  // Lite/Plus/Max names stay off the homepage; the student plan keeps the lite code.
  await expect(cards.nth(1).locator('h3')).toHaveText('Student');
  await expect(pricing).not.toContainText(/\b(Lite|Plus|Max)\b/);
  // One link to the full comparison and no per-card checkout buttons.
  await expect(pricing.locator('a, button')).toHaveCount(1);
  await expect(pricing.locator('a')).toHaveAttribute('href', /^\/en\/plans(?:\.html)?$/);

  // A failing catalog request leaves the same static numbers in place.
  await page.route(API_PLANS, route => route.fulfill({status: 503, contentType: 'application/json', headers: CORS, body: '{"detail":"unavailable"}'}));
  await page.reload();
  await expect(pricing).toHaveAttribute('data-pricing-source', 'fallback');
  expect(await values(cards.nth(1))).toEqual({price: '₺149', minutes: '600'});
});

test('homepage price cards render the live catalog, its featured plan and entitlement bullets', async ({page}) => {
  await page.addInitScript(() => localStorage.setItem('lecturesift-currency', 'TRY'));
  await page.route(API_PLANS, route => route.fulfill({status: 200, contentType: 'application/json', headers: CORS, body: JSON.stringify(catalog('lite'))}));
  await page.goto('/en/');
  const pricing = page.locator('[data-home-pricing]');
  await expect(pricing).toHaveAttribute('data-pricing-source', 'live');
  await expect(pricing).toHaveAttribute('data-pricing-currency', 'TRY');
  const [free, lite, pro] = [0, 1, 2].map(index => pricing.locator('.price-card').nth(index));
  expect(await values(free)).toEqual({price: '₺0', minutes: '110'});
  expect(await values(lite)).toEqual({price: '₺179', minutes: '650'});
  expect(await values(pro)).toEqual({price: '₺469', minutes: '1,600'});
  expect(await values(pricing.locator('.pricing-pack'))).toEqual({price: '₺209', minutes: '320'});
  // Bullets follow the entitlements: export formats, ads and priority.
  await expect(free.locator('[data-home-export="pdf"]')).toBeVisible();
  await expect(free.locator('[data-home-export="docx"]')).toBeHidden();
  await expect(free.locator('[data-home-ads]')).toBeVisible();
  await expect(free.locator('[data-home-ad-free]')).toBeHidden();
  await expect(lite.locator('[data-home-export="docx"]')).toBeVisible();
  await expect(lite.locator('[data-home-export="pdf"]')).toBeHidden();
  await expect(lite.locator('[data-home-ad-free]')).toBeVisible();
  await expect(lite.locator('[data-home-ads]')).toBeHidden();
  await expect(pro.locator('[data-home-priority]')).toBeVisible();
  await expect(lite).toHaveClass(/featured/);
  await expect(lite.locator('.price-badge')).toBeVisible();
  await expect(pro.locator('.price-badge')).toBeHidden();
  // Static HTML stays readable: the three cards and the pack are still real text.
  await expect(pricing.locator('.price-card h3')).toHaveText(['Free', 'Student', 'Pro']);

  // The "most popular" badge follows the catalog's featured subscription.
  await page.unroute(API_PLANS);
  await page.route(API_PLANS, route => route.fulfill({status: 200, contentType: 'application/json', headers: CORS, body: JSON.stringify(catalog('pro'))}));
  await page.reload();
  await expect(pricing).toHaveAttribute('data-pricing-source', 'live');
  await expect(pro).toHaveClass(/featured/);
  await expect(pro.locator('.price-badge')).toBeVisible();
  await expect(lite).not.toHaveClass(/featured/);
  await expect(lite.locator('.price-badge')).toBeHidden();
});
