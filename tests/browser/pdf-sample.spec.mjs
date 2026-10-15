import {test, expect, JOB_ID} from './fixtures.mjs';

const API = 'https://api.lecturesift.com';
const cors = {'Access-Control-Allow-Origin':'http://127.0.0.1:4173', 'Access-Control-Allow-Methods':'GET,OPTIONS', 'Access-Control-Allow-Headers':'authorization,content-type'};
const pdf = name => ({name, mimeType:'application/pdf', buffer:Buffer.from('%PDF-1.7\nSynthetic browser transport fixture; no PDF extraction is run here.')});
const sample = {start_page:7, end_page:11, original_pages:100, processed_pages:5, word_limit:400, truncated:true};
const sampleMetadata = {partial_source:true, document_sample:sample};
const result = {title:'Synthetic short sample', job_type:'study_pack', summary:'Only the selected section.',
  options:{include_summary:true, include_transcript:false, include_slides:false, quiz_count:0, flashcard_count:0},
  quiz:[], artifacts:[], flashcards:[], slides:[], transcript_segments:[], download_enabled:false, ...sampleMetadata};

async function prepare(page, {remaining = 2.7, locale = 'en', job = null, jobResult = result, capability = true, registered = true} = {}) {
  await page.addInitScript(({locale,registered}) => {
    if (location.origin !== 'http://127.0.0.1:4173') return;
    if (registered) localStorage.setItem('lecturesift-billing-token', 'synthetic-pdf-owner');
    localStorage.setItem('lecturesift-ui', locale);
    localStorage.setItem('lecturesift-currency', 'TRY');
    window.studyEvents = [];
    // Observe app integration separately from the analytics consent/privacy
    // contract, which is covered by the analytics module's own tests.
    const recorder = {trackStudy:async (action, parameters) => {
      window.studyEvents.push({action, parameters}); return true;
    }};
    Object.defineProperty(window, 'LectureSiftAnalytics', {configurable:true, get:()=>recorder, set:()=>{}});
    const open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send, header = XMLHttpRequest.prototype.setRequestHeader;
    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
      this.syntheticPdfUpload = method === 'POST' && url === 'https://api.lecturesift.com/jobs';
      return open.call(this, method, url, ...rest);
    };
    XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
      if (this.syntheticPdfUpload && name.toLowerCase() === 'content-type') this.syntheticType = value;
      return header.call(this, name, value);
    };
    XMLHttpRequest.prototype.send = function(body) {
      if (!this.syntheticPdfUpload) return send.call(this, body);
      window.syntheticPdfUpload = {xhr:this, body, type:this.syntheticType};
      window.syntheticPdfUploadCount = (window.syntheticPdfUploadCount || 0) + 1;
    };
  }, {locale,registered});
  await page.route(`${API}/billing/plans?currency=TRY`, route => route.fulfill({headers:cors, json:{
    plans:[], selected_currency:'TRY', guest_trial_minutes:5, document_words_per_credit_minute:200,
    ...(capability == null ? {} : {document_sample:{enabled:capability, max_words:1000, max_pages:5, native_text_only:true, formats:['pdf']}}),
  }}));
  await page.route(`${API}/billing/me`, route => route.fulfill(route.request().method() === 'OPTIONS'
    ? {status:204, headers:cors} : {headers:cors, json:{account:{
      user:{id:'synthetic-pdf-user', email:'pdf@example.invalid'}, remaining_minutes:remaining,
      plan:{code:'free', entitlements:{limits:{max_minutes_per_job:30, max_files_per_job:3, max_document_upload_mb:25, max_media_upload_mb:100}, download_enabled:false}},
    }}}));
  await page.route(`${API}/jobs/${JOB_ID}`, route => route.fulfill(route.request().method() === 'OPTIONS'
    ? {status:204, headers:cors} : {headers:cors, json:job || {job_id:JOB_ID, status:'done', stage:'done', percent:100,
      source_type:'document', options:{job_type:'study_pack'}, ...sampleMetadata}}));
  await page.route(`${API}/jobs/${JOB_ID}/result`, route => route.fulfill(route.request().method() === 'OPTIONS'
    ? {status:204, headers:cors} : {headers:cors, json:jobResult}));
}

async function openWorkspace(page, path = '/en/workspace.html') {
  await page.goto(path);
  const consent = page.locator('[data-consent="essential"]');
  if (await consent.isVisible()) await consent.click();
  await expect(page.locator('#documentQuotaHelp')).toBeVisible();
}

