import {test, expect, JOB_ID} from './fixtures.mjs';

for (const automatic of [false, true]) {
  test(`file uploads show separate byte progress and ${automatic ? 'automatically open' : 'wait to open'} results`, async ({page}, testInfo) => {
    await page.addInitScript(() => {
      localStorage.setItem('lecturesift-billing-token','synthetic-upload-owner');
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
      };
    });
    await page.goto('/en/workspace.html');
    await page.locator('[data-consent="essential"]').click();
    await page.locator('#classicFiles').setInputFiles([
      {name:'ders-α.mp3', mimeType:'audio/mpeg', buffer:Buffer.alloc(10240,65)},
      {name:'lesson-two.mp3', mimeType:'audio/mpeg', buffer:Buffer.alloc(20480,66)},
    ]);
    await page.locator('#autoOpenResult').setChecked(automatic);
    await page.locator('#analyzeButton').click();
    await page.waitForFunction(()=>Boolean(window.syntheticUpload));
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
    expect(await page.evaluate(()=>localStorage.getItem('lecturesift-auto-open-result'))).toBe(automatic ? null : 'false');
  });
}
