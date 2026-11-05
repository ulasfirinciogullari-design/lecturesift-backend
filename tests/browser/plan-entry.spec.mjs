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
      {code:'lite', display_price:{currency:'TRY', amount_minor:14900}},
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

test('the school and team link opens the localized contact page without a checkout', async ({page, isolatedNetwork}) => {
  await openPlans(page);
  await expect(page.locator('[data-plan-card="business"]')).toHaveCount(0);
  await expect(page.locator('#plansTeamLink a')).toHaveText('A school or a team? Write to us');
  await page.locator('#plansTeamLink a').click();
  await expect(page).toHaveURL(/\/en\/contact(?:\.html)?$/);
  expect(isolatedNetwork.apiCalls.filter(call => call.includes('/billing/checkout'))).toEqual([]);
});

test('paid plan sends a guest to register with the chosen plan, interval and language', async ({page}) => {
  await openPlans(page);
  await page.locator('.plan-action[data-plan="lite"][data-interval="annual"]').click();
  await expect(page).toHaveURL(/\/en\/register(?:\.html)?\?next=/);
  const nextValue = new URL(page.url()).searchParams.get('next');
  const next = new URL(nextValue, page.url());
  expect(next.pathname).toMatch(/^\/en\/plans(?:\.html)?$/);
  expect(next.searchParams.get('plan')).toBe('lite');
  expect(next.searchParams.get('interval')).toBe('annual');
  // The register card names the plan and the price the plans page showed.
  await expect(page.locator('#chosenPlan')).toBeVisible();
  await expect(page.locator('#chosenPlanName')).toHaveText(/^Student · ₺\s*1,490 \/ year$/);
  // Someone who already has an account can still sign in and keep the destination.
  const signIn = new URL(await page.locator('.inline-links a[data-auth-route="/login.html"]').getAttribute('href'), page.url());
  expect(signIn.pathname).toBe('/en/login.html');
  expect(signIn.searchParams.get('next')).toBe(nextValue);
});

test('a register link from another tab names the plan without a price', async ({page}) => {
  await page.goto(`/en/register.html?next=${encodeURIComponent('/en/plans?plan=lite&interval=monthly')}`);
  await expect(page.locator('#chosenPlanName')).toHaveText('Student · 30-day package');
  await page.goto(`/en/register.html?next=${encodeURIComponent('/en/about.html')}`);
  await expect(page.locator('#chosenPlan')).toBeHidden();
});

