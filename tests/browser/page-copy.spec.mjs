import {test, expect} from './fixtures.mjs';

const CODES = ['tr', 'en', 'de', 'fr', 'es', 'it', 'pt', 'ru', 'ar', 'zh', 'ja', 'ko', 'hi'];
const LOADER = /^\/assets\/page-i18n\/loader-[a-f0-9]{12}\.js$/;
const CATALOG = /^\/assets\/page-i18n\/([a-z]+)-[a-f0-9]{12}\.js$/;
const RUNTIME_LOADER = /^\/assets\/i18n\/loader-[a-f0-9]{12}\.js$/;
const RUNTIME = /^\/assets\/i18n\/([a-z]+)-[a-f0-9]{12}\.js$/;
// thanks.html's title is translated only through the page-copy catalog.
const TITLE = 'Mesajın Alındı | LectureSift';

async function fullCatalog(request) {
  const response = await request.get('/page-i18n.js');
  expect(response.status()).toBe(200);
  const body = await response.text();
  return {body, rows: JSON.parse(body.split('window.LECTURESIFT_PAGE_COPY=')[1].trim().replace(/;$/, ''))};
}

async function fullRuntime(request) {
  const response = await request.get('/i18n.js');
  expect(response.status()).toBe(200);
  const bytes = await response.body();
  const body = bytes.toString('utf8');
  const row = key => JSON.parse(body.match(new RegExp(`^\\s*"${key.replace('.', '\\.')}":(\\[[^\\n]+\\]),?$`, 'm'))[1]);
  return {body, size: bytes.length, row};
}

test('app pages download only the page copy and runtime of the reader language', async ({page, request}) => {
  const {body: fullBody, rows} = await fullCatalog(request);
  const runtime = await fullRuntime(request);
  const label = runtime.row('language.label');
  const requested = [];
  page.on('request', entry => {
    const pathname = new URL(entry.url()).pathname;
    if (pathname.includes('i18n')) requested.push(pathname);
  });
  await page.goto('/thanks.html');
  // [URL, saved language, language i18n.js selects]; the browser locale is en-US.
  for (const [pathname, saved, language] of [
    ['/de/thanks.html', 'tr', 'de'],
    ['/thanks.html', 'tr', 'tr'],
    ['/thanks.html', 'ja', 'ja'],
    ['/thanks.html', null, 'en'],
    ['/ar/thanks.html', null, 'ar'],
  ]) {
    await page.evaluate(value => value === null
      ? localStorage.removeItem('lecturesift-ui') : localStorage.setItem('lecturesift-ui', value), saved);
    requested.length = 0;
    await page.goto(pathname);
    await expect.poll(() => page.evaluate(() => window.LectureSiftI18n?.language)).toBe(language);
    await expect(page).toHaveTitle(rows[TITLE][CODES.indexOf(language)]);
    expect(requested).not.toContain('/page-i18n.js');
    expect(requested.filter(path => LOADER.test(path))).toHaveLength(1);
    const catalogs = requested.filter(path => CATALOG.test(path) && !LOADER.test(path));
    expect(catalogs.map(path => path.match(CATALOG)[1])).toEqual(language === 'tr' ? [] : [language]);
    for (const catalog of catalogs) {
      expect((await (await request.get(catalog)).body()).length).toBeLessThan(fullBody.length / 4);
    }
    expect(requested).not.toContain('/i18n.js');
    expect(requested.filter(path => RUNTIME_LOADER.test(path))).toHaveLength(1);
    const runtimes = requested.filter(path => RUNTIME.test(path) && !RUNTIME_LOADER.test(path));
    expect(runtimes.map(path => path.match(RUNTIME)[1])).toEqual([language]);
    expect((await (await request.get(runtimes[0])).body()).length).toBeLessThan(runtime.size / 2);
    expect(await page.evaluate(() => window.LectureSiftI18n.t('language.label'))).toBe(label[CODES.indexOf(language)]);
  }
});

