import {createHash} from 'node:crypto';
import {test, expect} from './fixtures.mjs';

const runtimeSource = html => html.match(/src="(\/assets\/i18n\/[a-z]+-[a-f0-9]{12}\.js)"/)?.[1];
const staleRuntime = language => `/assets/i18n/${language}-000000000000.js`;

test('missing previous locale bundles fall back without replacing current bundles or unrelated 404s', async ({request}) => {
  const fullRuntime = await request.get('/i18n.js');
  expect(fullRuntime.status()).toBe(200);
  const fullBody = await fullRuntime.body();
  for (const language of ['tr', 'en', 'de', 'fr', 'es', 'it', 'pt', 'ru', 'ar', 'zh', 'ja', 'ko', 'hi']) {
    const prefix = language === 'tr' ? '' : `/${language}`;
    const html = await request.get(`${prefix}/document-summary`);
    expect(html.status()).toBe(200);
    const source = runtimeSource(await html.text());
    expect(source).toMatch(new RegExp(`^/assets/i18n/${language}-[a-f0-9]{12}\\.js$`));
    const current = await request.get(source);
    expect(current.status()).toBe(200);
    const currentBody = await current.body();
    // A broad/forced rewrite would return the full catalog here and fail the
    // content-address check even though the response status remained 200.
    const hash = createHash('sha256').update(currentBody).digest('hex').slice(0, 12);
    expect(source).toBe(`/assets/i18n/${language}-${hash}.js`);
    expect(currentBody.length).toBeLessThan(fullBody.length);
    const previous = await request.get(staleRuntime(language));
    expect(previous.status()).toBe(200);
    expect(previous.headers()['content-type']).toMatch(/javascript/);
    expect(await previous.body()).toEqual(fullBody);
  }
  for (const missing of ['/assets/i18n/nested/missing.js', '/assets/missing-runtime.js', '/missing-runtime.js']) {
    expect((await request.get(missing)).status()).toBe(404);
  }
});

test('a page retained across deployment keeps its locale and picker when its old bundle is gone', async ({page}) => {
  // Replace only the HTML reference, as if it arrived just before a deploy.
  // The browser requests the absent URL from the real test server; no script
  // response or runtime is mocked, and original defer/order stays intact.
  await page.route('**/document-summary', async route => {
    const pathname = new URL(route.request().url()).pathname;
    const language = pathname.startsWith('/en/') ? 'en' : pathname.startsWith('/ar/') ? 'ar' : 'tr';
    const response = await route.fetch();
    const html = await response.text();
    const source = runtimeSource(html);
    expect(source).toBeTruthy();
    await route.fulfill({response, body: html.replace(`src="${source}"`, `src="${staleRuntime(language)}"`)});
  });
  for (const language of ['tr', 'en', 'ar']) {
    const prefix = language === 'tr' ? '' : `/${language}`;
    const recovered = page.waitForResponse(response => new URL(response.url()).pathname === staleRuntime(language));
    await page.goto(`${prefix}/document-summary`);
    expect((await recovered).status()).toBe(200);
    await expect.poll(() => page.evaluate(() => window.LectureSiftI18n?.language)).toBe(language);
    await expect(page.locator('html')).toHaveAttribute('lang', language);
    await expect(page.locator('html')).toHaveAttribute('dir', language === 'ar' ? 'rtl' : 'ltr');
    await expect(page.locator('.language-switcher, #uiLanguage')).toHaveValue(language);
    await expect(page.locator('.language-switcher option, #uiLanguage option')).toHaveCount(13);
    await expect(page.locator('meta[property="og:locale:alternate"]')).toHaveCount(12);
    expect(await page.evaluate(() => window.LectureSiftI18n.localizedPath(window.LectureSiftI18n.language, '/plans'))).toBe(`${prefix}/plans`);
    expect(await page.evaluate(() => window.LectureSiftI18n.t('language.label'))).not.toBe('language.label');
  }
  await page.locator('.language-switcher, #uiLanguage').selectOption('en');
  await expect(page).toHaveURL('/en/document-summary');
  await expect.poll(() => page.evaluate(() => window.LectureSiftI18n?.language)).toBe('en');
  await expect(page.locator('[data-landing-page] h1')).toContainText('PDF');
});
