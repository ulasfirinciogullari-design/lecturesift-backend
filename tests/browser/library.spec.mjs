import {test, expect, JOB_ID} from './fixtures.mjs';

test.beforeEach(async ({page}) => {
  await page.addInitScript(() => localStorage.setItem('lecturesift-currency', 'TRY'));
});

test('workspace lessons can be organized and deleted without leaving the workspace', async ({page}, testInfo) => {
  const cors = {'Access-Control-Allow-Origin':'http://127.0.0.1:4173','Access-Control-Allow-Methods':'GET,POST,PATCH,DELETE,OPTIONS','Access-Control-Allow-Headers':'authorization,content-type'};
  const state = {folders:[], jobs:[{job_id:'synthetic-library', title:'Biology revision notes', status:'done', created:1788868800, expires_at:1791460800, can_delete:true, stored_bytes:1048576, folder_id:null}]};
  const writes=[];
  await page.route('https://api.lecturesift.com/library**', async route => {
    const request=route.request(), path=new URL(request.url()).pathname, method=request.method();
    if (method==='OPTIONS') { await route.fulfill({status:204,headers:cors}); return; }
    let response={ok:true};
    if (path==='/library' && method==='GET') response={...response,...state};
    else if (path==='/library/folders' && method==='POST') {
      state.folders.push({id:'synthetic-folder',name:request.postDataJSON().name});
      response.folder=state.folders[0]; writes.push('create');
    } else if (path==='/library/folders/synthetic-folder' && method==='PATCH') {
      state.folders[0].name=request.postDataJSON().name; response.folder=state.folders[0]; writes.push('rename');
    } else if (path==='/library/folders/synthetic-folder' && method==='DELETE') {
      state.folders=[]; state.jobs.forEach(job=>job.folder_id=null); writes.push('delete-folder');
    } else if (path==='/library/lessons/synthetic-library' && method==='PATCH') {
      state.jobs[0].folder_id=request.postDataJSON().folder_id; writes.push('move');
    } else if (path==='/library/lessons/synthetic-library' && method==='DELETE') {
      state.jobs=[]; writes.push('delete-lesson');
    } else { throw new Error(`Unexpected synthetic library request: ${method} ${path}`); }
    await route.fulfill({status:200,headers:cors,contentType:'application/json',body:JSON.stringify(response)});
  });
  await page.addInitScript(()=>{
    localStorage.setItem('lecturesift-billing-token','synthetic-library-token');
    localStorage.setItem('lecturesift-language','en');
  });
  await page.goto('/workspace.html#library');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#workspaceLibraryPanel')).toBeVisible();
  await expect(page.locator('.library-lesson')).toHaveCount(1);
  await expect(page.locator('.study-sidebar a[href="/account.html"], .study-sidebar a[href="/plans.html"]')).toHaveCount(0);
  page.once('dialog',dialog=>dialog.accept('Biology'));
  await page.locator('#libraryNewFolder').click();
  await expect(page.locator('[data-library-folder="synthetic-folder"]')).toContainText('Biology');
  await page.locator('[data-library-folder="all"]').click();
  await page.locator('[data-library-move]').selectOption('synthetic-folder');
  await expect(page.locator('[data-library-move]')).toHaveValue('synthetic-folder');
  await page.reload();
  await expect(page.locator('[data-library-move]')).toHaveValue('synthetic-folder');
  await page.locator('[data-library-folder="synthetic-folder"]').click();
  page.once('dialog',dialog=>dialog.accept('Exam revision'));
  await page.locator('#libraryRename').click();
  await expect(page.locator('[data-library-folder="synthetic-folder"]')).toContainText('Exam revision');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.screenshot({path:testInfo.outputPath('workspace-library-layout.jpg'),quality:75});
  page.once('dialog',dialog=>dialog.accept());
  await page.locator('#libraryDeleteFolder').click();
  await expect(page.locator('.library-lesson')).toHaveCount(1);
  await expect(page.locator('[data-library-move]')).toHaveValue('');
  page.once('dialog',dialog=>dialog.accept());
  await page.locator('[data-library-delete]').click();
  await expect(page.locator('.library-lesson')).toHaveCount(0);
  expect(writes).toEqual(['create','move','rename','delete-folder','delete-lesson']);
  await page.locator('#workspaceStudyTab').click();
  await expect(page.locator('#classicDropZone')).toBeVisible();
  await expect(page.locator('#recordingModeTabs, #separateSources, #audioFiles, #visualFiles')).toHaveCount(0);
});


