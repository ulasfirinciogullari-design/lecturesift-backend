import {test, expect} from './fixtures.mjs';

const API = 'https://api.lecturesift.com';
const headers = {'Access-Control-Allow-Origin':'http://127.0.0.1:4173', 'Access-Control-Allow-Methods':'GET,POST,OPTIONS', 'Access-Control-Allow-Headers':'authorization,content-type'};
const limits = {max_files_per_job:1, max_media_upload_mb:150, max_document_upload_mb:35,
  max_minutes_per_job:30, max_document_pages:40, max_ocr_pages:8};
const pdf = name => ({name, mimeType:'application/pdf', buffer:Buffer.from('Synthetic PDF transport fixture; no extraction.')});

async function prepare(page, {advertised = limits, actual = limits, locale = 'en'} = {}) {
  let identities = 0;
  await page.addInitScript(locale => {
    localStorage.setItem('lecturesift-ui', locale);
    localStorage.setItem('lecturesift-currency', 'TRY');
    const open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
      this.syntheticGuestUpload = method === 'POST' && url === 'https://api.lecturesift.com/jobs';
      return open.call(this, method, url, ...rest);
    };
    XMLHttpRequest.prototype.send = function(body) {
      if (!this.syntheticGuestUpload) return send.call(this, body);
      window.syntheticGuestUpload = this;
      window.syntheticGuestUploadCount = (window.syntheticGuestUploadCount || 0) + 1;
    };
  }, locale);
  await page.route(`${API}/billing/plans?currency=TRY`, route => route.fulfill({headers, json:{
    plans:[], selected_currency:'TRY', guest_trial_minutes:30, document_words_per_credit_minute:200,
    ...(advertised == null ? {} : {guest_trial_limits:advertised}),
    document_sample:{enabled:true, max_words:2000, max_pages:10, max_ocr_pages:5, supports_ocr:true, native_text_only:false, formats:['pdf']},
  }}));
  await page.route(`${API}/billing/guest-session`, route => {
    if (route.request().method() === 'OPTIONS') return route.fulfill({status:204, headers});
    identities += 1;
    return route.fulfill({headers, json:{token:'synthetic-guest-limit-token', account:{
      user:{id:'synthetic-guest', email:'guest@example.invalid'}, remaining_minutes:30,
      plan:{code:'guest', entitlements:{limits:actual, download_enabled:false}},
    }, trial:{used:false, remaining_minutes:30}}});
  });
  await page.goto(`${locale === 'tr' ? '' : `/${locale}`}/workspace.html`);
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#analyzeButton')).toHaveAttribute('data-guest-wrapped', '1');
  await expect(page.locator('#documentQuotaAllowance')).toContainText(locale === 'tr' ? 'kelime' : 'words');
  return () => identities;
}

test('quota rejection stays retryable while a used-trial API response gives a localized registration gate', async ({page}) => {
  const identities = await prepare(page, {locale:'tr'});
  await page.locator('#classicFiles').setInputFiles(pdf('retained-chapter.pdf'));
  await page.locator('#documentSample').check();
  await page.locator('#documentStartPage').fill('7');
  const reject = code => page.evaluate(code => {
    const xhr = window.syntheticGuestUpload;
    Object.defineProperty(xhr, 'status', {value:402});
    Object.defineProperty(xhr, 'responseText', {value:JSON.stringify({detail:{code, message:'Synthetic server rejection'}})});
    xhr.dispatchEvent(new ProgressEvent('load'));
  }, code);
  await page.locator('#analyzeButton').click();
  await expect.poll(() => page.evaluate(() => window.syntheticGuestUploadCount || 0)).toBe(1);
  await reject('LS-GUEST-04');
  await expect(page.locator('#errorCode')).toContainText('LS-GUEST-04');
  await expect(page.locator('#analyzeButton')).toBeEnabled();
  await expect(page.locator('#guestTrialSignup')).toHaveCount(0);
  expect(await page.evaluate(() => window.LectureSiftGuestTrial.state().used)).toBe(false);
  expect(await page.evaluate(() => window.syntheticGuestUploadCount)).toBe(1);
  await page.locator('#analyzeButton').click();
  await expect.poll(() => page.evaluate(() => window.syntheticGuestUploadCount || 0)).toBe(2);
  await reject('LS-GUEST-05');
  await expect(page.locator('#errorCode')).toContainText('LS-GUEST-05');
  await expect(page.locator('#errorMessage')).toContainText('Tek seferlik 30 dakikalık denemen kullanıldı.');
  await expect(page.locator('#errorMessage')).not.toContainText('Synthetic');
  await expect(page.locator('#analyzeButton')).toBeDisabled();
  await expect(page.locator('#guestTrialSignup')).toHaveText('Ücretsiz hesap oluştur');
  await expect(page.locator('#guestTrialSignup')).toHaveAttribute('href', '/register.html');
  await expect(page.locator('#classicFileList')).toContainText('retained-chapter.pdf');
  await expect(page.locator('#documentSample')).toBeChecked();
  await expect(page.locator('#documentStartPage')).toHaveValue('7');
  expect(await page.evaluate(() => window.LectureSiftGuestTrial.state().used)).toBe(true);
  expect(await page.evaluate(() => window.syntheticGuestUploadCount)).toBe(2);
  expect(identities()).toBe(1);
});