test('a page retained across deployment keeps its page copy and runtime when its old loaders are gone', async ({page, request}) => {
  const {body: fullBody, rows} = await fullCatalog(request);
  const runtime = await fullRuntime(request);
  for (const [missing, full] of [
    ['/assets/page-i18n/loader-000000000000.js', fullBody],
    ['/assets/page-i18n/de-000000000000.js', fullBody],
    ['/assets/i18n/loader-000000000000.js', runtime.body],
  ]) {
    const response = await request.get(missing);
    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).toMatch(/javascript/);
    expect(await response.text()).toBe(full);
  }
  expect((await request.get('/assets/page-i18n/nested/missing.js')).status()).toBe(404);
  // Only the HTML references change, as if they arrived just before a deploy.
  await page.route('**/de/thanks.html', async route => {
    const response = await route.fetch();
    const html = await response.text();
    expect(html).toMatch(/src="\/assets\/page-i18n\/loader-[a-f0-9]{12}\.js"/);
    expect(html).toMatch(/src="\/assets\/i18n\/loader-[a-f0-9]{12}\.js"/);
    await route.fulfill({response, body: html
      .replace(/\/assets\/page-i18n\/loader-[a-f0-9]{12}\.js/, '/assets/page-i18n/loader-000000000000.js')
      .replace(/\/assets\/i18n\/loader-[a-f0-9]{12}\.js/, '/assets/i18n/loader-000000000000.js')});
  });
  await page.goto('/de/thanks.html');
  await expect.poll(() => page.evaluate(() => window.LectureSiftI18n?.language)).toBe('de');
  await expect(page).toHaveTitle(rows[TITLE][CODES.indexOf('de')]);
  expect(await page.evaluate(() => window.LectureSiftI18n.t('language.label'))).toBe(runtime.row('language.label')[CODES.indexOf('de')]);
});

test('switching language on an app page shows the new language from its own runtime', async ({page, request}) => {
  const runtime = await fullRuntime(request);
  const label = runtime.row('language.label');
  const studyTab = runtime.row('workspace.studyTab');
  const requested = [];
  page.on('request', entry => {
    const pathname = new URL(entry.url()).pathname;
    if (pathname === '/i18n.js' || RUNTIME.test(pathname)) requested.push(pathname);
  });
  await page.addInitScript(() => localStorage.setItem('lecturesift-currency', 'TRY'));
  await page.goto('/thanks.html');
  await page.evaluate(() => localStorage.setItem('lecturesift-ui', 'de'));
  requested.length = 0;
  await page.goto('/workspace.html');
  const picker = page.locator('#uiLanguage');
  const expectLanguage = async (language, url) => {
    const index = CODES.indexOf(language);
    await expect(page).toHaveURL(url);
    await expect.poll(() => page.evaluate(() => window.LectureSiftI18n?.language)).toBe(language);
    await expect(page.locator('html')).toHaveAttribute('lang', language);
    await expect(page.locator('html')).toHaveAttribute('dir', language === 'ar' ? 'rtl' : 'ltr');
    await expect(picker).toHaveValue(language);
    await expect(picker).toHaveAttribute('aria-label', label[index]);
    await expect(page.locator('#workspaceStudyTab')).toHaveText(studyTab[index]);
    expect(requested).not.toContain('/i18n.js');
    expect(requested.filter(path => !RUNTIME_LOADER.test(path)).map(path => path.match(RUNTIME)[1])).toEqual([language]);
    requested.length = 0;
  };
  await expectLanguage('de', '/workspace.html');
  // Turkish keeps the unprefixed URL, where app.js also applies it in place.
  for (const [language, url] of [['tr', '/workspace.html'], ['en', '/en/workspace.html'], ['ar', '/ar/workspace.html']]) {
    const loaded = page.waitForEvent('load');
    await picker.selectOption(language);
    await loaded;
    await expectLanguage(language, url);
  }
});