test('workspace help opens the support form and a bare conversation URL redirects there', async ({page}) => {
  await page.goto('/workspace.html');
  await page.locator('[data-consent="essential"]').click();
  await page.locator('.sidebar-support').click();
  await expect(page.locator('#contactForm')).toBeVisible();
  await expect(page.locator('#supportStatus')).toHaveCount(0);
  await page.goto('/support.html');
  await expect(page.locator('#contactForm')).toBeVisible();
});

test('opening library lessons and creating new ones preserves the current language', async ({page}) => {
  let empty = false;
  await page.route('https://api.lecturesift.com/library', async route => {
    const headers = {'Access-Control-Allow-Origin':'http://127.0.0.1:4173', 'Access-Control-Allow-Methods':'GET,OPTIONS', 'Access-Control-Allow-Headers':'authorization,content-type'};
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({status:204, headers});
      return;
    }
    await route.fulfill({status:200, headers, json:{folders:[], jobs:empty ? [] : [{job_id:JOB_ID, title:'Synthetic revision notes', status:'done', created:1788868800, expires_at:1791460800, can_delete:true, stored_bytes:1024, folder_id:null}]}});
  });
  await page.addInitScript(() => {
    localStorage.setItem('lecturesift-billing-token', 'synthetic-library-owner');
    // The current URL language must win even if an older saved preference differs.
    localStorage.setItem('lecturesift-ui', 'tr');
  });
  for (const language of ['en', 'ar']) {
    empty = false;
    await page.goto(`/${language}/workspace.html#library`);
    if (language === 'en') await page.locator('[data-consent="essential"]').click();
    await expect(page.locator('.library-open')).toHaveAttribute('href', `/${language}/workspace.html?job=${JOB_ID}#study`);
    await page.locator('.library-open').click();
    await expect(page).toHaveURL(`/${language}/workspace.html?job=${JOB_ID}#study`);
    await expect(page.locator('html')).toHaveAttribute('lang', language);
    await expect(page.locator('#results')).toBeVisible();

    empty = true;
    await page.locator('#workspaceLibraryTab').click();
    const create = page.locator('.library-empty a');
    await expect(create).toHaveAttribute('href', `/${language}/workspace.html?source=upload`);
    await create.click();
    await expect(page).toHaveURL(`/${language}/workspace.html?source=upload`);
    await expect(page.locator('html')).toHaveAttribute('lang', language);
    await expect(page.locator('#classicDropZone')).toBeVisible();
  }
});

