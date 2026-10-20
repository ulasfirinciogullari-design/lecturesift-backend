import {test, expect} from './fixtures.mjs';

const CODES = ['tr', 'en', 'de', 'fr', 'es', 'it', 'pt', 'ru', 'ar', 'zh', 'ja', 'ko', 'hi'];
const LOADER = /^\/assets\/page-i18n\/loader-[a-f0-9]{12}\.js$/;
const CATALOG = /^\/assets\/page-i18n\/([a-z]+)-[a-f0-9]{12}\.js$/;
// thanks.html's title is translated only through the page-copy catalog.
const TITLE = 'Mesajın Alındı | LectureSift';

async function fullCatalog(request) {
  const response = await request.get('/page-i18n.js');
  expect(response.status()).toBe(200);
  const body = await response.text();
  return {body, rows: JSON.parse(body.split('window.LECTURESIFT_PAGE_COPY=')[1].trim().replace(/;$/, ''))};
}

test('app pages download only the page-copy catalog of the reader language', async ({page, request}) => {
  const {body: fullBody, rows} = await fullCatalog(request);
  const requested = [];
  page.on('request', entry => {
    const pathname = new URL(entry.url()).pathname;
    if (pathname.includes('page-i18n')) requested.push(pathname);
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
  }
});

test('a page retained across deployment keeps its page copy when its old loader is gone', async ({page, request}) => {
  const {body: fullBody, rows} = await fullCatalog(request);
  for (const missing of ['/assets/page-i18n/loader-000000000000.js', '/assets/page-i18n/de-000000000000.js']) {
    const response = await request.get(missing);
    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).toMatch(/javascript/);
    expect(await response.text()).toBe(fullBody);
  }
  expect((await request.get('/assets/page-i18n/nested/missing.js')).status()).toBe(404);
  // Only the HTML reference changes, as if it arrived just before a deploy.
  await page.route('**/de/thanks.html', async route => {
    const response = await route.fetch();
    const html = await response.text();
    expect(html).toMatch(/src="\/assets\/page-i18n\/loader-[a-f0-9]{12}\.js"/);
    await route.fulfill({response, body: html.replace(/\/assets\/page-i18n\/loader-[a-f0-9]{12}\.js/, '/assets/page-i18n/loader-000000000000.js')});
  });
  await page.goto('/de/thanks.html');
  await expect.poll(() => page.evaluate(() => window.LectureSiftI18n?.language)).toBe('de');
  await expect(page).toHaveTitle(rows[TITLE][CODES.indexOf('de')]);
});
