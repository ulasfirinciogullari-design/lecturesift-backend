import {test, expect} from './fixtures.mjs';

const API = 'https://api.lecturesift.com';
const CORS = {
  'Access-Control-Allow-Origin':'http://127.0.0.1:4173',
  'Access-Control-Allow-Methods':'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers':'authorization, content-type',
};
const prices = {TRY:29900, USD:1111, EUR:2222};
const catalog = currency => ({selected_currency:currency, plans:[
  {code:'lite', display_price:{currency, amount_minor:prices[currency]}},
]});
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};
const fulfill = (route, json, status = 200) => route.fulfill({status, headers:CORS, json});

async function openPlans(page, override) {
  await page.addInitScript(() => {
    localStorage.setItem('lecturesift-billing-token', 'synthetic-checkout-owner');
    localStorage.setItem('lecturesift-currency', 'TRY');
  });
  await page.route(`${API}/**`, async route => {
    const url = new URL(route.request().url());
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({status:204, headers:CORS});
      return;
    }
    if (override && await override(route, url)) return;
    if (url.pathname === '/billing/plans') {
      await fulfill(route, catalog(url.searchParams.get('currency')));
    } else if (url.pathname === '/billing/providers') {
      await fulfill(route, {commerce_identity:{configured:true}, providers:[{
        code:'iyzico', configured:true, currencies:['TRY','USD','EUR'], capabilities:['cards','bank_transfer'],
      }]});
    } else if (url.pathname === '/billing/manual-transfer') {
      await fulfill(route, {available:true});
    } else await route.fallback();
  });
  await page.goto('/en/plans.html');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#plansGrid')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('[data-plan="lite"][data-interval="monthly"]')).toBeEnabled();
}

async function choosePlan(page, code = 'lite') {
  await page.locator(`[data-plan="${code}"][data-interval="monthly"]`).click();
  await expect(page.locator('#checkoutPanel')).toBeVisible();
  await page.locator('#checkoutFirstName').fill('Synthetic');
  await page.locator('#checkoutLastName').fill('Student');
  await page.locator('#checkoutAddress').fill('Synthetic billing address');
  await page.locator('#checkoutCity').fill('Ankara');
  await page.locator('#checkoutZipCode').fill('06000');
  await page.locator('#checkoutPhone').fill('+905550000001');
  await page.locator('#checkoutTerms').check();
  await page.locator('#checkoutEarlyPerformance').check();
}

