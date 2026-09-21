import {test, expect} from './fixtures.mjs';

test('localized home links to an actual guide edition after client localization', async ({page}, testInfo) => {
  await page.goto('/de/');
  const link = page.locator('.study-resource-entry a');
  await expect(link).toHaveAttribute('href', '/en/study-guides');
  await link.click();
  await expect(page).toHaveURL(/\/en\/study-guides$/);
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.locator('.guide-grid .guide-card')).toHaveCount(3);
  await expect(page.getByRole('heading', {level:1})).toContainText('Study guides');
  await expect(page.locator('.guide-preview')).toContainText('Mean');
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({path:testInfo.outputPath('study-library-layout.jpg'),type:'jpeg',quality:65});
});

test.describe('open learning material without JavaScript', () => {
  test.use({javaScriptEnabled:false});

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
      for (const slug of ['study-guides','study-pack-example','check-ai-notes','active-recall','about-study-guides']) {
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