test('confirmed library changes remain visible when the follow-up list read fails', async ({page}) => {
  const headers = {'Access-Control-Allow-Origin':'http://127.0.0.1:4173', 'Access-Control-Allow-Methods':'GET,PATCH,DELETE,OPTIONS', 'Access-Control-Allow-Headers':'authorization,content-type'};
  const state = {folders:[{id:'revision-folder', name:'Revision'}], jobs:[{job_id:'revision-lesson', title:'Saved revision notes', status:'done', created:1788868800, expires_at:1791460800, can_delete:true, folder_id:null}]};
  const writes = [];
  let failList = false;
  await page.addInitScript(() => localStorage.setItem('lecturesift-billing-token', 'synthetic-library-owner'));
  await page.route('https://api.lecturesift.com/library**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname, method = request.method();
    if (method === 'OPTIONS') { await route.fulfill({status:204, headers}); return; }
    if (path === '/library' && method === 'GET') {
      await route.fulfill({status:failList ? 503 : 200, headers, json:failList ? {detail:{message:'Synthetic refresh failure'}} : state});
      return;
    }
    if (path === '/library/lessons/revision-lesson' && method === 'PATCH') {
      state.jobs[0].folder_id = request.postDataJSON().folder_id;
      writes.push('move');
    } else if (path === '/library/folders/revision-folder' && method === 'DELETE') {
      state.folders = [];
      state.jobs[0].folder_id = null;
      writes.push('delete-folder');
    } else if (path === '/library/lessons/revision-lesson' && method === 'DELETE') {
      state.jobs = [];
      writes.push('delete-lesson');
    } else throw new Error(`Unexpected library request ${method} ${path}`);
    failList = true;
    await route.fulfill({headers, json:{ok:true}});
  });
  await page.goto('/en/workspace.html#library');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('.library-lesson')).toHaveCount(1);
  await page.locator('[data-library-move]').selectOption('revision-folder');
  await expect(page.locator('#libraryNotice')).toContainText('Your change was saved, but the list could not refresh.');
  await expect(page.locator('[data-library-move]')).toHaveValue('revision-folder');
  await page.locator('[data-library-folder="revision-folder"]').click();
  page.once('dialog', dialog => dialog.accept());
  await page.locator('#libraryDeleteFolder').click();
  await expect(page.locator('[data-library-folder="revision-folder"]')).toHaveCount(0);
  await expect(page.locator('[data-library-move]')).toHaveValue('');
  await expect(page.locator('#libraryNotice')).toContainText('Your change was saved, but the list could not refresh.');
  page.once('dialog', dialog => dialog.accept());
  await page.locator('[data-library-delete]').click();
  await expect(page.locator('.library-lesson')).toHaveCount(0);
  await expect(page.locator('#libraryNotice')).toContainText('Your change was saved, but the list could not refresh.');
  failList = false;
  await page.locator('#libraryRefresh').click();
  await expect(page.locator('#libraryNotice')).toBeHidden();
  await expect(page.locator('.library-lesson')).toHaveCount(0);
  expect(writes).toEqual(['move', 'delete-folder', 'delete-lesson']);
});

test('a malformed library refresh keeps the last valid list searchable and recoverable', async ({page}) => {
  let malformed = null;
  const validJob = {job_id:'saved-lesson', title:'Saved revision notes', status:'done', created:1788868800, expires_at:1791460800, can_delete:true, folder_id:null};
  const headers = {'Access-Control-Allow-Origin':'http://127.0.0.1:4173', 'Access-Control-Allow-Methods':'GET,OPTIONS', 'Access-Control-Allow-Headers':'authorization,content-type'};
  await page.addInitScript(() => localStorage.setItem('lecturesift-billing-token', 'synthetic-library-owner'));
  await page.route('https://api.lecturesift.com/library', async route => {
    if (route.request().method() === 'OPTIONS') { await route.fulfill({status:204, headers}); return; }
    await route.fulfill({headers, json:malformed || {folders:[], jobs:[validJob]}});
  });
  await page.goto('/en/workspace.html#library');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('.library-lesson')).toHaveCount(1);
  for (const invalid of [{}, {folders:[], jobs:[null]}, {folders:[], jobs:[{...validJob, title:3}]}, {folders:[], jobs:[{...validJob, created:'bad'}]}, {folders:[null], jobs:[validJob]}]) {
    malformed = invalid;
    await page.locator('#libraryRefresh').click();
    await expect(page.locator('#libraryNotice')).toBeVisible();
    await page.locator('#librarySearch').fill('');
    await page.locator('#librarySearch').fill('revision');
    await expect(page.locator('.library-lesson')).toHaveCount(1);
  }
  malformed = null;
  await page.locator('#libraryRefresh').click();
  await expect(page.locator('#libraryNotice')).toBeHidden();
  await expect(page.locator('.library-lesson')).toHaveCount(1);
});