async function finishResponse(page, response) {
  await (await response).finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

test('a late currency response cannot replace the latest price or checkout currency', async ({page}) => {
  const pending = deferred(), received = deferred(), payloads = [];
  await openPlans(page, async (route, url) => {
    if (url.pathname === '/billing/plans' && url.searchParams.get('currency') === 'USD') {
      received.resolve();
      await pending.promise;
      await fulfill(route, catalog('USD'));
      return true;
    }
    if (url.pathname === '/billing/checkout') {
      payloads.push(route.request().postDataJSON());
      await fulfill(route, {detail:{message:'Synthetic provider unavailable'}}, 503);
      return true;
    }
    return false;
  });
  try {
    await page.locator('#billingCurrency').selectOption('USD');
    await received.promise;
    await expect(page.locator('[data-plan="lite"][data-interval="monthly"]')).toBeDisabled();
    await page.locator('#billingCurrency').selectOption('EUR');
    await expect(page.locator('#plansGrid')).toHaveAttribute('aria-busy', 'false');
    await expect(page.locator('.plan-card').filter({has:page.locator('[data-plan="lite"]')}).toContainText('€22.22');
    const response = page.waitForResponse(`${API}/billing/plans?currency=USD`);
    pending.resolve();
    await finishResponse(page, response);
    await expect(page.locator('#billingCurrency')).toHaveValue('EUR');
    await choosePlan(page);
    await expect(page.locator('#checkoutSummaryTotal')).toHaveText('€22.22');
    await expect(page.locator('#billingCurrency')).toBeDisabled();
    await page.locator('#checkoutCardButton').click();
    await expect(page.locator('#checkoutNotice')).toHaveText('Synthetic provider unavailable');
    expect(payloads).toHaveLength(1);
    expect(payloads[0]).toMatchObject({plan_code:'lite', interval:'monthly', currency:'EUR'});
    await expect(page.locator('#checkoutCardButton')).toBeEnabled();
  } finally { pending.resolve(); }
});

test('a failed currency refresh restores the displayed currency and can be retried', async ({page}) => {
  let attempts = 0;
  await openPlans(page, async (route, url) => {
    if (url.pathname !== '/billing/plans' || url.searchParams.get('currency') !== 'USD') return false;
    attempts += 1;
    await fulfill(route, attempts === 1 ? {detail:{message:'Synthetic catalog unavailable'}} : catalog('USD'), attempts === 1 ? 503 : 200);
    return true;
  });
  await page.locator('#billingCurrency').selectOption('USD');
  await expect(page.locator('#errorMessage')).toHaveText('Synthetic catalog unavailable');
  await expect(page.locator('#billingCurrency')).toHaveValue('TRY');
  expect(await page.evaluate(() => localStorage.getItem('lecturesift-currency'))).toBe('TRY');
  await choosePlan(page);
  await expect(page.locator('#checkoutSummaryTotal')).toHaveText('₺299.00');
  await page.locator('#checkoutClose').click();
  await expect(page.locator('#billingCurrency')).toBeEnabled();
  await page.locator('#billingCurrency').selectOption('USD');
  await expect(page.locator('#plansGrid')).toHaveAttribute('aria-busy', 'false');
  await choosePlan(page);
  await expect(page.locator('#checkoutSummaryTotal')).toHaveText('$11.11');
  expect(attempts).toBe(2);
});

test('a manual transfer rejection stays visible in checkout and permits an explicit retry', async ({page}) => {
  const payloads = [];
  const refreshPending = deferred(), refreshReceived = deferred();
  let orderCreated = false;
  await openPlans(page, async (route, url) => {
    if (url.pathname === '/billing/me' && orderCreated) {
      refreshReceived.resolve();
      await refreshPending.promise;
      await fulfill(route, {account:{user:{email:'synthetic@example.invalid'}, plan:{code:'free'}, remaining_minutes:60}});
      return true;
    }
    if (url.pathname !== '/billing/manual-transfer/orders') return false;
    payloads.push(route.request().postDataJSON());
    const body = payloads.length === 1
      ? {detail:{message:'Synthetic transfer unavailable'}}
      : {order:{reference:'SYNTHETIC-RETRY', order_number:'SYNTHETIC-RETRY', plan_code:'lite', amount_minor:29900, currency:'TRY', bank:{iban:'TR000000000000000000000000', account_holder:'Synthetic fixture'}}};
    orderCreated = payloads.length > 1;
    await fulfill(route, body, orderCreated ? 200 : 503);
    return true;
  });
  try {
    await choosePlan(page);
    await page.locator('#checkoutBankButton').click();
    await expect(page.locator('#checkoutPanel')).toBeVisible();
    await expect(page.locator('#checkoutNotice')).toHaveText('Synthetic transfer unavailable');
    await expect(page.locator('#checkoutBankButton')).toBeEnabled();
    await expect(page.locator('#transferPanel')).toBeHidden();
    expect(payloads).toHaveLength(1);
    await page.locator('#checkoutBankButton').click();
    await expect(page.locator('#transferPanel')).toBeVisible();
    await expect(page.locator('#transferReference')).toHaveText('SYNTHETIC-RETRY');
    await expect(page.locator('#transferAmount')).toHaveText('₺299.00');
    await expect(page.locator('#checkoutPanel')).toBeHidden();
    await expect(page.locator('#billingCurrency')).toBeEnabled();
    await refreshReceived.promise;
    await choosePlan(page, 'plus');
    await expect(page.locator('#checkoutCardButton')).toBeEnabled();
    const response = page.waitForResponse(`${API}/billing/me`);
    refreshPending.resolve();
    await finishResponse(page, response);
    await expect(page.locator('#checkoutPlanCode')).toHaveValue('plus');
    await expect(page.locator('#checkoutCardButton')).toBeEnabled();
    expect(payloads).toHaveLength(2);
    expect(payloads[1]).toMatchObject({plan_code:'lite', interval:'monthly'});
  } finally { refreshPending.resolve(); }
});

for (const method of ['hosted', 'manual']) {
  for (const outcome of ['success', 'failure']) {
    test(`a late ${method} ${outcome} preserves a reopened checkout and blocks duplicate submissions`, async ({page}) => {
      const pending = deferred(), received = deferred(), payloads = [];
      const path = method === 'hosted' ? '/billing/checkout' : '/billing/manual-transfer/orders';
      const nextPlan = outcome === 'success' ? 'plus' : 'lite';
      await openPlans(page, async (route, url) => {
        if (![path, '/billing/checkout'].includes(url.pathname)) return false;
        payloads.push(route.request().postDataJSON());
        if (payloads.length === 1) {
          received.resolve();
          await pending.promise;
          const body = method === 'hosted'
            ? {provider:'iyzico', display_mode:'redirect', checkout_url:'/en/plans.html?unexpectedRedirect=1', order:{order_number:'SYNTHETIC-OLD'}}
            : {order:{reference:'SYNTHETIC-OLD', order_number:'SYNTHETIC-OLD', plan_code:'lite', amount_minor:29900, currency:'TRY', bank:{iban:'TR000000000000000000000000', account_holder:'Synthetic fixture'}}};
          await fulfill(route, outcome === 'success' ? body : {detail:{message:'Old checkout failed'}}, outcome === 'success' ? 200 : 503);
        } else await fulfill(route, {detail:{message:'Synthetic retry unavailable'}}, 503);
        return true;
      });
      try {
        await choosePlan(page);
        await page.locator(method === 'hosted' ? '#checkoutCardButton' : '#checkoutBankButton').click();
        await received.promise;
        await page.locator('#checkoutClose').click();
        await choosePlan(page, nextPlan);
        await expect(page.locator('#checkoutCardButton')).toBeDisabled();
        await expect(page.locator('#checkoutBankButton')).toBeDisabled();
        await page.locator('#checkoutForm').evaluate(form => form.requestSubmit());
        expect(payloads).toHaveLength(1);
        const response = page.waitForResponse(`${API}${path}`);
        pending.resolve();
        await finishResponse(page, response);
        await expect(page).not.toHaveURL(/unexpectedRedirect/);
        await expect(page.locator('#checkoutPanel')).toBeVisible();
        await expect(page.locator('#checkoutPlanCode')).toHaveValue(nextPlan);
        await expect(page.locator('#checkoutNotice')).not.toContainText('Old checkout failed');
        await expect(page.locator('#errorBox')).toBeHidden();
        await expect(page.locator('#transferPanel')).toBeHidden();
        await expect(page.locator('#checkoutCardButton')).toBeEnabled();
        await page.locator('#checkoutProtectedBankButton').click();
        await expect(page.locator('#bankTransferContinue')).toBeEnabled();
        await page.locator('#bankTransferContinue').click();
        await expect(page.locator('#checkoutNotice')).toHaveText('Synthetic retry unavailable');
        await expect(page.locator('#bankTransferContinue')).toBeEnabled();
        expect(payloads).toHaveLength(2);
        expect(payloads[1]).toMatchObject({plan_code:nextPlan, interval:'monthly', currency:'TRY', payment_method:'bank_transfer'});
      } finally { pending.resolve(); }
    });
  }
}
