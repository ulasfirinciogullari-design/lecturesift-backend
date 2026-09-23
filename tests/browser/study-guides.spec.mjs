import {test, expect} from './fixtures.mjs';

test('localized home links to an actual guide edition after client localization', async ({page}, testInfo) => {
  await page.goto('/de/');
  const link = page.locator('.study-resource-entry a');
  await expect(link).toHaveAttribute('href', '/en/study-guides');
  await link.click();
  await expect(page).toHaveURL(/\/en\/study-guides$/);
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.locator('.guide-grid .guide-card')).toHaveCount(4);
  await expect(page.getByRole('heading', {level:1})).toContainText('Study guides');
  await expect(page.locator('.guide-preview')).toContainText('Mean');
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({path:testInfo.outputPath('study-library-layout.jpg'),type:'jpeg',quality:65});
});

test('guides expose localized privacy choices without loading the application catalog', async ({page, isolatedNetwork}) => {
  await page.goto('/en/cornell-notes');
  await expect(page.locator('.consent-banner')).toBeVisible();
  await expect(page.locator('.consent-banner a')).toHaveAttribute('href', '/en/cookies');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('.consent-banner')).toBeHidden();
  expect(await page.evaluate(() => window.LectureSiftConsent.get().analytics)).toBe(false);
  await page.locator('.consent-manage').click();
  await expect(page.locator('.consent-modal')).toBeVisible();
  await page.locator('[data-consent="close"]').click();
  expect(await page.locator('script[src]').evaluateAll(scripts => scripts.map(s => s.getAttribute('src')).some(src => /(?:^|\/)i18n\.js|page-i18n\.js/.test(src)))).toBe(false);
  expect(isolatedNetwork.apiCalls).not.toContain('/analytics/config');
});

test.describe('open learning material without JavaScript', () => {
  test.use({javaScriptEnabled:false});

  test('Cornell example, answer and editable template work without JavaScript', async ({page}, testInfo) => {
    for (const prefix of ['', '/en']) {
      await page.goto(`${prefix}/study-guides`);
      await page.locator(`.guide-card[href="${prefix}/cornell-notes"]`).click();
      await expect(page.getByRole('heading', {level: 1})).toContainText('Cornell');
      const answer = page.locator('.guide-article details');
      await expect(answer.locator('p')).toBeHidden();
      await answer.locator('summary').click();
      await expect(answer.locator('p')).toContainText('150');
      const downloadPromise = page.waitForEvent('download');
      await page.locator('a[download]').click();
      const download = await downloadPromise;
      expect(download.suggestedFilename()).toBe(`cornell-notes-${prefix ? 'en' : 'tr'}.txt`);
      expect(await download.failure()).toBeNull();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
      if (!prefix && testInfo.project.name.endsWith('light')) {
        await page.evaluate(() => scrollTo(0, 0));
        await page.screenshot({path: testInfo.outputPath('cornell-guide-layout.jpg'), fullPage: true, type: 'jpeg', quality: 65});
      }
      await page.locator(`[data-guide-products] a[href="${prefix}/lecture-video-summary"]`).click();
      await expect(page).toHaveURL(`${prefix}/lecture-video-summary`);
      await page.locator(`.landing-related-grid a[href="${prefix}/cornell-notes"]`).click();
      await expect(page).toHaveURL(`${prefix}/cornell-notes`);
    }
  });

  test('source, native answer disclosures, language switch and worksheet remain usable', async ({page}, testInfo) => {
    await page.goto('/study-pack-example');
    await expect(page.locator('#kaynak-2')).toContainText('150 ÷ 5 = 30');
    await expect(page.locator('.guide-toc ol')).not.toBeVisible();
    await page.locator('.guide-toc summary').click();
    await expect(page.locator('.guide-toc ol')).toBeVisible();
    await page.locator('.guide-toc summary').click();
    const question = page.locator('#sorular details').nth(3);
    await expect(question.locator('p')).not.toBeVisible();
    await question.locator('summary').click();
    await expect(question.locator('p')).toBeVisible();
    await expect(question.locator('p')).toContainText('(12 + 14) ÷ 2 = 13');
    const downloadPromise = page.waitForEvent('download');
    await page.locator('a[download]').click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe('mean-median-tr.txt');
    expect(await download.failure()).toBeNull();
    await page.locator('.guide-language').click();
    await expect(page).toHaveURL(/\/en\/study-pack-example$/);
    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
    await expect(page.locator('#source-4')).toContainText('120 rather than 70');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    await page.screenshot({path:testInfo.outputPath('study-pack-layout.jpg'),type:'jpeg',quality:65});
  });

  test('every published guide has a readable body and working internal section links', async ({page}) => {
    for (const prefix of ['', '/en']) {
      for (const slug of ['study-guides','study-pack-example','check-ai-notes','active-recall','about-study-guides','cornell-notes']) {
        await page.goto(`${prefix}/${slug}`);
        await expect(page.locator('.guide-article h1')).toHaveCount(1);
        await expect(page.locator('.guide-article section').first()).toBeVisible();
        expect(await page.locator('.guide-toc a[href^="#"]').evaluateAll(links => links.every(link => document.getElementById(link.hash.slice(1))))).toBe(true);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
      }
    }
  });

  test('about the guides opens a real page from the library and an article in both languages', async ({page}, testInfo) => {
    for (const prefix of ['', '/en']) {
      for (const slug of ['study-guides', 'active-recall']) {
        await page.goto(`${prefix}/${slug}`);
        await page.locator('.guide-footer [data-guide-about]').click();
        await expect(page).toHaveURL(`${prefix}/about-study-guides`);
        await expect(page.getByRole('heading', {level: 1})).toHaveText(prefix ? 'About these guides' : 'Rehberler hakkında');
        await expect(page.locator(prefix ? '#corrections a[href="/en/contact"]' : '#duzeltme a[href="/contact"]')).toBeVisible();
      }
    }
    await page.locator('.guide-language').click();
    await expect(page).toHaveURL('/about-study-guides');
    await expect(page.getByRole('heading', {level: 1})).toHaveText('Rehberler hakkında');
    await page.locator('#duzeltme .guide-button').click();
    await expect(page).toHaveURL('/study-guides');
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({path:testInfo.outputPath('study-library-turkish-layout.jpg'),fullPage:true,type:'jpeg',quality:65});
  });
});
