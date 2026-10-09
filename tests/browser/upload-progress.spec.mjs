import {test, expect, JOB_ID} from './fixtures.mjs';

async function syntheticUploadTransport(page, {registered = true} = {}) {
  await page.addInitScript(({registered}) => {
    if (registered) localStorage.setItem('lecturesift-billing-token','synthetic-upload-owner');
    localStorage.setItem('lecturesift-ui','en');
    localStorage.setItem('lecturesift-currency','TRY');
    const open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send, header = XMLHttpRequest.prototype.setRequestHeader;
    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
      this.syntheticUpload = method === 'POST' && url === 'https://api.lecturesift.com/jobs';
      return open.call(this,method,url,...rest);
    };
    XMLHttpRequest.prototype.setRequestHeader = function(name,value) {
      if (this.syntheticUpload && name.toLowerCase() === 'content-type') this.syntheticType = value;
      return header.call(this,name,value);
    };
    XMLHttpRequest.prototype.send = function(body) {
      if (!this.syntheticUpload) return send.call(this,body);
      // Synthetic transport events exercise the actual UI without an external
      // upload. The multipart body is independently parsed below.
      window.syntheticUpload = {xhr:this, body, type:this.syntheticType};
      window.syntheticUploadCount = (window.syntheticUploadCount || 0) + 1;
    };
  }, {registered});
}

for (const automatic of [false, true]) {
  test(`file uploads show separate byte progress and ${automatic ? 'automatically open' : 'wait to open'} results`, async ({page}, testInfo) => {
    await syntheticUploadTransport(page);
    await page.goto('/en/workspace.html');
    await page.locator('[data-consent="essential"]').click();
    await page.locator('#classicFiles').setInputFiles([
      {name:'ders-α.mp3', mimeType:'audio/mpeg', buffer:Buffer.alloc(10240,65)},
      {name:'lesson-two.mp3', mimeType:'audio/mpeg', buffer:Buffer.alloc(20480,66)},
    ]);
    await page.locator('#autoOpenResult').setChecked(automatic);
    await page.locator('#analyzeButton').click();
    await page.waitForFunction(()=>Boolean(window.syntheticUpload));
    await expect(page.locator('#classicFiles')).toBeDisabled();
    await expect(page.locator('[data-action="remove"]').first()).toBeDisabled();
    await page.locator('#classicDropZone').evaluate(zone => {
      const files = new DataTransfer();
      files.items.add(new File(['not an allowed source'], 'invalid.exe'));
      zone.dispatchEvent(new DragEvent('drop', {bubbles:true, cancelable:true, dataTransfer:files}));
    });
    await expect(page.locator('#errorBox')).toBeHidden();
    await expect(page.locator('#analyzeButton')).toBeDisabled();
    const contract = await page.evaluate(async () => {
      const {body,type,xhr} = window.syntheticUpload;
      const parsed = await new Response(body,{headers:{'Content-Type':type}}).formData();
      const content = await body.text();
      const secondStart = new TextEncoder().encode(content.slice(0,content.indexOf('B'.repeat(1024)))).length;
      xhr.upload.dispatchEvent(new ProgressEvent('progress',{lengthComputable:true,loaded:secondStart+10240,total:body.size}));
      return {names:parsed.getAll('files').map(file=>file.name), sizes:parsed.getAll('files').map(file=>file.size),
        summary:parsed.get('include_summary'), layout:parsed.get('source_layout'),
        firstText:await parsed.getAll('files')[0].text(),secondText:await parsed.getAll('files')[1].text()};
    });
    expect(contract.names).toEqual(['ders-α.mp3','lesson-two.mp3']);
    expect(contract.sizes).toEqual([10240,20480]);
    expect(contract.firstText).toBe('A'.repeat(10240));
    expect(contract.secondText).toBe('B'.repeat(20480));
    expect(contract.summary).toBe('true');
    expect(contract.layout).toBe('classic');
    await expect(page.locator('.upload-progress-file b')).toHaveText(['100%','50%']);
    await expect(page.locator('#progressPercent')).toHaveCount(0);
    await page.locator('.process-panel').scrollIntoViewIfNeeded();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth+1)).toBe(true);
    if (!automatic) await page.screenshot({path:testInfo.outputPath('file-upload-progress-layout.jpg'),quality:75});
    await page.evaluate(jobId => {
      const {xhr}=window.syntheticUpload;
      xhr.upload.dispatchEvent(new ProgressEvent('load'));
      Object.defineProperty(xhr,'status',{value:202});
      Object.defineProperty(xhr,'responseText',{value:JSON.stringify({job_id:jobId})});
      xhr.dispatchEvent(new ProgressEvent('load'));
    }, JOB_ID);
    await expect(page.locator('#openReadyResult')).toBeVisible();
    await expect(page.locator('.upload-progress-file b')).toHaveText(['100%','100%']);
    if (!automatic) {
      await expect(page.locator('#results')).toBeHidden();
      await page.locator('#openReadyResult').click();
    }
    await expect(page.locator('#results')).toBeVisible();
    await expect(page.locator('#resultHeading')).toHaveText('Synthetic browser quiz');
    await expect(page.locator('#classicFiles')).toBeEnabled();
    expect(await page.evaluate(()=>localStorage.getItem('lecturesift-auto-open-result'))).toBe(automatic ? null : 'false');
  });
}

