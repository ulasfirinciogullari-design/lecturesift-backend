import {test, expect, JOB_ID} from './fixtures.mjs';

const API = 'https://api.lecturesift.com';
const cors = {'Access-Control-Allow-Origin':'http://127.0.0.1:4173', 'Access-Control-Allow-Methods':'GET,POST,OPTIONS', 'Access-Control-Allow-Headers':'authorization,content-type'};
const guestAccount = {user:{id:'synthetic-funnel-guest', email:'funnel-guest@example.invalid'}, remaining_minutes:30,
  plan:{code:'guest', entitlements:{limits:{max_files_per_job:1, max_media_upload_mb:150, max_document_upload_mb:35, max_minutes_per_job:30}, download_enabled:false}}};
const privatePdf = {name:'private-lecture-notes.pdf', mimeType:'application/pdf', buffer:Buffer.from('Synthetic PDF transport fixture; no extraction.')};

// A stand-in for analytics.js that records what the page scripts hand to
// window.lectureSiftTrack, across same-tab navigations. The real file's
// consent, origin and parameter checks run in tests/test_funnel_analytics_runtime.py.
const RECORDER = `(() => {
  const read = () => JSON.parse(sessionStorage.getItem('synthetic-funnel') || '[]');
  window.lectureSiftTrack = (name, parameters) => {
    sessionStorage.setItem('synthetic-funnel', JSON.stringify([...read(), [name, parameters]]));
    return Promise.resolve(true);
  };
  window.LectureSiftAnalytics = Object.freeze({track: () => Promise.resolve(false),
    trackConversion: () => Promise.resolve(false), refresh: () => Promise.resolve()});
  for (const item of (window.__lecturesiftAnalyticsQueue || []).splice(0)) {
    const result = item?.type === 'event' ? window.lectureSiftTrack(item.name, item.parameters) : Promise.resolve(false);
    result.then(value => item?.resolve?.(value));
  }
})();`;

const funnel = page => page.evaluate(() => JSON.parse(sessionStorage.getItem('synthetic-funnel') || '[]'));
const answer = json => route => route.fulfill(route.request().method() === 'OPTIONS' ? {status:204, headers:cors} : {headers:cors, json});

async function recordFunnel(page) {
  await page.route(/\/analytics\.js(?:\?|$)/, route => route.fulfill({status:200, contentType:'text/javascript', body:RECORDER}));
}

async function prepareGuest(page) {
  await recordFunnel(page);
  await page.addInitScript(() => {
    if (location.origin !== 'http://127.0.0.1:4173') return;
    localStorage.setItem('lecturesift-ui', 'en');
    localStorage.setItem('lecturesift-currency', 'TRY');
    const open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
      this.syntheticFunnelUpload = method === 'POST' && url === 'https://api.lecturesift.com/jobs';
      return open.call(this, method, url, ...rest);
    };
    XMLHttpRequest.prototype.send = function(body) {
      if (!this.syntheticFunnelUpload) return send.call(this, body);
      window.syntheticFunnelUpload = this;
    };
  });
  await page.route(`${API}/billing/guest-session`, answer({token:'synthetic-funnel-guest-token', account:guestAccount, trial:{used:false, remaining_minutes:30}}));
  await page.route(`${API}/billing/me`, answer({account:guestAccount}));
  await page.goto('/en/workspace.html');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#analyzeButton')).toHaveAttribute('data-guest-wrapped', '1');
  await page.locator('#classicFiles').setInputFiles(privatePdf);
  await page.locator('#analyzeButton').click();
  await page.waitForFunction(() => Boolean(window.syntheticFunnelUpload));
}

async function finishUpload(page, status, body) {
  await page.evaluate(({status, body}) => {
    const xhr = window.syntheticFunnelUpload;
    Object.defineProperty(xhr, 'status', {value:status});
    Object.defineProperty(xhr, 'responseText', {value:JSON.stringify(body)});
    xhr.dispatchEvent(new ProgressEvent('load'));
  }, {status, body});
}

