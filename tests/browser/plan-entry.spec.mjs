import {test, expect} from './fixtures.mjs';

const API = 'https://api.lecturesift.com';
const headers = {'Access-Control-Allow-Origin':'http://127.0.0.1:4173'};

async function openPlans(page, registered = false) {
  await page.addInitScript(registered => {
    localStorage.setItem('lecturesift-currency', 'TRY');
    if (registered) localStorage.setItem('lecturesift-billing-token', 'synthetic-plan-owner');
  }, registered);
  await page.route(`${API}/billing/plans?currency=TRY`, route => route.request().method() === 'OPTIONS'
    ? route.fallback() : route.fulfill({headers, json:{
    selected_currency:'TRY', plans:[
      {code:'free', display_price:{currency:'TRY', amount_minor:0}},
      {code:'lite', display_price:{currency:'TRY', amount_minor:29900}},
      {code:'business'},
    ],
  }}));
  await page.goto('/en/plans.html');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#plansGrid')).toHaveAttribute('aria-busy', 'false');
}

test('free plan starts registration with a localized workspace destination', async ({page, isolatedNetwork}) => {
  await openPlans(page);
  const button = page.locator('.plan-action[data-plan="free"]');
  await expect(button).toBeEnabled();
  await button.click();
  await expect(page).toHaveURL(/\/en\/register(?:\.html)?\?next=/);
  expect(new URL(page.url()).searchParams.get('next')).toMatch(/^\/en\/workspace(?:\.html)?$/);
  expect(isolatedNetwork.apiCalls.filter(call => call.includes('/billing/checkout'))).toEqual([]);
});

test('current free plan returns a signed-in user to their workspace', async ({page}) => {
  await openPlans(page, true);
  const button = page.locator('.plan-action[data-plan="free"]');
  await expect(button).toBeEnabled();
  await button.click();
  await expect(page).toHaveURL(/\/en\/workspace(?:\.html)?$/);
});

test('business contact action opens the localized contact page without a checkout', async ({page, isolatedNetwork}) => {
  await openPlans(page);
  await page.locator('#morePlans > summary').click();
  await page.locator('.plan-action[data-plan="business"]').click();
  await expect(page).toHaveURL(/\/en\/contact(?:\.html)?$/);
  expect(isolatedNetwork.apiCalls.filter(call => call.includes('/billing/checkout'))).toEqual([]);
});

test('paid plan retains selected billing interval and language at login', async ({page}) => {
  await openPlans(page);
  await page.locator('#morePlans > summary').click();
  await page.locator('.plan-action[data-plan="lite"][data-interval="annual"]').click();
  await expect(page).toHaveURL(/\/en\/login(?:\.html)?\?next=/);
  const next = new URL(new URL(page.url()).searchParams.get('next'), page.url());
  expect(next.pathname).toMatch(/^\/en\/plans(?:\.html)?$/);
  expect(next.searchParams.get('plan')).toBe('lite');
  expect(next.searchParams.get('interval')).toBe('annual');
});

test('free, the credit pack and the popular package lead while the rest wait under Other packages', async ({page}) => {
  await openPlans(page);
  const lead = page.locator('#plansGrid > .plan-card');
  await expect(lead).toHaveCount(3);
  expect(await lead.evaluateAll(cards => cards.map(card => card.dataset.planCard))).toEqual(['free', 'credit', 'plus']);
  await expect(page.locator('#plansGrid [data-plan-card="plus"]')).toHaveClass(/featured/);
  await expect(page.locator('#morePlans > summary')).toContainText('Other packages');
  await expect(page.locator('#morePlansNames')).toHaveText('Lite · Pro · Max · Business');
  const others = ['lite', 'pro', 'max', 'business'];
  expect(await page.locator('#morePlansGrid > .plan-card').evaluateAll(cards => cards.map(card => card.dataset.planCard))).toEqual(others);
  for (const code of others) await expect(page.locator(`[data-plan-card="${code}"]`)).toBeHidden();
  const plus = page.locator('[data-plan-card="plus"]');
  await expect(plus.locator('.plan-price')).toContainText('30-day package');
  await expect(plus.locator('.plan-price')).not.toContainText('month');
  await expect(plus).toContainText('900 min / 30 days');
  await expect(page.locator('.plan-action[data-plan="plus"][data-interval="monthly"]')).toHaveText('Choose 30-day package');
  await page.locator('#morePlans > summary').click();
  for (const code of others) await expect(page.locator(`[data-plan-card="${code}"]`)).toBeVisible();
  await expect(page.locator('[data-plan-card="lite"] .plan-price')).toContainText('30-day package');
});