test('malformed accepted upload replies show an error and leave the workspace usable', async ({page, isolatedNetwork}) => {
  await syntheticUploadTransport(page);
  await page.goto('/en/workspace.html');
  await page.locator('[data-consent="essential"]').click();
  await page.locator('#classicFiles').setInputFiles({name:'lesson.txt', mimeType:'text/plain', buffer:Buffer.from('Synthetic course notes.')});
  for (const body of ['<html>Unexpected gateway response</html>', '{}', 'null', '{"job_id":"../../plans"}']) {
    await page.evaluate(() => { window.syntheticUpload = null; });
    await page.locator('#analyzeButton').click();
    await page.waitForFunction(() => Boolean(window.syntheticUpload));
    await page.evaluate(responseText => {
      const {xhr} = window.syntheticUpload;
      xhr.upload.dispatchEvent(new ProgressEvent('load'));
      Object.defineProperty(xhr, 'status', {value:202});
      Object.defineProperty(xhr, 'responseText', {value:responseText});
      xhr.dispatchEvent(new ProgressEvent('load'));
    }, body);
    await expect(page.locator('#errorBox')).toBeVisible();
    await expect(page.locator('#errorCode')).toContainText('LS-NETWORK-01');
    await expect(page.locator('#analyzeButton')).toBeEnabled();
    await expect(page.locator('#results')).toBeHidden();
  }
  expect(isolatedNetwork.apiCalls.filter(path => path.startsWith('/jobs/'))).toEqual([]);
});

test('restoring a processing lesson keeps a second upload disabled until results are ready', async ({page}) => {
  await syntheticUploadTransport(page);
  let complete = false;
  await page.route(`https://api.lecturesift.com/jobs/${JOB_ID}`, async route => {
    const headers = {'Access-Control-Allow-Origin':'http://127.0.0.1:4173', 'Access-Control-Allow-Methods':'GET,OPTIONS', 'Access-Control-Allow-Headers':'authorization,content-type'};
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({status:204, headers});
      return;
    }
    await route.fulfill({status:200, headers, json:{job_id:JOB_ID, status:complete ? 'done' : 'queued', stage:complete ? 'done' : 'queued_worker', percent:complete ? 100 : 0, source_type:'document', options:{job_type:'study_pack'}}});
  });
  await page.goto(`/en/workspace.html?job=${JOB_ID}`);
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#currentStage')).toHaveText('Waiting in the processing queue');
  await expect(page.locator('#analyzeButton')).toBeDisabled();
  await expect(page.locator('#classicFiles')).toBeDisabled();
  await page.locator('#classicDropZone').evaluate(zone => {
    const files = new DataTransfer();
    files.items.add(new File(['new source'], 'later.txt'));
    zone.dispatchEvent(new DragEvent('drop', {bubbles:true, cancelable:true, dataTransfer:files}));
  });
  await expect(page.locator('#classicFileList')).toBeEmpty();
  await expect(page.locator('#currentStage')).toHaveText('Waiting in the processing queue');
  expect(await page.evaluate(() => Boolean(window.syntheticUpload))).toBe(false);
  complete = true;
  await expect(page.locator('#results')).toBeVisible();
  await expect(page.locator('#analyzeButton')).toBeEnabled();
});