async function multipart(page) {
  return page.evaluate(async () => {
    const {body,type} = window.syntheticPdfUpload;
    const parsed = await new Response(body, {headers:{'Content-Type':type}}).formData();
    return {sample:parsed.get('document_sample'), start:parsed.get('document_start_page'),
      names:parsed.getAll('files').map(file => file.name), source:await parsed.getAll('files')[0].text(),
      jobType:parsed.get('job_type')};
  });
}

async function finishUpload(page, body, status = 202) {
  await page.evaluate(({body,status}) => {
    const {xhr} = window.syntheticPdfUpload;
    xhr.upload.dispatchEvent(new ProgressEvent('load'));
    Object.defineProperty(xhr, 'status', {value:status});
    Object.defineProperty(xhr, 'responseText', {value:JSON.stringify(body)});
    xhr.dispatchEvent(new ProgressEvent('load'));
  }, {body,status});
}

test('full PDF processing stays the default and the original multipart source is unchanged', async ({page}) => {
  await prepare(page);
  await openWorkspace(page);
  await expect(page.locator('#documentSampleOption')).toBeHidden();
  const file = pdf('full-source.pdf');
  await page.locator('#classicFiles').setInputFiles(file);
  await expect(page.locator('#documentSampleOption')).toBeVisible();
  await expect(page.locator('#documentSample')).not.toBeChecked();
  await expect(page.locator('#documentSampleSettings')).toBeHidden();
  await page.locator('#analyzeButton').click();
  await page.waitForFunction(() => Boolean(window.syntheticPdfUpload));
  expect(await multipart(page)).toEqual({sample:null, start:null, names:[file.name], source:file.buffer.toString(), jobType:'study_pack'});
  expect(await page.evaluate(() => window.studyEvents)).toEqual([{action:'upload_started', parameters:{sample_mode:false, source_type:'document'}}]);
});

for (const capability of [null, false]) {
  test(`an older or disabled API cannot enable PDF samples (capability=${capability})`, async ({page}) => {
    await prepare(page, {capability});
    await openWorkspace(page);
    await page.locator('#classicFiles').setInputFiles(pdf('chapter.pdf'));
    await expect(page.locator('#documentSampleOption')).toBeHidden();
    await expect(page.locator('#documentSample')).not.toBeChecked();
    await page.locator('#analyzeButton').click();
    await page.waitForFunction(() => Boolean(window.syntheticPdfUpload));
    const body = await multipart(page);
    expect(body.sample).toBeNull(); expect(body.start).toBeNull();
  });
}

for (const interruption of ['catalog disabled', 'source changed']) {
  test(`a requested sample never becomes a full upload while guest access waits: ${interruption}`, async ({page}) => {
    await prepare(page, {registered:false});
    let guestRequested = false, releaseGuest;
    const guestReady = new Promise(resolve => { releaseGuest = resolve; });
    const guestHeaders = {...cors, 'Access-Control-Allow-Methods':'GET,POST,OPTIONS'};
    await page.route(`${API}/billing/guest-session`, async route => {
      if (route.request().method() === 'OPTIONS') return route.fulfill({status:204, headers:guestHeaders});
      guestRequested = true;
      await guestReady;
      await route.fulfill({headers:guestHeaders, json:{token:'synthetic-guest-token', account:{
        user:{id:'synthetic-guest', email:'guest@example.invalid'}, remaining_minutes:5,
        plan:{code:'guest', entitlements:{limits:{max_minutes_per_job:5, max_files_per_job:3, max_document_upload_mb:25, max_media_upload_mb:100}}},
      }, trial:{used:false, remaining_minutes:5}}});
    });
    await openWorkspace(page);
    await expect(page.locator('#analyzeButton')).toHaveAttribute('data-guest-wrapped', '1');
    await page.locator('#classicFiles').setInputFiles(pdf('chapter.pdf'));
    await page.locator('#documentSample').check();
    await page.locator('#analyzeButton').click();
    await expect.poll(() => guestRequested).toBe(true);
    if (interruption === 'catalog disabled') {
      await page.route(`${API}/billing/plans?currency=TRY`, route => route.fulfill({headers:cors, json:{
        plans:[], selected_currency:'TRY', guest_trial_minutes:5, document_words_per_credit_minute:200,
        document_sample:{enabled:false},
      }}));
      // Refresh the actual catalog while the identity response is pending.
      await page.evaluate(() => loadBilling());
    } else await page.locator('#classicFiles').setInputFiles(pdf('second.pdf'));
    await expect(page.locator('#documentSampleOption')).toBeHidden();
    releaseGuest();
    await expect(page.locator('#errorCode')).toContainText('LS-DOC-17');
    expect(await page.evaluate(() => window.syntheticPdfUploadCount || 0)).toBe(0);
    expect(await page.evaluate(() => window.studyEvents)).toEqual([]);
  });
}