test('package cards keep list prices when the catalog and payment capabilities fail', async ({page}) => {
  await page.addInitScript(() => {
    localStorage.setItem('lecturesift-consent-v1', JSON.stringify({version:1, analytics:false, advertising:false, updated_at:new Date().toISOString()}));
    localStorage.setItem('lecturesift-currency', 'TRY');
  });
  const unavailable = route => route.request().method() === 'OPTIONS'
    ? route.fallback() : route.fulfill({status:503, headers, json:{detail:{message:'Synthetic catalog unavailable'}}});
  await page.route(`${API}/billing/plans?currency=TRY`, unavailable);
  await page.route(`${API}/billing/providers`, unavailable);
  await page.goto('/en/plans.html');
  await expect(page.locator('#plansGrid')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('#errorMessage')).toHaveText('Synthetic catalog unavailable');
  const credit = page.locator('#plansGrid [data-plan-card="credit"]');
  await expect(credit).toBeVisible();
  await expect(credit.locator('.plan-price')).toContainText(/₺\s*199\.00/);
  await expect(credit).toContainText('180 minutes');
  await expect(page.locator('[data-plan-card="plus"] .plan-price')).toContainText(/₺\s*599\.00/);
  await expect(page.locator('[data-plan-card="plus"] .plan-price')).toContainText('30-day package');
  await expect(page.locator('#morePlansGrid > .plan-card')).toHaveCount(4);
  await expect(page.locator('.plan-action[data-plan="credit"]')).toBeEnabled();
});

test('a deep link to a package under Other packages opens it and its checkout', async ({page}) => {
  await page.addInitScript(() => {
    localStorage.setItem('lecturesift-consent-v1', JSON.stringify({version:1, analytics:false, advertising:false, updated_at:new Date().toISOString()}));
    localStorage.setItem('lecturesift-currency', 'TRY');
    localStorage.setItem('lecturesift-billing-token', 'synthetic-plan-owner');
  });
  await page.route(`${API}/billing/providers`, route => route.request().method() === 'OPTIONS'
    ? route.fallback() : route.fulfill({headers, json:{commerce_identity:{configured:true}, providers:[{
      code:'iyzico', configured:true, currencies:['TRY'], capabilities:['cards'],
    }]}}));
  await page.goto('/en/plans.html?plan=lite&interval=monthly');
  await expect(page.locator('#checkoutPanel')).toBeVisible();
  await expect(page.locator('#checkoutPlanCode')).toHaveValue('lite');
  await expect(page.locator('#checkoutSummaryInterval')).toHaveText('30-day package');
  await expect(page.locator('#morePlans')).toHaveAttribute('open', '');
  await expect(page.locator('[data-plan-card="lite"]')).toBeVisible();
  await expect(page.locator('[data-plan-card="pro"]')).toBeVisible();
});

test.describe('without JavaScript', () => {
  test.use({javaScriptEnabled:false});

  test('static cards show the list prices, minutes and 30-day package names', async ({page}) => {
    await page.goto('/plans.html');
    await expect(page.locator('#plansGrid > .plan-card')).toHaveCount(3);
    const credit = page.locator('#plansGrid [data-plan-card="credit"]');
    await expect(credit).toBeVisible();
    await expect(credit.locator('[data-plan-value="price"]')).toHaveText('₺199');
    await expect(credit.locator('[data-plan-value="minutes"]')).toHaveText('180');
    const plus = page.locator('#plansGrid [data-plan-card="plus"]');
    await expect(plus.locator('[data-plan-value="price"]')).toHaveText('₺599');
    await expect(plus.locator('.plan-price')).toContainText('30 günlük paket');
    await expect(page.locator('[data-plan-card="lite"]')).toBeHidden();
    await page.locator('#morePlans > summary').click();
    await expect(page.locator('[data-plan-card="lite"] [data-plan-value="price"]')).toHaveText('₺299');
    await expect(page.locator('[data-plan-card="max"] [data-plan-value="minutes"]')).toHaveText('4.000');
    await page.goto('/en/plans.html');
    await expect(page.locator('#plansGrid [data-plan-card="plus"] .plan-price')).toContainText('30-day package');
    await expect(page.locator('#plansGrid [data-plan-card="plus"]')).toContainText('min / 30 days');
    await expect(page.locator('#morePlans > summary')).toContainText('Other packages');
    // The build rewrites Turkish number formatting for each language.
    await expect(page.locator('[data-plan-card="plus"] [data-plan-value="annual_price"]')).toHaveText('₺5,990');
    await expect(page.locator('[data-plan-card="pro"] [data-plan-value="price"]')).toHaveText('₺1,199');
    await expect(page.locator('[data-plan-card="pro"] [data-plan-value="minutes"]')).toHaveText('2,000');
    await expect(page.locator('[data-plan-card="max"] [data-plan-value="max_media_upload_mb"]')).toHaveText('1,024');
    const signIn = await page.locator('#plansGrid .plan-action[data-plan="credit"]').getAttribute('href');
    const loginUrl = new URL(signIn, page.url());
    expect(loginUrl.pathname).toBe('/en/login.html');
    expect(loginUrl.searchParams.get('next')).toBe('/en/plans?plan=credit&interval=one_time');
    await page.goto('/de/plans.html');
    await expect(page.locator('[data-plan-card="pro"] [data-plan-value="price"]')).toHaveText(/^1\.199\s₺$/);
  });
});
