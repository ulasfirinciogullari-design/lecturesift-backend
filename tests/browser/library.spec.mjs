import {test, expect} from './fixtures.mjs';

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