test('an opted-in PDF sample sends its start page and explains whole-minute quota without automatic retry', async ({page}, testInfo) => {
  await prepare(page);
  await openWorkspace(page);
  await page.locator('#classicFiles').setInputFiles(pdf('sample-source.pdf'));
  await page.locator('#documentSample').check();
  await page.locator('#documentStartPage').fill('7');
  await expect(page.locator('#documentSampleAllowance')).toHaveText('This sample can process up to 400 words.');
  await expect(page.locator('#documentSampleHelp')).toContainText('does not use OCR');
  if (testInfo.project.name.endsWith('light')) await page.locator('#documentSampleOption').screenshot({path:testInfo.outputPath('pdf-sample-controls-layout.jpg'), type:'jpeg', quality:75});
  await page.locator('#analyzeButton').click();
  await page.waitForFunction(() => Boolean(window.syntheticPdfUpload));
  const body = await multipart(page);
  expect(body.sample).toBe('true'); expect(body.start).toBe('7'); expect(body.names).toEqual(['sample-source.pdf']);
  await expect(page.locator('#documentSample')).toBeDisabled();
  await expect(page.locator('#documentStartPage')).toBeDisabled();
  await finishUpload(page, {detail:{code:'LS-DOC-14', message:'Synthetic scanned PDF rejection'}}, 422);
  await expect(page.locator('#errorCode')).toContainText('LS-DOC-14');
  await expect(page.locator('#errorMessage')).not.toContainText('Synthetic');
  await expect(page.locator('#documentSample')).toBeEnabled();
  await expect(page.locator('#documentSample')).toBeChecked();
  await expect(page.locator('#results')).toBeHidden();
  expect(await page.evaluate(() => window.syntheticPdfUploadCount)).toBe(1);
  expect(await page.evaluate(() => window.studyEvents)).toEqual([
    {action:'upload_started', parameters:{sample_mode:true, source_type:'document'}},
    {action:'job_failed', parameters:{sample_mode:true, source_type:'document', error_code:'LS-DOC-14'}},
  ]);
});

test('changing sources clears sample choices and only a single PDF exposes them', async ({page}) => {
  await prepare(page);
  await openWorkspace(page);
  await page.locator('#classicFiles').setInputFiles(pdf('first.pdf'));
  await page.locator('#documentSample').check();
  await page.locator('#documentStartPage').fill('4');
  await page.locator('#classicFiles').setInputFiles(pdf('second.pdf'));
  await expect(page.locator('#documentSampleOption')).toBeHidden();
  await expect(page.locator('#documentSample')).not.toBeChecked();
  await page.locator('[data-action="remove"]').last().click();
  await expect(page.locator('#documentSampleOption')).toBeVisible();
  await expect(page.locator('#documentSample')).not.toBeChecked();
  await expect(page.locator('#documentStartPage')).toHaveValue('1');
  await page.locator('[data-action="remove"]').click();
  for (const file of [{name:'notes.docx', mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}, {name:'lecture.mp3', mimeType:'audio/mpeg'}]) {
    await page.locator('#classicFiles').setInputFiles({...file, buffer:Buffer.from('Synthetic selection only.')});
    await expect(page.locator('#documentSampleOption')).toBeHidden();
    await page.locator('[data-action="remove"]').click();
  }
  expect(await page.evaluate(() => window.syntheticPdfUploadCount || 0)).toBe(0);
});

test('an invalid sample start page is rejected before any upload', async ({page}) => {
  await prepare(page);
  await openWorkspace(page);
  await page.locator('#classicFiles').setInputFiles(pdf('chapter.pdf'));
  await page.locator('#documentSample').check();
  for (const invalid of ['0','-1','1.5']) {
    await page.locator('#documentStartPage').fill(invalid);
    await page.locator('#analyzeButton').click();
    await expect(page.locator('#errorCode')).toContainText('LS-DOC-15');
    await expect(page.locator('#analyzeButton')).toBeEnabled();
    await page.locator('#closeError').click();
  }
  expect(await page.evaluate(() => window.syntheticPdfUploadCount || 0)).toBe(0);
  expect(await page.evaluate(() => window.studyEvents)).toEqual([]);
});

