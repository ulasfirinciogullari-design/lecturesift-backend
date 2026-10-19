import {test, expect, JOB_ID} from './fixtures.mjs';

const API = 'https://api.lecturesift.com';
const LOCAL = 'http://127.0.0.1:4173';
const CORS = {
  'Access-Control-Allow-Origin':LOCAL,
  'Access-Control-Allow-Methods':'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers':'authorization, content-type',
};
const GUEST_TOKEN = 'synthetic-guest-session-token';
const user = {id:'synthetic-signup-user', email:'new-student@example.invalid', country_code:'TR'};
const authConfig = google => ({
  ok:true,
  registration:{required_fields:['email', 'password'], password_min_length:10},
  google,
});

async function prepare(context) {
  await context.addInitScript(token => {
    if (location.origin !== 'http://127.0.0.1:4173') return;
    localStorage.setItem('lecturesift-consent-v1', JSON.stringify({
      version:1, analytics:false, advertising:false, updated_at:new Date().toISOString(),
    }));
    localStorage.setItem('lecturesift-ui', 'en');
    localStorage.setItem('lecturesift-currency', 'TRY');
    // The guest trial token belongs to this tab only; the device id is never enough.
    if (location.pathname.endsWith('/register.html')) sessionStorage.setItem('lecturesift-guest-token', token);
  }, GUEST_TOKEN);
}

async function capturePost(context, path, response) {
  const sent = [];
  await context.route(`${API}${path}`, async route => {
    if (route.request().method() === 'OPTIONS') return route.fulfill({status:204, headers:CORS});
    if (route.request().method() !== 'POST') return route.fallback();
    sent.push(route.request().postDataJSON());
    await route.fulfill({status:200, headers:CORS, json:response});
  });
  return sent;
}

async function fillShortForm(page) {
  await page.locator('#email').fill(user.email);
  await page.locator('#password').fill('SyntheticPassword123');
  await page.locator('#terms').check();
  await page.locator('#registerSubmit').click();
  await expect(page.locator('#successBox')).toBeVisible();
}

test('signup asks only for email, one password and consent, and sends this tab guest session', async ({page, context}) => {
  await prepare(context);
  const sent = await capturePost(context, '/billing/register', {user, referral_status:'not_provided', guest_claim:{status:'pending'}});
  await page.goto('/en/register.html');
  await expect(page.locator('#phone, #countryCode, #passwordConfirm')).toHaveCount(0);
  await expect(page.locator('#firstName')).toBeHidden();
  await expect(page.locator('#lastName')).toBeHidden();
  await expect(page.locator('#referralCode')).toBeHidden();
  await expect(page.locator('#terms')).toBeVisible();
  // Google stays off unless the backend publishes a client id.
  await expect(page.locator('#googleSignIn')).toBeHidden();

  const password = page.locator('#password');
  const toggle = page.locator('#passwordToggle');
  await password.fill('SyntheticPassword123');
  await expect(password).toHaveAttribute('type', 'password');
  await toggle.click();
  await expect(password).toHaveAttribute('type', 'text');
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await toggle.click();
  await expect(password).toHaveAttribute('type', 'password');
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');

  await fillShortForm(page);
  expect(sent).toHaveLength(1);
  expect(Object.keys(sent[0]).sort()).toEqual(['country_code', 'email', 'guest_token', 'password']);
  expect(sent[0]).toMatchObject({email:user.email, password:'SyntheticPassword123', guest_token:GUEST_TOKEN});
  await expect(page.locator('#registerGuestNote')).toBeVisible();
});

test('a weak password is explained before any request is sent', async ({page, context}) => {
  await prepare(context);
  const sent = await capturePost(context, '/billing/register', {user, referral_status:'not_provided'});
  await page.goto('/en/register.html');
  await page.locator('#email').fill(user.email);
  await page.locator('#password').fill('alllowercase1');
  await page.locator('#terms').check();
  await page.locator('#registerSubmit').click();
  await expect(page.locator('#authNotice')).toBeVisible();
  await expect(page.locator('#successBox')).toBeHidden();
  expect(sent).toHaveLength(0);
});

