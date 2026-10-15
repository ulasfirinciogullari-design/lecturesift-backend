import {test, expect} from './fixtures.mjs';

const API = 'https://api.lecturesift.com';
const CONTINUATION = 'lecturesift-signup-continuation-v1';
const user = {id:'synthetic-signup-user', email:'new-student@example.invalid', country_code:'TR'};
const CORS = {
  'Access-Control-Allow-Origin':'http://127.0.0.1:4173',
  'Access-Control-Allow-Methods':'POST,OPTIONS',
  'Access-Control-Allow-Headers':'content-type',
};

async function prepare(context, verifiedUser = user) {
  await context.addInitScript(() => {
    // A newly opened tab starts at about:blank, which has no storage origin.
    if (location.origin !== 'http://127.0.0.1:4173') return;
    localStorage.setItem('lecturesift-consent-v1', JSON.stringify({
      version:1, analytics:false, advertising:false, updated_at:new Date().toISOString(),
    }));
  });
  const bodies = new Map([
    ['/billing/register', {user, referral_status:'not_provided'}],
    ['/billing/verify-email-code', {user:verifiedUser, token:'synthetic-verified-session'}],
    ['/billing/verify-email', {user:verifiedUser, token:'synthetic-verified-session'}],
  ]);
  await context.route(`${API}/**`, async route => {
    const request = route.request();
    const body = bodies.get(new URL(request.url()).pathname);
    if (!body) return route.fallback();
    if (request.method() === 'OPTIONS') return route.fulfill({status:204, headers:CORS});
    if (request.method() !== 'POST') return route.fallback();
    await route.fulfill({status:200, headers:CORS, json:body});
  });
}

async function register(page) {
  await page.locator('#firstName').fill('Synthetic');
  await page.locator('#lastName').fill('Student');
  await page.locator('#email').fill(user.email);
  await page.locator('#countryCode').selectOption('TR');
  await page.locator('#password').fill('SyntheticPassword123');
  await page.locator('#passwordConfirm').fill('SyntheticPassword123');
  await page.locator('#terms').check();
  await page.locator('#registerSubmit').click();
  await expect(page.locator('#successBox')).toBeVisible();
}

async function verifyCode(page) {
  await page.locator('#verifyCode').fill('123456');
  await page.locator('#verifyCodeSubmit').click();
  await expect(page.locator('#verifyAction')).toBeVisible();
}

test('a selected plan survives sign-in links, registration and code verification', async ({page, context}) => {
  await prepare(context);
  const next = '/en/plans.html?plan=plus&interval=annual&currency=TRY#plan-grid';
  await page.goto(`/en/login.html?next=${encodeURIComponent(next)}`);
  const forgot = page.locator('a[data-auth-route="/forgot-password.html"]');
  expect(new URL(await forgot.getAttribute('href'), page.url()).searchParams.get('next')).toBe(next);
  await forgot.click();
  await page.locator('a[data-auth-route="/login.html"]').click();
  expect(new URL(page.url()).searchParams.get('next')).toBe(next);
  await page.locator('a[data-auth-route="/register.html"]').click();
  expect(new URL(page.url()).pathname).toBe('/en/register.html');
  expect(new URL(page.url()).searchParams.get('next')).toBe(next);
  await register(page);
  await page.locator('#enterCodeLink').click();
  expect(new URL(page.url()).pathname).toBe('/en/verify.html');
  expect(new URL(page.url()).searchParams.get('next')).toBe(next);
  await verifyCode(page);
  await expect(page.locator('#verifyAction')).toHaveAttribute('href', next);
  await expect(page.locator('#verifyAction')).toHaveText('Continue');
  expect(await page.evaluate(key => localStorage.getItem(key), CONTINUATION)).toBeNull();
});

test('an email verification opened in another tab restores the same account destination once', async ({page, context}) => {
  await prepare(context);
  const next = '/en/workspace.html#library';
  await page.goto(`/en/register.html?next=${encodeURIComponent(next)}`);
  await register(page);
  const emailTab = await context.newPage();
  try {
    // This is the backend email link shape: a token, with no next or email.
    await emailTab.goto('/verify.html?token=synthetic-email-token');
    await expect(emailTab.locator('#verifyAction')).toBeVisible();
    await expect(emailTab.locator('#verifyAction')).toHaveAttribute('href', next);
    expect(await emailTab.evaluate(key => localStorage.getItem(key), CONTINUATION)).toBeNull();
  } finally { await emailTab.close(); }
});

for (const state of ['different-account', 'expired', 'unsafe-path']) {
  test(`email verification ignores a ${state} saved continuation`, async ({page, context}) => {
    await prepare(context);
    await page.addInitScript(({key, account, state}) => {
      localStorage.setItem(key, JSON.stringify({
        userId:state === 'different-account' ? 'another-user' : account.id,
        email:account.email,
        next:state === 'unsafe-path' ? '/a/..//outside.example' : '/en/plans.html?plan=plus',
        createdAt:Date.now() - (state === 'expired' ? 25 * 60 * 60 * 1000 : 1000),
      }));
    }, {key:CONTINUATION, account:user, state});
    await page.goto('/en/verify.html?token=synthetic-email-token');
    await expect(page.locator('#verifyAction')).toBeVisible();
    await expect(page.locator('#verifyAction')).toHaveAttribute('href', '/en/workspace.html');
    expect(await page.evaluate(key => localStorage.getItem(key), CONTINUATION)).toBeNull();
  });
}

test('unsafe or malformed next links are dropped and new accounts default to their localized workspace', async ({page, context}) => {
  await prepare(context);
  for (const next of ['https://outside.example', '//outside.example', '/a/..//outside.example', '/%5coutside.example', '/en/%ZZ']) {
    await page.goto(`/en/login.html?next=${encodeURIComponent(next)}`);
    await expect(page.locator('a[data-auth-route="/register.html"]')).toHaveAttribute('href', '/en/register.html');
  }
  await page.locator('a[data-auth-route="/register.html"]').click();
  await register(page);
  await page.locator('#enterCodeLink').click();
  // Even a modified code link cannot turn the validated destination external.
  const verification = new URL(page.url());
  verification.searchParams.set('next', '/a/..//outside.example');
  await page.goto(verification.href);
  await verifyCode(page);
  await expect(page.locator('#verifyAction')).toHaveAttribute('href', '/en/workspace.html');
});

test('changing the verification email does not inherit another account purchase destination', async ({page, context}) => {
  const other = {id:'another-user', email:'other-student@example.invalid'};
  await prepare(context, other);
  await page.goto('/en/register.html?next=%2Fen%2Fplans.html%3Fplan%3Dplus');
  await register(page);
  await page.locator('#enterCodeLink').click();
  await page.locator('#verifyEmail').fill(other.email);
  await verifyCode(page);
  await expect(page.locator('#verifyAction')).toHaveAttribute('href', '/en/workspace.html');
});