test('sample results and saved lessons clearly disclose scope, with no invented full-document requirement', async ({page}, testInfo) => {
  await prepare(page);
  await page.route(`${API}/library`, route => route.fulfill(route.request().method() === 'OPTIONS'
    ? {status:204, headers:cors} : {headers:cors, json:{folders:[], jobs:[
      {job_id:JOB_ID, title:'Sample chapter', status:'done', created:1792100000, expires_at:1794700000, can_delete:true, ...sampleMetadata},
      {job_id:'synthetic-full-pdf', title:'Full source', status:'done', created:1792100000, expires_at:1794700000, can_delete:true},
    ]}}));
  await openWorkspace(page);
  await page.locator('#classicFiles').setInputFiles(pdf('chapter.pdf'));
  await page.locator('#documentSample').check();
  await page.locator('#documentStartPage').fill('7');
  await page.locator('#autoOpenResult').uncheck();
  await page.locator('#analyzeButton').click();
  await page.waitForFunction(() => Boolean(window.syntheticPdfUpload));
  await finishUpload(page, {job_id:JOB_ID});
  await expect(page.locator('#openReadyResult')).toBeVisible();
  await expect(page.locator('#results')).toBeHidden();
  expect(await page.evaluate(() => window.studyEvents.filter(event => event.action === 'result_ready'))).toEqual([]);
  await page.locator('#openReadyResult').click();
  await expect(page.locator('#documentSampleResult')).toBeVisible();
  await expect(page.locator('#documentSampleResultRange')).toContainText('7–11');
  await expect(page.locator('#documentSampleResultRange')).toContainText('100');
  await expect(page.locator('#documentSampleResultRange')).toContainText('400');
  if (testInfo.project.name.endsWith('light')) await page.locator('#documentSampleResult').screenshot({path:testInfo.outputPath('pdf-sample-result-layout.jpg'), type:'jpeg', quality:75});
  const plans = new URL(await page.locator('#documentSamplePlans').getAttribute('href'), page.url());
  expect(plans.pathname).toBe('/en/plans'); expect(plans.search).toBe('');
  const full = new URL(await page.locator('#documentSampleFull').getAttribute('href'), page.url());
  expect(full.pathname).toBe('/en/workspace.html'); expect([...full.searchParams.entries()]).toEqual([['source','upload']]);
  await page.locator('#openReadyResult').click();
  await expect.poll(() => page.evaluate(() => window.studyEvents.filter(event => event.action === 'result_ready').length)).toBe(1);
  const events = await page.evaluate(() => window.studyEvents);
  expect(events.map(event => event.action)).toEqual(['upload_started','upload_accepted','result_ready']);
  for (const event of events) expect(event.parameters).toEqual({sample_mode:true, source_type:'document'});
  await page.locator('#workspaceLibraryTab').click();
  await expect(page.locator('.document-sample-badge')).toHaveCount(1);
  await expect(page.locator('.library-lesson').filter({hasText:'Full source'}).locator('.document-sample-badge')).toHaveCount(0);
  await page.locator('.library-lesson').filter({hasText:'Sample chapter'}).locator('.library-open').click();
  await expect(page.locator('#documentSampleResult')).toBeVisible();
  expect(await page.evaluate(() => window.studyEvents.filter(event => event.action === 'result_ready'))).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

for (const partial of [false, true]) {
  test(`quota recovery sends only measured full-document requirements when sample=${partial}`, async ({page}) => {
    await prepare(page, {job:{job_id:JOB_ID, status:'error', stage:'error', percent:0, source_type:'document',
      options:{job_type:'study_pack'}, partial_source:partial, error_code:'LS-BILL-10', error:'Synthetic quota rejection',
      billable_minutes:310, document_pages:90, document_ocr_pages:0, file_size_bytes:1048576, source_file_count:1}});
    await openWorkspace(page, `/en/workspace.html?job=${JOB_ID}`);
    await expect(page.locator('#quotaRecoveryActions a')).toBeVisible();
    const url = new URL(await page.locator('#quotaRecoveryActions a').getAttribute('href'), page.url());
    expect(url.pathname).toBe('/en/plans');
    expect(Object.fromEntries(url.searchParams)).toEqual(partial ? {} : {need_minutes:'310', need_pages:'90', need_ocr:'0', need_bytes:'1048576', need_files:'1'});
    expect(await page.evaluate(() => window.syntheticPdfUploadCount || 0)).toBe(0);
  });
}

test('Arabic sample controls remain readable without horizontal overflow', async ({page}) => {
  await prepare(page, {locale:'ar'});
  await openWorkspace(page, '/ar/workspace.html');
  await page.locator('#classicFiles').setInputFiles(pdf('chapter.pdf'));
  await page.locator('#documentSample').check();
  await expect(page.locator('#documentSampleLabel')).toHaveText('إنشاء عينة قصيرة');
  await expect(page.locator('#documentSampleAllowance')).toContainText('كلمة');
  await expect(page.locator('#documentSampleOption')).not.toContainText('Kısa örnek');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