test('a guest trial reports its accepted start and shown result with fixed labels only', async ({page}) => {
  await prepareGuest(page);
  expect(await funnel(page)).toEqual([]);
  await finishUpload(page, 202, {job_id:JOB_ID});
  await expect(page.locator('#resultHeading')).toHaveText('Synthetic browser quiz');
  await expect.poll(() => funnel(page)).toEqual([
    ['trial_start', {source_type:'document'}],
    ['trial_complete', {source_type:'document'}],
  ]);
  expect(JSON.stringify(await funnel(page))).not.toContain('private');
});

test('a used-trial rejection reports the sign-up wall without a trial start', async ({page}) => {
  await prepareGuest(page);
  await finishUpload(page, 402, {detail:{code:'LS-GUEST-05', message:'Synthetic server rejection'}});
  await expect(page.locator('#guestTrialSignup')).toHaveCount(1);
  await expect.poll(() => funnel(page)).toEqual([['trial_limit_reached', {reason:'trial_used'}]]);
});

test('a returning guest whose trial is used reports the sign-up wall once', async ({page}) => {
  await recordFunnel(page);
  await page.addInitScript(() => {
    if (location.origin !== 'http://127.0.0.1:4173') return;
    localStorage.setItem('lecturesift-ui', 'en');
    localStorage.setItem('lecturesift-currency', 'TRY');
    sessionStorage.setItem('lecturesift-guest-token', 'synthetic-funnel-guest-token');
  });
  await page.route(`${API}/billing/me`, answer({account:guestAccount}));
  await page.route(`${API}/billing/me/rollout`, answer({guest_trial:{used:true, remaining_minutes:0, job_id:'synthetic-used-trial'}, rewarded_ads:{enabled:false}}));
  await page.goto('/en/workspace.html');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#guestTrialSignup')).toHaveCount(1);
  await expect.poll(() => funnel(page)).toEqual([['trial_limit_reached', {reason:'trial_used'}]]);
});

test('a result download reports only its kind and format', async ({page}) => {
  await recordFunnel(page);
  await page.addInitScript(() => {
    if (location.origin !== 'http://127.0.0.1:4173') return;
    localStorage.setItem('lecturesift-billing-token', 'ci-synthetic-token-not-valid-on-any-server');
    localStorage.setItem('lecturesift-ui', 'en');
    localStorage.setItem('lecturesift-currency', 'TRY');
  });
  await page.route(`${API}/jobs/${JOB_ID}/result`, answer({
    title:'Synthetic download', job_type:'study_pack', summary:'A deterministic local fixture.',
    options:{include_summary:true, include_transcript:false, include_slides:false, quiz_count:0, flashcard_count:0},
    quiz:[], flashcards:[], slides:[], transcript_segments:[],
    artifacts:[{file:'private-lecture-summary.pdf', label:'Summary', format:'PDF', size_bytes:48}],
  }));
  await page.route(`${API}/jobs/${JOB_ID}/download`, route => route.fulfill(route.request().method() === 'OPTIONS'
    ? {status:204, headers:cors} : {headers:cors, contentType:'application/zip', body:'Synthetic package bytes'}));
  await page.goto(`/en/workspace.html?job=${JOB_ID}`);
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#downloadAll')).toBeVisible();
  await page.locator('#downloadAll').click();
  await expect.poll(() => funnel(page)).toEqual([['result_download', {content_type:'package', file_extension:'zip'}]]);
});

test('signing in reports a login event before leaving the page', async ({page}) => {
  await recordFunnel(page);
  await page.addInitScript(() => {
    if (location.origin !== 'http://127.0.0.1:4173') return;
    localStorage.setItem('lecturesift-consent-v1', JSON.stringify({
      version:1, analytics:false, advertising:false, updated_at:new Date().toISOString(),
    }));
  });
  await page.route(`${API}/billing/login`, answer({token:'synthetic-login-session'}));
  await page.route('http://127.0.0.1:4173/en/about.html', route => route.fulfill({status:200, contentType:'text/html', body:'<!doctype html><title>Signed in</title>'}));
  await page.goto(`/en/login.html?next=${encodeURIComponent('/en/about.html')}`);
  await page.locator('#email').fill('private-student@example.invalid');
  await page.locator('#password').fill('SyntheticPassword123');
  await page.locator('#loginSubmit').click();
  await expect(page).toHaveTitle('Signed in');
  expect(await funnel(page)).toEqual([['login', {method:'email'}]]);
});