async function selectSizedFile(page, name, bytes) {
  // Exercise the real picker validation using a tiny synthetic File with an
  // overridden size. No 150MB buffer, upload, browser media read, or extraction.
  await page.evaluate(({name, bytes}) => {
    const file = new File(['Synthetic size-boundary fixture'], name);
    Object.defineProperty(file, 'size', {value:bytes});
    addFiles('classic', [file]);
  }, {name, bytes});
}

test('new guest media and document limits accept their boundary and reject one extra byte before identity creation', async ({page}) => {
  const identities = await prepare(page);
  await expect(page.locator('#sourceLimitHelp')).toContainText('150 MB');
  await expect(page.locator('#sourceLimitHelp')).toContainText('35 MB');
  await expect(page.locator('#documentQuotaAllowance')).toHaveText('Your allowance for this job is 30 minutes: about 6,000 words.');
  await expect(page.locator('.rollout-guest-note')).toContainText('up to 30 minutes');
  for (const [name, megabytes] of [['chapter.pdf',35], ['lecture.mp3',150]]) {
    await selectSizedFile(page, name, megabytes * 1024 ** 2);
    await expect(page.locator('#classicFileList .source-file-row')).toHaveCount(1);
    await expect(page.locator('#classicFileList')).toContainText(name);
    await page.locator('[data-action="remove"]').click();
    await selectSizedFile(page, name, megabytes * 1024 ** 2 + 1);
    await expect(page.locator('#errorCode')).toContainText('LS-UPLOAD-02');
    await expect(page.locator('#errorMessage')).toContainText(`${megabytes} MB`);
    await expect(page.locator('#classicFileList .source-file-row')).toHaveCount(0);
    await page.locator('#closeError').click();
  }
  expect(identities()).toBe(0);
  expect(await page.evaluate(() => window.syntheticGuestUploadCount || 0)).toBe(0);
});

test('advertised guest limits govern source count and sample guidance before guest bootstrap', async ({page}) => {
  const advertised = {...limits, max_media_upload_mb:3, max_document_upload_mb:2, max_minutes_per_job:12, max_ocr_pages:2};
  const identities = await prepare(page, {advertised});
  await expect(page.locator('#sourceLimitHelp')).toContainText('3 MB');
  await expect(page.locator('#sourceLimitHelp')).toContainText('2 MB');
  await expect(page.locator('#documentQuotaAllowance')).toHaveText('Your allowance for this job is 12 minutes: about 2,400 words.');
  await page.locator('#classicFiles').setInputFiles(pdf('first.pdf'));
  await page.locator('#documentSample').check();
  await expect(page.locator('#documentSampleHelp')).toContainText('up to 2 scanned pages');
  await expect(page.locator('#documentSampleHelp')).toContainText('10 pages and 2,000 words');
  await page.locator('#classicFiles').setInputFiles(pdf('second.pdf'));
  await expect(page.locator('#errorCode')).toContainText('LS-UPLOAD-04');
  await expect(page.locator('#classicFileList .source-file-row')).toHaveCount(1);
  await expect(page.locator('#classicFileList')).toContainText('first.pdf');
  await expect(page.locator('#documentSample')).toBeChecked();
  expect(identities()).toBe(0);
  expect(await page.evaluate(() => window.syntheticGuestUploadCount || 0)).toBe(0);
});

test('an older guest catalog keeps the one-source fallback and unchanged PDF sample cap', async ({page}) => {
  const identities = await prepare(page, {advertised:null});
  await expect(page.locator('#sourceLimitHelp')).toContainText('150 MB');
  await expect(page.locator('#sourceLimitHelp')).toContainText('35 MB');
  await page.locator('#classicFiles').setInputFiles(pdf('first.pdf'));
  await page.locator('#documentSample').check();
  await expect(page.locator('#documentSampleHelp')).toContainText('10 pages and 2,000 words');
  await expect(page.locator('#documentSampleHelp')).toContainText('up to 5 scanned pages');
  await page.locator('#classicFiles').setInputFiles(pdf('second.pdf'));
  await expect(page.locator('#errorCode')).toContainText('LS-UPLOAD-04');
  await expect(page.locator('#classicFileList .source-file-row')).toHaveCount(1);
  expect(identities()).toBe(0);
});

for (const stricter of ['file count', 'file size']) {
  test(`stricter ${stricter} after guest bootstrap rejects before upload and permits an explicit valid retry`, async ({page}) => {
    const identities = await prepare(page, {advertised:{...limits, max_files_per_job:2},
      actual:{...limits, max_document_upload_mb:2}});
    if (stricter === 'file count') await page.locator('#classicFiles').setInputFiles([pdf('first.pdf'), pdf('second.pdf')]);
    else await selectSizedFile(page, 'too-large.pdf', 3 * 1024 ** 2);
    await page.locator('#analyzeButton').click();
    await expect(page.locator('#errorCode')).toContainText(stricter === 'file count' ? 'LS-UPLOAD-04' : 'LS-UPLOAD-02');
    await expect(page.locator('#analyzeButton')).toBeEnabled();
    expect(await page.evaluate(() => window.LectureSiftGuestTrial.state().used)).toBe(false);
    expect(await page.evaluate(() => window.syntheticGuestUploadCount || 0)).toBe(0);
    expect(identities()).toBe(1);
    await page.locator('[data-action="remove"]').last().click();
    if (stricter === 'file size') await page.locator('#classicFiles').setInputFiles(pdf('smaller.pdf'));
    await page.locator('#analyzeButton').click();
    await expect.poll(() => page.evaluate(() => window.syntheticGuestUploadCount || 0)).toBe(1);
    expect(identities()).toBe(1);
  });
}
