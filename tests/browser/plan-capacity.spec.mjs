import {test, expect} from './fixtures.mjs';

const API = 'https://api.lecturesift.com';
const headers = {'Access-Control-Allow-Origin':'http://127.0.0.1:4173'};
const requirement = {need_minutes:198, need_pages:100, need_ocr:0, need_bytes:1048576, need_files:1};
const limits = {
  max_minutes_per_job:240, max_document_pages:350, max_ocr_pages:100,
  max_document_upload_mb:100, max_files_per_job:16,
};
const plans = [
  {code:'credit', entitlements:{minutes:180, limits:{...limits, max_minutes_per_job:180}}},
  {code:'lite', entitlements:{minutes:400, limits:{...limits, max_minutes_per_job:120}}},
  {code:'plus', entitlements:{minutes:900, limits}},
];
const status = (page, code) => page.locator(`[data-plan-card="${code}"] [data-plan-capacity]`);

async function installCatalog(page, remotePlans = plans) {
  await page.addInitScript(() => localStorage.setItem('lecturesift-currency', 'TRY'));
  await page.route(`${API}/billing/plans?currency=TRY`, route => route.request().method() === 'OPTIONS'
    ? route.fallback() : route.fulfill({headers, json:{selected_currency:'TRY', plans:remotePlans}}));
}

async function openPlans(page, need = requirement) {
  await page.goto(`/en/plans.html?${new URLSearchParams(need)}`);
  const consent = page.locator('[data-consent="essential"]');
  if (await consent.isVisible()) await consent.click();
  await expect(page.locator('#plansGrid')).toHaveAttribute('aria-busy', 'false');
}

test('measured document compares both per-job and total minutes without choosing a purchase', async ({page, isolatedNetwork}) => {
  await installCatalog(page);
  await openPlans(page);
  await expect(page.locator('#planCapacityContext')).toContainText('198 minutes');
  await expect(status(page, 'credit')).toHaveAttribute('data-plan-capacity', 'insufficient');
  await expect(status(page, 'lite')).toHaveAttribute('data-plan-capacity', 'insufficient');
  await expect(status(page, 'plus')).toHaveAttribute('data-plan-capacity', 'fits');
  await expect(status(page, 'plus')).toContainText('remaining allowance');
  await expect(page.locator('[data-plan="lite"][data-interval="monthly"]')).toBeEnabled();
  await expect(page.locator('#checkoutPanel')).toBeHidden();
  expect(isolatedNetwork.apiCalls.filter(call => call.includes('/billing/checkout'))).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.locator('[data-plan="plus"][data-interval="monthly"]').click();
  await expect(page).toHaveURL(/\/en\/login(?:\.html)?\?next=/);
  const next = new URL(new URL(page.url()).searchParams.get('next'), page.url());
  expect(next.searchParams.get('need_minutes')).toBe('198');
  expect(next.searchParams.get('need_ocr')).toBe('0');
});

test('page, OCR, byte and source limits can each rule out a plan', async ({page}) => {
  await installCatalog(page);
  for (const change of [
    {need_pages:351}, {need_ocr:101}, {need_bytes:100 * 1024 * 1024 + 1}, {need_files:17},
  ]) {
    await openPlans(page, {...requirement, ...change});
    await expect(status(page, 'plus')).toHaveAttribute('data-plan-capacity', 'insufficient');
  }
});

test('missing catalog limits never inherit a false confirmation from fallback prices', async ({page}) => {
  const partialLimits = {...limits};
  delete partialLimits.max_document_pages;
  await installCatalog(page, [{code:'plus', entitlements:{minutes:900, limits:partialLimits}}]);
  await openPlans(page);
  await expect(status(page, 'plus')).toHaveAttribute('data-plan-capacity', 'check');
  await expect(status(page, 'credit')).toHaveAttribute('data-plan-capacity', 'check');
});

test('missing job metadata remains unknown while known violations stay visible', async ({page}) => {
  await installCatalog(page);
  await openPlans(page, {need_minutes:198});
  await expect(status(page, 'plus')).toHaveAttribute('data-plan-capacity', 'check');
  await expect(status(page, 'credit')).toHaveAttribute('data-plan-capacity', 'insufficient');
});

test('invalid or duplicate required minutes do not create a recommendation context', async ({page}) => {
  await installCatalog(page);
  for (const query of ['need_minutes=0', 'need_minutes=Infinity', 'need_minutes=198&need_minutes=1']) {
    await openPlans(page, new URLSearchParams(query));
    await expect(page.locator('#planCapacityContext')).toHaveCount(0);
    await expect(page.locator('[data-plan-capacity]')).toHaveCount(0);
  }
});
