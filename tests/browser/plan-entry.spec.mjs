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
  await page.locator('.plan-action[data-plan="business"]').click();
  await expect(page).toHaveURL(/\/en\/contact(?:\.html)?$/);
  expect(isolatedNetwork.apiCalls.filter(call => call.includes('/billing/checkout'))).toEqual([]);
});

test('paid plan retains selected billing interval and language at login', async ({page}) => {
  await openPlans(page);
  await page.locator('.plan-action[data-plan="lite"][data-interval="annual"]').click();
  await expect(page).toHaveURL(/\/en\/login(?:\.html)?\?next=/);
  const next = new URL(new URL(page.url()).searchParams.get('next'), page.url());
  expect(next.pathname).toMatch(/^\/en\/plans(?:\.html)?$/);
  expect(next.searchParams.get('plan')).toBe('lite');
  expect(next.searchParams.get('interval')).toBe('annual');
});
