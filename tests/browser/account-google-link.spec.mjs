import {test, expect} from './fixtures.mjs';

const API = 'https://api.lecturesift.com';
const CORS = {
  'Access-Control-Allow-Origin':'http://127.0.0.1:4173',
  'Access-Control-Allow-Methods':'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers':'authorization, content-type',
};
const CLIENT_ID = '123456789-synthetic.apps.googleusercontent.com';
const PASSWORD = 'Synthetic-password1';
const fulfill = (route, json, status = 200) => route.fulfill({status, headers:CORS, json});
const account = linked => ({
  user:{id:'synthetic-google-link', email:'student@example.invalid', first_name:'Student', email_verified:true},
  plan:{code:'free'}, remaining_minutes:60, used_minutes:0, credit_minutes:0,
  ...(linked === undefined ? {} : {google_linked:linked}),
});

// A local stand-in for Google Identity Services; no request leaves the runner.
const SYNTHETIC_GIS = `window.google={accounts:{id:{
  initialize(options){window.__syntheticGis=options;},
  renderButton(target,options){window.__syntheticGisButton=options;const button=document.createElement('button');button.type='button';
    button.id='syntheticGoogleButton';button.textContent='Continue with Google';
    button.onclick=()=>window.__syntheticGis.callback({credential:'synthetic.google.credential'});target.append(button);}
}}};`;

async function prepare(page, context, {linked, googleEnabled = true}) {
  const state = {linked, scriptLoads:0, configLoads:0, posts:[]};
  await page.addInitScript(() => {
    localStorage.setItem('lecturesift-consent-v1', JSON.stringify({version:1, analytics:false, advertising:false, updated_at:new Date().toISOString()}));
    localStorage.setItem('lecturesift-billing-token', 'synthetic-google-link-session');
  });
  await context.route('https://accounts.google.com/gsi/client', route => {
    state.scriptLoads += 1;
    return route.fulfill({status:200, contentType:'application/javascript', body:SYNTHETIC_GIS});
  });
  await page.route(`${API}/**`, async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'OPTIONS') return route.fulfill({status:204, headers:CORS});
    if (path === '/billing/me') return fulfill(route, {account:account(state.linked)});
    if (path === '/billing/referrals') return fulfill(route, {ok:true, referrals:{enabled:false}});
    if (path === '/billing/me/refund-requests') return fulfill(route, {requests:[]});
    if (path === '/billing/auth-config') {
      state.configLoads += 1;
      return fulfill(route, {ok:true, registration:{required_fields:['email', 'password'], password_min_length:10},
        google:googleEnabled ? {enabled:true, client_id:CLIENT_ID} : {enabled:false, client_id:null}});
    }
    if (request.method() === 'POST' && ['/billing/me/google/link', '/billing/me/google/unlink'].includes(path)) {
      const body = request.postDataJSON();
      state.posts.push({path, body});
      if (body.current_password !== PASSWORD) {
        return fulfill(route, {detail:{code:'LS-GOOGLE-05', message:'Mevcut parola hatalı.'}}, 400);
      }
      const linking = path.endsWith('/link');
      state.linked = linking;
      return fulfill(route, linking
        ? {ok:true, google_link:'linked', message:'Google hesabın bu hesaba bağlandı. Artık Google ile giriş yaptığında bu hesap açılır.', account:account(true)}
        : {ok:true, removed:true, message:'Google ile giriş bu hesaptan kaldırıldı.', account:account(false)});
    }
    return route.fallback();
  });
  return state;
}

async function noHorizontalOverflow(page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
}

// Text over its composited background, as the reader sees it in this theme.
async function expectReadable(page, selector) {
  const ratios = await page.locator(selector).evaluateAll(nodes => nodes.filter(node => node.offsetParent).map(node => {
    const rgba = value => {
      const parts = (value.match(/[\d.]+/g) || []).map(Number);
      return [parts[0] || 0, parts[1] || 0, parts[2] || 0, parts.length > 3 ? parts[3] : 1];
    };
    const layers = [];
    for (let current = node; current; current = current.parentElement) {
      const color = rgba(getComputedStyle(current).backgroundColor);
      if (color[3] > 0) layers.push(color);
      if (color[3] >= 1) break;
    }
    let paper = [255, 255, 255];
    for (const [r, g, b, a] of layers.reverse()) paper = [r, g, b].map((channel, index) => channel * a + paper[index] * (1 - a));
    const luminance = values => values.map(value => {
      const channel = value / 255;
      return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
    }).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
    const ink = luminance(rgba(getComputedStyle(node).color).slice(0, 3));
    const back = luminance(paper);
    return {text:node.textContent.trim().slice(0, 40), ratio:(Math.max(ink, back) + .05) / (Math.min(ink, back) + .05)};
  }));
  expect(ratios.length).toBeGreaterThan(0);
  for (const {text, ratio} of ratios) expect(ratio, text).toBeGreaterThanOrEqual(4.5);
}