test('free, Student and Pro lead the grid while the minute pack sits in its own column', async ({page}) => {
  await openPlans(page);
  const lead = page.locator('#plansGrid > .plan-card');
  await expect(lead).toHaveCount(3);
  expect(await lead.evaluateAll(cards => cards.map(card => card.dataset.planCard))).toEqual(['free', 'lite', 'pro']);
  const student = page.locator('#plansGrid [data-plan-card="lite"]');
  await expect(student).toHaveClass(/featured/);
  await expect(student.locator('.plan-badge')).toHaveText('Most popular');
  await expect(student.locator('h3')).toHaveText('Student');
  await expect(student.locator('.plan-price')).toContainText('30-day package');
  await expect(student.locator('.plan-price')).not.toContainText('month');
  await expect(student).toContainText('600 min / 30 days');
  // The fixture's ad provider is off, so no plan claims an ad experience.
  await expect(student).not.toContainText('Ad-free use');
  await expect(page.locator('#compareBody')).not.toContainText('Ad experience');
  await expect(page.locator('.plan-action[data-plan="lite"][data-interval="monthly"]')).toHaveText('Choose 30-day package');
  await expect(page.locator('.plan-action[data-plan="lite"][data-interval="annual"]')).toHaveText(/^Annual · ₺\s*1,490 · 2 months free$/);
  const side = page.locator('#sidePlansGrid > .plan-card');
  await expect(side).toHaveCount(1);
  await expect(side).toHaveAttribute('data-plan-card', 'credit');
  await expect(side.locator('.plan-eyebrow')).toHaveText('Minutes that never expire');
  await expect(side).toContainText('200 minutes');
  // Plus and Max are hidden, not removed: no card, no comparison column.
  for (const code of ['plus', 'max', 'business']) await expect(page.locator(`[data-plan-card="${code}"]`)).toHaveCount(0);
  await expect(page.locator('#compareHead th')).toHaveText(['Entitlement', 'Free', 'Minute Pack', 'Student', 'Pro']);
  await expect(page.locator('#plansTeamLink')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
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
  const credit = page.locator('#sidePlansGrid [data-plan-card="credit"]');
  await expect(credit).toBeVisible();
  // Cards show whole prices, as the homepage does; checkout keeps the decimals.
  await expect(credit.locator('.plan-price')).toContainText(/^₺\s*199(?![.,\d])/);
  await expect(credit).toContainText('200 minutes');
  await expect(page.locator('[data-plan-card="lite"] .plan-price')).toContainText(/^₺\s*149(?![.,\d])/);
  await expect(page.locator('[data-plan-card="lite"] .plan-price')).toContainText('30-day package');
  await expect(page.locator('[data-plan-card="pro"] .plan-price')).toContainText(/^₺\s*449(?![.,\d])/);
  await expect(page.locator('#plansGrid > .plan-card')).toHaveCount(3);
  await expect(page.locator('.plan-action[data-plan="credit"]')).toBeEnabled();
});

for (const [code, label] of [['lite', 'a listed package'], ['plus', 'a hidden Plus renewal link']]) {
  test(`a deep link to ${label} opens its checkout`, async ({page}) => {
    await page.addInitScript(() => {
      localStorage.setItem('lecturesift-consent-v1', JSON.stringify({version:1, analytics:false, advertising:false, updated_at:new Date().toISOString()}));
      localStorage.setItem('lecturesift-currency', 'TRY');
      localStorage.setItem('lecturesift-billing-token', 'synthetic-plan-owner');
    });
    await page.route(`${API}/billing/providers`, route => route.request().method() === 'OPTIONS'
      ? route.fallback() : route.fulfill({headers, json:{commerce_identity:{configured:true}, providers:[{
        code:'iyzico', configured:true, currencies:['TRY'], capabilities:['cards'],
      }]}}));
    await page.goto(`/en/plans.html?plan=${code}&interval=monthly`);
    await expect(page.locator('#checkoutPanel')).toBeVisible();
    await expect(page.locator('#checkoutPlanCode')).toHaveValue(code);
    await expect(page.locator('#checkoutSummaryInterval')).toHaveText('30-day package');
    await expect(page.locator('#checkoutSummaryTotal')).toContainText(code === 'plus' ? /₺\s*599\.00/ : /₺\s*149\.00/);
    // The hidden plan stays orderable, but it never gets a card.
    await expect(page.locator('[data-plan-card="plus"]')).toHaveCount(0);
    await expect(page.locator('[data-plan-card="lite"]')).toBeVisible();
  });
}

test.describe('without JavaScript', () => {
  test.use({javaScriptEnabled:false});

  test('static cards show the list prices, minutes and 30-day package names', async ({page}) => {
    await page.goto('/plans.html');
    await expect(page.locator('#plansGrid > .plan-card')).toHaveCount(3);
    await expect(page.locator('#sidePlansGrid > .plan-card')).toHaveCount(1);
    const credit = page.locator('#sidePlansGrid [data-plan-card="credit"]');
    await expect(credit).toBeVisible();
    await expect(credit.locator('[data-plan-value="price"]')).toHaveText('₺99');
    await expect(credit.locator('[data-plan-value="minutes"]')).toHaveText('200');
    const student = page.locator('#plansGrid [data-plan-card="lite"]');
    await expect(student).toHaveClass(/featured/);
    await expect(student.locator('h3')).toHaveText('Öğrenci');
    await expect(student.locator('[data-plan-value="price"]')).toHaveText('₺149');
    await expect(student.locator('.plan-price')).toContainText('30 günlük paket');
    await expect(student.locator('[data-plan-value="annual_price"]')).toHaveText('₺1.490');
    await expect(page.locator('[data-plan-card="pro"] [data-plan-value="minutes"]')).toHaveText('1.500');
    for (const code of ['plus', 'max', 'business']) await expect(page.locator(`[data-plan-card="${code}"]`)).toHaveCount(0);
    await expect(page.locator('#plansTeamLink a')).toHaveAttribute('href', /contact/);
    await page.goto('/en/plans.html');
    await expect(page.locator('#plansGrid [data-plan-card="lite"] h3')).toHaveText('Student');
    await expect(page.locator('#plansGrid [data-plan-card="lite"] .plan-price')).toContainText('30-day package');
    await expect(page.locator('#plansGrid [data-plan-card="lite"]')).toContainText('min / 30 days');
    await expect(page.locator('#plansGrid [data-plan-card="lite"] .plan-badge')).toHaveText('Most popular');
    // The build rewrites Turkish number formatting for each language.
    await expect(page.locator('[data-plan-card="lite"] [data-plan-value="annual_price"]')).toHaveText('₺1,490');
    await expect(page.locator('.plan-action[data-plan="lite"][data-interval="annual"]')).toHaveText('Annual · ₺1,490 · 2 months free');
    await expect(page.locator('[data-plan-card="pro"] [data-plan-value="price"]')).toHaveText('₺449');
    await expect(page.locator('[data-plan-card="pro"] [data-plan-value="minutes"]')).toHaveText('1,500');
    await expect(page.locator('[data-plan-card="pro"] [data-plan-value="max_media_upload_mb"]')).toHaveText('1,024');
    const register = await page.locator('#sidePlansGrid .plan-action[data-plan="credit"]').getAttribute('href');
    const registerUrl = new URL(register, page.url());
    expect(registerUrl.pathname).toBe('/en/register.html');
    expect(registerUrl.searchParams.get('next')).toBe('/en/plans?plan=credit&interval=one_time');
    await page.goto('/de/plans.html');
    await expect(page.locator('[data-plan-card="pro"] [data-plan-value="price"]')).toHaveText(/^449\s₺$/);
  });
});
