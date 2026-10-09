import {test, expect} from './fixtures.mjs';

const API = 'https://api.lecturesift.com';
const TOKEN = 'lecturesift-billing-token';
const CORS = {
  'Access-Control-Allow-Origin':'http://127.0.0.1:4173',
  'Access-Control-Allow-Methods':'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers':'authorization, content-type',
};
const account = {
  user:{id:'synthetic-account', email:'student@example.invalid', first_name:'Student', email_verified:true},
  plan:{code:'free'}, remaining_minutes:60, used_minutes:0, credit_minutes:0,
};
const fulfill = (route, json, status = 200) => route.fulfill({status, headers:CORS, json});
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};

async function prepare(page, override, signedIn = true) {
  await page.addInitScript(({key, signedIn}) => {
    localStorage.setItem('lecturesift-consent-v1', JSON.stringify({version:1, analytics:false, advertising:false, updated_at:new Date().toISOString()}));
    if (!sessionStorage.getItem('account-fixture-initialized')) {
      sessionStorage.setItem('account-fixture-initialized', '1');
      if (signedIn) localStorage.setItem(key, 'synthetic-current-session');
    }
  }, {key:TOKEN, signedIn});
  await page.route(`${API}/**`, async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === 'OPTIONS') return route.fulfill({status:204, headers:CORS});
    if (override && await override(route, path)) return;
    if (path === '/billing/me') return fulfill(route, {account});
    if (path === '/billing/referrals') return fulfill(route, {ok:true, referrals:{enabled:false}});
    if (path === '/billing/me/refund-requests') return fulfill(route, {requests:[]});
    return route.fallback();
  });
}

for (const failure of ['unavailable', 'forbidden', 'network', 'malformed']) {
  test(`account ${failure} preserves the session and supports an explicit retry`, async ({page}) => {
    let fail = true;
    await prepare(page, async (route, path) => {
      if (path !== '/billing/me' || !fail) return false;
      if (failure === 'network') await route.abort('failed');
      else await fulfill(route, failure === 'malformed' ? {account:{user:{email:'incomplete@example.invalid'}}}
        : {detail:{message:'Synthetic temporary failure'}}, failure === 'malformed' ? 200 : failure === 'forbidden' ? 403 : 503);
      return true;
    });
    await page.goto('/en/account.html');
    await expect(page.locator('#accountLoadState')).toBeVisible();
    await expect(page.locator('#accountRetry')).toBeEnabled();
    expect(await page.evaluate(key => localStorage.getItem(key), TOKEN)).toBe('synthetic-current-session');
    expect(new URL(page.url()).pathname).toBe('/en/account.html');
    fail = false;
    await page.locator('#accountRetry').click();
    await expect(page.locator('#accountPage')).toBeVisible();
    await expect(page.locator('#accountLoadState')).toBeHidden();
    await expect(page.locator('#accountEmail')).toHaveText(account.user.email);
  });
}

test('an expired account session redirects to sign in while preserving language and section', async ({page}) => {
  await prepare(page, async (route, path) => {
    if (path !== '/billing/me') return false;
    await fulfill(route, {detail:{message:'Expired synthetic session'}}, 401);
    return true;
  });
  await page.goto('/en/account.html#account-referrals');
  await expect(page).toHaveURL(/\/en\/login\.html\?next=/);
  expect(new URL(page.url()).searchParams.get('next')).toBe('/en/account.html#account-referrals');
  expect(await page.evaluate(key => localStorage.getItem(key), TOKEN)).toBeNull();
});

for (const malformed of [{}, {token:null}, {token:'bad token'}]) {
  test(`a malformed login success ${JSON.stringify(malformed)} stays retryable without a bogus session`, async ({page}) => {
    let attempts = 0;
    await prepare(page, async (route, path) => {
      if (path !== '/billing/login') return false;
      attempts += 1;
      await fulfill(route, attempts === 1 ? malformed : {token:'synthetic-login-session'});
      return true;
    }, false);
    const next = '/en/account.html?source=login#account-security';
    await page.goto(`/en/login.html?next=${encodeURIComponent(next)}`);
    await page.locator('#email').fill(account.user.email);
    await page.locator('#password').fill('synthetic-password-only');
    await page.locator('#loginSubmit').click();
    await expect(page.locator('#authNotice')).toBeVisible();
    await expect(page.locator('#loginSubmit')).toBeEnabled();
    expect(await page.evaluate(key => localStorage.getItem(key), TOKEN)).toBeNull();
    await page.locator('#loginSubmit').click();
    await expect(page).toHaveURL(`http://127.0.0.1:4173${next}`);
    await expect(page.locator('#accountPage')).toBeVisible();
    expect(attempts).toBe(2);
  });
}

for (const next of ['/\\outside.example', '//outside.example', '/en/account.html?source=return#account-security']) {
  test(`an existing login session follows only a safe local destination ${next}`, async ({page}) => {
    await prepare(page);
    await page.goto(`/en/login.html?next=${encodeURIComponent(next)}`);
    const expected = next.startsWith('/en/') ? next : '/en/account.html';
    await expect(page).toHaveURL(`http://127.0.0.1:4173${expected}`);
    await expect(page.locator('#accountPage')).toBeVisible();
  });
}

test('a delayed header authentication failure cannot erase a newer session', async ({page}) => {
  const pending = deferred(), received = deferred();
  await prepare(page, async (route, path) => {
    if (path !== '/billing/me') return false;
    received.resolve();
    await pending.promise;
    await fulfill(route, {detail:{message:'Old session expired'}}, 401);
    return true;
  });
  try {
    await page.goto('/en/about.html');
    await received.promise;
    await page.evaluate(key => localStorage.setItem(key, 'synthetic-new-session'), TOKEN);
    const response = page.waitForResponse(`${API}/billing/me`);
    pending.resolve();
    await (await response).finished();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    expect(await page.evaluate(key => localStorage.getItem(key), TOKEN)).toBe('synthetic-new-session');
    await expect(page.locator('#accountButton')).toHaveAttribute('data-session-state', 'signed-in');
  } finally { pending.resolve(); }
});