test('an account links Google after confirming its password, and loads Google only on request', async ({page, context}, testInfo) => {
  const state = await prepare(page, context, {linked:false});
  await page.goto('/en/account.html#account-security');
  const card = page.locator('#googleLinkCard');
  await expect(card).toBeVisible();
  await expect(card.locator('h2')).toHaveText('Sign in with Google');
  await expect(page.locator('#googleLinkState')).toHaveText('Not linked');
  await expect(page.locator('#googleLinkStart')).toHaveText('Link Google account');
  await expect(page.locator('#googleUnlinkButton')).toBeHidden();
  expect(state.configLoads).toBe(1);
  expect(state.scriptLoads).toBe(0);

  // Nothing opens without the password.
  await page.locator('#googleLinkStart').click();
  await expect(page.locator('#googleLinkNotice')).toHaveText('Enter your current password first.');
  await expect(page.locator('#googleLinkNotice')).toHaveClass(/\berror\b/);
  expect(state.scriptLoads).toBe(0);

  await page.locator('#googleLinkPassword').fill('Wrong-password1');
  await page.locator('#googleLinkStart').click();
  await expect(page.locator('#syntheticGoogleButton')).toBeVisible();
  await expect(page.locator('#googleLinkStart')).toBeHidden();
  await expect(page.locator('#googleLinkNotice')).toHaveText('Continue with the Google button and choose the Google account you want to link.');
  expect(state.scriptLoads).toBe(1);
  expect(await page.evaluate(() => [window.__syntheticGis.client_id, window.__syntheticGis.context])).toEqual([CLIENT_ID, 'use']);
  const theme = await page.locator('html').getAttribute('data-theme');
  expect(await page.evaluate(() => window.__syntheticGisButton.theme)).toBe(theme === 'light' ? 'outline' : 'filled_black');

  await page.locator('#syntheticGoogleButton').click();
  await expect(page.locator('#googleLinkNotice')).toHaveText('Your current password is incorrect.');
  await expect(page.locator('#googleLinkNotice')).toHaveClass(/\berror\b/);
  await expect(page.locator('#googleLinkState')).toHaveText('Not linked');
  await expectReadable(page, '#googleLinkNotice');

  await page.locator('#googleLinkPassword').fill(PASSWORD);
  await page.locator('#syntheticGoogleButton').click();
  await expect(page.locator('#googleLinkState')).toHaveText('Linked');
  await expect(page.locator('#googleLinkNotice')).toHaveText('Your Google account is now linked to this account. Signing in with Google now opens this account.');
  await expect(page.locator('#googleLinkNotice')).not.toHaveClass(/\berror\b/);
  await expect(page.locator('#googleUnlinkButton')).toBeVisible();
  await expect(page.locator('#syntheticGoogleButton')).toBeHidden();
  await expect(page.locator('#googleLinkPassword')).toHaveValue('');
  expect(state.posts.map(post => post.path)).toEqual(['/billing/me/google/link', '/billing/me/google/link']);
  expect(state.posts.map(post => Object.keys(post.body).sort())).toEqual([['credential', 'current_password'], ['credential', 'current_password']]);
  expect(state.posts[1].body).toEqual({credential:'synthetic.google.credential', current_password:PASSWORD});
  expect(state.scriptLoads).toBe(1);

  await expectReadable(page, '#googleLinkCard :is(h2, .account-meta span, .account-meta strong, .empty-copy, .field > span, button, #googleLinkNotice)');
  await noHorizontalOverflow(page);
  await card.screenshot({path:testInfo.outputPath('account-google-link.jpg'), quality:75});
});

test('a linked account removes the link with its password without loading Google', async ({page, context}) => {
  const state = await prepare(page, context, {linked:true});
  await page.goto('/en/account.html#account-security');
  await expect(page.locator('#googleLinkState')).toHaveText('Linked');
  await expect(page.locator('#googleLinkStart')).toBeHidden();
  await expect(page.locator('#googleUnlinkButton')).toHaveText('Remove link');
  expect(state.configLoads).toBe(0);

  await page.locator('#googleUnlinkButton').click();
  await expect(page.locator('#googleLinkNotice')).toHaveText('Enter your current password first.');
  expect(state.posts).toEqual([]);

  await page.locator('#googleLinkPassword').fill(PASSWORD);
  await page.locator('#googleUnlinkButton').click();
  await expect(page.locator('#googleLinkState')).toHaveText('Not linked');
  await expect(page.locator('#googleLinkNotice')).toHaveText('Sign-in with Google was removed from this account.');
  await expect(page.locator('#googleUnlinkButton')).toBeHidden();
  await expect(page.locator('#googleLinkStart')).toBeVisible();
  await expect(page.locator('#googleLinkPassword')).toHaveValue('');
  expect(state.posts).toEqual([{path:'/billing/me/google/unlink', body:{current_password:PASSWORD}}]);
  expect(state.scriptLoads).toBe(0);
  await expectReadable(page, '#googleLinkCard :is(.account-meta strong, #googleLinkNotice)');
  await noHorizontalOverflow(page);
});

test('the section stays hidden without Google sign-in or on a backend without the flag', async ({page, context}) => {
  const state = await prepare(page, context, {linked:false, googleEnabled:false});
  await page.goto('/en/account.html#account-security');
  await expect(page.locator('#passwordForm')).toBeVisible();
  await expect.poll(() => state.configLoads).toBe(1);
  await expect(page.locator('#googleLinkCard')).toBeHidden();

  state.linked = undefined;
  await page.reload();
  await expect(page.locator('#passwordForm')).toBeVisible();
  await expect(page.locator('#googleLinkCard')).toBeHidden();
  expect(state.configLoads).toBe(1);
  expect(state.scriptLoads).toBe(0);
});
