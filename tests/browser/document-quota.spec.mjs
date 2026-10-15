import {test, expect, JOB_ID} from './fixtures.mjs';

const API = 'https://api.lecturesift.com';
const headers = {
  'Access-Control-Allow-Origin':'http://127.0.0.1:4173',
  'Access-Control-Allow-Methods':'GET,OPTIONS',
  'Access-Control-Allow-Headers':'authorization,content-type',
};

async function openWorkspace(page, {account = null, rate = 200, locale = 'en', failed = false} = {}) {
  const writes = [];
  page.on('request', request => {
    if (request.url().startsWith(API) && !['GET', 'OPTIONS', 'HEAD'].includes(request.method())) writes.push(request.url());
  });
  await page.addInitScript(({registered, locale}) => {
    localStorage.setItem('lecturesift-ui', locale);
    localStorage.setItem('lecturesift-currency', 'TRY');
    if (registered) localStorage.setItem('lecturesift-billing-token', 'synthetic-quota-owner');
  }, {registered:Boolean(account), locale});
  await page.route(`${API}/billing/plans?currency=TRY`, route => route.fulfill({headers, json:{
    plans:[], selected_currency:'TRY', guest_trial_minutes:5,
    ...(rate == null ? {} : {document_words_per_credit_minute:rate}),
  }}));
  if (account) {
    await page.route(`${API}/billing/me`, route => route.fulfill(route.request().method() === 'OPTIONS'
      ? {status:204, headers}
      : {headers, json:{account:{user:{id:'synthetic-quota-user', email:'quota@example.invalid'}, ...account}}}));
  }
  if (failed) {
    await page.route(`${API}/jobs/${JOB_ID}`, route => route.fulfill(route.request().method() === 'OPTIONS'
      ? {status:204, headers}
      : {headers, json:{job_id:JOB_ID, status:'error', source_type:'document', stage:'error', percent:0,
        error_code:'LS-BILL-10', error:'This document needs 310 minutes; 5 minutes remain.'}}));
  }
  const prefix = locale === 'tr' ? '' : `/${locale}`;
  await page.goto(`${prefix}/workspace.html${failed ? `?job=${JOB_ID}` : ''}`);
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#documentQuotaHelp')).toBeVisible();
  return writes;
}

const freeAccount = {
  plan:{code:'free', entitlements:{limits:{max_minutes_per_job:30}, download_enabled:false}},
  remaining_minutes:60,
};

for (const scenario of [
  {name:'anonymous trial', account:null, minutes:5, words:'1,000'},
  {name:'free account limited per job', account:freeAccount, minutes:30, words:'6,000'},
  {name:'remaining balance below job limit', account:{...freeAccount, remaining_minutes:7}, minutes:7, words:'1,400'},
  {name:'extra-credit effective entitlement', account:{...freeAccount, remaining_minutes:145,
    download_enabled:true, job_entitlements:{limits:{max_minutes_per_job:180}, download_enabled:true}}, minutes:145, words:'29,000'},
]) {
  test(`document allowance explains ${scenario.name} before any upload`, async ({page}) => {
    const writes = await openWorkspace(page, {account:scenario.account});
    await expect(page.locator('#documentQuotaAllowance')).toHaveText(`Your allowance for this job is ${scenario.minutes} minutes: about ${scenario.words} words.`);
    await expect(page.locator('#documentQuotaHelp details')).not.toHaveAttribute('open', '');
    await page.locator('#classicFiles').setInputFiles({name:'chapter.pdf', mimeType:'application/pdf', buffer:Buffer.from('Synthetic selection only; no upload.')});
    await expect(page.locator('#documentQuotaAllowance')).toContainText(scenario.words);
    expect(writes).toEqual([]);
    if (scenario.account?.download_enabled) await expect(page.locator('#documentQuotaHelp')).not.toContainText('file downloads are not included');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  });
}

test('document estimates use the server conversion rate and retain other limits', async ({page}) => {
  await openWorkspace(page, {account:freeAccount, rate:400});
  await expect(page.locator('#documentQuotaAllowance')).toContainText('12,000 words');
  await page.locator('#documentQuotaHelp summary').click();
  await expect(page.locator('#documentQuotaHelp')).toContainText('About 400 words');
  await expect(page.locator('#documentQuotaHelp')).toContainText('page, scanned-page and file-size limits');
  await expect(page.locator('#documentQuotaHelp')).toContainText('file downloads are not included');
  await page.locator('#classicFiles').setInputFiles({name:'audio.mp3', mimeType:'audio/mpeg', buffer:Buffer.from('Synthetic selection only.')});
  await expect(page.locator('#documentQuotaHelp')).toBeHidden();
});

test('missing conversion metadata does not invent a document word allowance', async ({page}) => {
  await openWorkspace(page, {account:freeAccount, rate:null});
  await expect(page.locator('#documentQuotaAllowance')).toHaveText('Your allowance for this job is 30 minutes.');
  await expect(page.locator('#documentQuotaHelp')).not.toContainText('words');
});

test('missing remaining balance does not advertise the whole job limit as available', async ({page}) => {
  await openWorkspace(page, {account:{plan:freeAccount.plan}});
  await expect(page.locator('#documentQuotaAllowance')).toHaveText('Document limits depend on your remaining allowance and the limit per job.');
});

for (const guest of [false, true]) {
  test(`quota rejection offers a shorter source and ${guest ? 'localized registration' : 'plans'} without resubmission`, async ({page}) => {
    const account = guest ? {plan:{code:'guest', entitlements:{limits:{max_minutes_per_job:5}}}, remaining_minutes:5} : freeAccount;
    const writes = await openWorkspace(page, {account, failed:true});
    await expect(page.locator('#errorBox')).toBeVisible();
    const link = page.locator('#quotaRecoveryActions a');
    const destination = new URL(await link.getAttribute('href'), page.url());
    expect(destination.pathname).toBe(guest ? '/en/register.html' : '/en/plans');
    if (guest) expect(destination.searchParams.get('next')).toBe('/en/workspace.html');
    await page.locator('#classicFiles').setInputFiles({name:'whole-book.pdf', mimeType:'application/pdf', buffer:Buffer.from('Synthetic selection only.')});
    const chooserReady = page.waitForEvent('filechooser');
    await page.locator('#quotaRecoveryActions button').click();
    const chooser = await chooserReady;
    await chooser.setFiles({name:'short-chapter.pdf', mimeType:'application/pdf', buffer:Buffer.from('Synthetic shorter selection only.')});
    await expect(page.locator('#errorBox')).toBeHidden();
    await expect(page.locator('#classicFileList .source-file-row')).toHaveCount(1);
    await expect(page.locator('#classicFileList')).toContainText('short-chapter.pdf');
    await expect(page.locator('#classicFileList')).not.toContainText('whole-book.pdf');
    expect(writes).toEqual([]);
  });
}

test('Arabic document guidance is localized and fits the workspace', async ({page}) => {
  await openWorkspace(page, {locale:'ar'});
  await expect(page.locator('#documentQuotaTitle')).toHaveText('قبل رفع المستند');
  await expect(page.locator('#documentQuotaAllowance')).toContainText('كلمة');
  await expect(page.locator('#documentQuotaHelp')).not.toContainText('İlk deneme');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