test('an older backend without the signup config still receives the names it requires', async ({page, context}) => {
  await prepare(context);
  await context.route(`${API}/billing/auth-config`, route => route.fulfill({
    status:404, headers:{'Access-Control-Allow-Origin':LOCAL}, json:{detail:'Not Found'},
  }));
  const sent = await capturePost(context, '/billing/register', {user, referral_status:'not_provided'});
  await page.goto('/en/register.html');
  await expect(page.locator('#firstName')).toBeVisible();
  await page.locator('#firstName').fill('Synthetic');
  await page.locator('#lastName').fill('Student');
  await fillShortForm(page);
  expect(sent[0]).toMatchObject({first_name:'Synthetic', last_name:'Student', guest_token:GUEST_TOKEN});
});

test('Google sign-in appears with a published client id and carries the guest session', async ({page, context}) => {
  await prepare(context);
  const clientId = '123456789-synthetic.apps.googleusercontent.com';
  await context.route(`${API}/billing/auth-config`, route => route.fulfill({
    status:200, headers:{'Access-Control-Allow-Origin':LOCAL},
    json:authConfig({enabled:true, client_id:clientId}),
  }));
  // A local stand-in for Google Identity Services; no request leaves the runner.
  await context.route('https://accounts.google.com/gsi/client', route => route.fulfill({
    status:200, contentType:'application/javascript',
    body:`window.google={accounts:{id:{
      initialize(options){window.__syntheticGis=options;},
      renderButton(target){const button=document.createElement('button');button.type='button';button.id='syntheticGoogleButton';
        button.textContent='Continue with Google';button.onclick=()=>window.__syntheticGis.callback({credential:'synthetic.google.credential'});target.append(button);}
    }}};`,
  }));
  const sent = await capturePost(context, '/billing/google', {
    token:'synthetic-google-session', created:true, user, guest_claim:{status:'claimed', jobs:1},
  });
  const next = `/workspace.html?job=${JOB_ID}`;
  await page.goto(`/en/register.html?next=${encodeURIComponent(next)}`);
  await expect(page.locator('#googleSignIn')).toBeVisible();
  expect(await page.evaluate(() => window.__syntheticGis.client_id)).toBe(clientId);
  await page.locator('#syntheticGoogleButton').click();
  await expect(page).toHaveURL(new RegExp(`/workspace\\.html\\?job=${JOB_ID}$`));
  expect(sent).toHaveLength(1);
  expect(sent[0]).toMatchObject({credential:'synthetic.google.credential', guest_token:GUEST_TOKEN});
  expect(await page.evaluate(() => localStorage.getItem('lecturesift-billing-token'))).toBe('synthetic-google-session');
  expect(await page.evaluate(() => sessionStorage.getItem('lecturesift-guest-token'))).toBeNull();
});

for (const plan of ['guest', 'free']) {
  test(`the result screen offers "save your result" only to a guest (${plan})`, async ({page, context}) => {
    await prepare(context);
    await page.addInitScript(() => localStorage.setItem('lecturesift-billing-token', 'ci-synthetic-token-not-valid-on-any-server'));
    await page.route(`${API}/billing/me`, route => route.fulfill({status:200, headers:CORS, json:{account:{
      user:{id:`synthetic-${plan}`, email:'browser-smoke@example.invalid', first_name:'', email_verified:true},
      plan:{code:plan}, remaining_minutes:0, used_minutes:0, credit_minutes:0,
    }}}));
    await page.goto(`/workspace.html?job=${JOB_ID}`);
    await expect(page.locator('#results')).toBeVisible();
    const notice = page.locator('#guestSaveResult');
    if (plan === 'free') return expect(notice).toBeHidden();
    await expect(notice).toBeVisible();
    const register = new URL(await page.locator('#guestSaveRegister').getAttribute('href'), page.url());
    expect(register.pathname).toMatch(/\/register\.html$/);
    const back = new URL(register.searchParams.get('next'), page.url());
    expect(back.pathname).toMatch(/\/workspace\.html$/);
    expect(back.searchParams.get('job')).toBe(JOB_ID);
    const login = new URL(await page.locator('#guestSaveLogin').getAttribute('href'), page.url());
    expect(login.pathname).toMatch(/\/login\.html$/);
    expect(login.searchParams.get('next')).toBe(register.searchParams.get('next'));
  });
}