for (const failure of ['server', 'network', 'malformed']) {
  test(`a ${failure} account-read failure preserves workspace access for reload recovery`, async ({page}) => {
    await syntheticUploadTransport(page);
    let failAccount = true;
    const headers = {'Access-Control-Allow-Origin':'http://127.0.0.1:4173', 'Access-Control-Allow-Methods':'GET,OPTIONS', 'Access-Control-Allow-Headers':'authorization,content-type'};
    await page.route('https://api.lecturesift.com/billing/me', async route => {
      if (route.request().method() === 'OPTIONS') { await route.fulfill({status:204, headers}); return; }
      if (!failAccount) { await route.fallback(); return; }
      if (failure === 'network') { await route.abort('failed'); return; }
      await route.fulfill({status:failure === 'server' ? 503 : 200, headers, json:failure === 'server' ? {detail:{message:'Temporary account read failure'}} : {account:{}}});
    });
    await page.goto(`/en/workspace.html?job=${JOB_ID}`);
    await page.locator('[data-consent="essential"]').click();
    await expect(page.locator('#errorBox')).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem('lecturesift-billing-token'))).toBe('synthetic-upload-owner');
    await expect(page.locator('#results')).toBeHidden();
    failAccount = false;
    await page.reload();
    await expect(page.locator('#results')).toBeVisible();
    await expect(page.locator('#resultHeading')).toHaveText('Synthetic browser quiz');
  });
}

test('an explicitly rejected workspace session is cleared', async ({page}) => {
  await syntheticUploadTransport(page);
  await page.route('https://api.lecturesift.com/billing/me', async route => {
    const headers = {'Access-Control-Allow-Origin':'http://127.0.0.1:4173', 'Access-Control-Allow-Methods':'GET,OPTIONS', 'Access-Control-Allow-Headers':'authorization,content-type'};
    await route.fulfill(route.request().method() === 'OPTIONS' ? {status:204, headers} : {status:401, headers, json:{detail:{message:'Session expired'}}});
  });
  await page.goto(`/en/workspace.html?job=${JOB_ID}`);
  await page.locator('[data-consent="essential"]').click();
  await expect.poll(() => page.evaluate(() => localStorage.getItem('lecturesift-billing-token'))).toBeNull();
  await expect(page.locator('#results')).toBeHidden();
});

test('rapid anonymous Analyze clicks share guest creation and allow retry after failure', async ({page}) => {
  await syntheticUploadTransport(page, {registered:false});
  let creations = 0, release;
  let blocked = new Promise(resolve => { release = resolve; });
  const headers = {'Access-Control-Allow-Origin':'http://127.0.0.1:4173', 'Access-Control-Allow-Methods':'POST,OPTIONS', 'Access-Control-Allow-Headers':'authorization,content-type'};
  await page.route('https://api.lecturesift.com/billing/guest-session', async route => {
    if (route.request().method() === 'OPTIONS') { await route.fulfill({status:204, headers}); return; }
    expect(route.request().method()).toBe('POST');
    creations += 1;
    const status = await blocked;
    await route.fulfill({status, headers, json:status === 200 ? {
      token:'synthetic-guest-token', account:{user:{id:'guest-fixture'}, plan:{code:'guest'}, remaining_minutes:5}, trial:{used:false, remaining_minutes:5},
    } : {detail:{message:'Synthetic guest service failure'}}});
  });
  await page.goto('/en/workspace.html');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#analyzeButton')).toHaveAttribute('data-guest-wrapped', '1');
  await page.locator('#classicFiles').setInputFiles({name:'lesson.txt', mimeType:'text/plain', buffer:Buffer.from('Synthetic notes.')});
  try {
    await page.locator('#analyzeButton').dblclick();
    await expect.poll(() => creations).toBe(1);
    release(503);
    await expect(page.locator('#errorBox')).toBeVisible();
    await expect(page.locator('#analyzeButton')).toBeEnabled();
    expect(await page.evaluate(() => window.syntheticUploadCount || 0)).toBe(0);
    blocked = new Promise(resolve => { release = resolve; });
    await page.locator('#analyzeButton').dblclick();
    await expect.poll(() => creations).toBe(2);
    release(200);
    await page.waitForFunction(() => Boolean(window.syntheticUpload));
    await expect(page.locator('#analyzeButton')).toBeDisabled();
    expect(await page.evaluate(() => window.syntheticUploadCount)).toBe(1);
    expect(await page.evaluate(() => sessionStorage.getItem('lecturesift-guest-token'))).toBe('synthetic-guest-token');
    expect(creations).toBe(2);
  } finally { release(503); }
});

test('a late answer from the previous lesson cannot appear in a newly completed lesson', async ({page}) => {
  await syntheticUploadTransport(page);
  const nextJob = 'ci-synthetic-quiz-0002';
  const headers = {'Access-Control-Allow-Origin':'http://127.0.0.1:4173', 'Access-Control-Allow-Methods':'GET,POST,OPTIONS', 'Access-Control-Allow-Headers':'authorization,content-type'};
  let releaseAnswer, asked = false;
  const answerPending = new Promise(resolve => { releaseAnswer = resolve; });
  await page.route(`https://api.lecturesift.com/jobs/${JOB_ID}/ask`, async route => {
    if (route.request().method() === 'OPTIONS') { await route.fulfill({status:204, headers}); return; }
    asked = true;
    await answerPending;
    await route.fulfill({headers, json:{answer:'Answer belonging to the old lesson', citations:[]}});
  });
  await page.route(`https://api.lecturesift.com/jobs/${nextJob}**`, async route => {
    if (route.request().method() === 'OPTIONS') { await route.fulfill({status:204, headers}); return; }
    const result = new URL(route.request().url()).pathname.endsWith('/result');
    await route.fulfill({headers, json:result ? {title:'New lesson', summary:'New course notes', options:{include_summary:true, include_transcript:false, include_slides:false}, quiz:[], flashcards:[], artifacts:[]} : {job_id:nextJob, status:'done', stage:'done', percent:100, source_type:'document'}});
  });
  await page.goto(`/en/workspace.html?job=${JOB_ID}`);
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#results')).toBeVisible();
  await page.locator('#resultTab-ask').click();
  await page.locator('#lessonQuestion').fill('What does this lesson explain?');
  try {
    await page.locator('#lessonQuestionButton').click();
    await expect.poll(() => asked).toBe(true);
    await page.locator('#classicFiles').setInputFiles({name:'new.txt', mimeType:'text/plain', buffer:Buffer.from('New lesson source')});
    await page.locator('#analyzeButton').click();
    await page.waitForFunction(() => Boolean(window.syntheticUpload));
    await page.evaluate(job => {
      const {xhr} = window.syntheticUpload;
      xhr.upload.dispatchEvent(new ProgressEvent('load'));
      Object.defineProperty(xhr, 'status', {value:202});
      Object.defineProperty(xhr, 'responseText', {value:JSON.stringify({job_id:job})});
      xhr.dispatchEvent(new ProgressEvent('load'));
    }, nextJob);
    await expect(page.locator('#resultHeading')).toHaveText('New lesson');
    await page.locator('#resultTab-ask').click();
    releaseAnswer();
    await expect(page.locator('#lessonQuestionButton')).toBeEnabled();
    await expect(page.locator('#lessonAnswer')).toBeHidden();
    await expect(page.locator('#lessonAnswer')).not.toContainText('Answer belonging to the old lesson');
  } finally { releaseAnswer(); }
});

test('a delayed workspace account rejection cannot remove a newer stored session', async ({page}) => {
  await syntheticUploadTransport(page);
  let release, started = false;
  const pending = new Promise(resolve => { release = resolve; });
  const headers = {'Access-Control-Allow-Origin':'http://127.0.0.1:4173', 'Access-Control-Allow-Methods':'GET,OPTIONS', 'Access-Control-Allow-Headers':'authorization,content-type'};
  await page.route('https://api.lecturesift.com/billing/me', async route => {
    if (route.request().method() === 'OPTIONS') { await route.fulfill({status:204, headers}); return; }
    started = true;
    await pending;
    await route.fulfill({status:401, headers, json:{detail:{message:'Old session expired'}}});
  });
  try {
    await page.goto('/en/workspace.html');
    await page.locator('[data-consent="essential"]').click();
    await expect.poll(() => started).toBe(true);
    // Hold an explicit workspace read, independently of the header's read.
    const accountRead = page.evaluate(() => refreshBillingAccount());
    await page.evaluate(() => localStorage.setItem('lecturesift-billing-token', 'synthetic-new-workspace-session'));
    release();
    await accountRead;
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    expect(await page.evaluate(() => localStorage.getItem('lecturesift-billing-token'))).toBe('synthetic-new-workspace-session');
  } finally { release(); }
});
