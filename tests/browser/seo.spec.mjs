import {test, expect} from './fixtures.mjs';

test('locale rewrites keep private pages and assets available without serving nested public copies', async ({request}) => {
  for (const prefix of ['/en', '/fr', '/hi']) {
    const workspace = await request.get(`${prefix}/workspace`);
    expect(workspace.status()).toBe(200);
    expect(await workspace.text()).toContain('content="noindex,follow"');
    expect((await request.get(`${prefix}/auth.css`)).status()).toBe(200);
    expect((await request.get(`${prefix}/assets/study/cornell-notes-en.txt`)).status()).toBe(200);
    expect((await request.get(`${prefix}/en/features`)).status()).toBe(404);
    expect((await request.get(`${prefix}/de/`)).status()).toBe(404);
    expect((await request.get(`${prefix}/sitemap.xml`)).status()).toBe(404);
  }
});

test('public runtime bundles preserve every language and picker navigation', async ({page}) => {
  for (const language of ['tr', 'en', 'de', 'fr', 'es', 'it', 'pt', 'ru', 'ar', 'zh', 'ja', 'ko', 'hi']) {
    const prefix = language === 'tr' ? '' : `/${language}`;
    await page.goto(`${prefix}/document-summary`);
    await expect.poll(() => page.evaluate(() => window.LectureSiftI18n?.language)).toBe(language);
    await expect(page.locator('html')).toHaveAttribute('lang', language);
    await expect(page.locator('.language-switcher, #uiLanguage')).toHaveValue(language);
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', `https://lecturesift.com${prefix}/document-summary`);
    expect(await page.evaluate(() => window.LectureSiftI18n.t('language.label'))).not.toBe('language.label');
  }
  await page.locator('.language-switcher, #uiLanguage').selectOption('en');
  await expect(page).toHaveURL('/en/document-summary');
  await expect(page.locator('[data-landing-page] h1')).toContainText('PDF');
});

test('runtime SEO preserves the language of the breadcrumb home', async ({page}) => {
  for (const prefix of ['', '/en', '/ar']) {
    await page.goto(`${prefix}/document-summary`);
    // Wait for the client SEO pass, not only the prerendered JSON-LD.
    await expect(page.locator('meta[property="og:locale:alternate"]')).toHaveCount(12);
    const graph = await page.locator('script[data-lecturesift-seo]').evaluate(
      node => JSON.parse(node.textContent)['@graph'],
    );
    const breadcrumb = graph.find(node => node['@type'] === 'BreadcrumbList');
    const webpage = graph.find(node => node['@type'] === 'WebPage');
    expect(breadcrumb.itemListElement.map(item => item.item)).toEqual([
      `https://lecturesift.com${prefix}/`,
      `https://lecturesift.com${prefix}/document-summary`,
    ]);
    expect(webpage.breadcrumb).toEqual({'@id': breadcrumb['@id']});
  }
});

test('product examples work after localization and fit the viewport', async ({page}, testInfo) => {
  for (const prefix of ['', '/en']) {
    for (const slug of ['document-summary', 'lecture-video-summary', 'quiz-flashcards']) {
      await page.goto(`${prefix}/${slug}`);
      await expect(page.locator('meta[property="og:locale:alternate"]')).toHaveCount(12);
      await expect(page.locator('[data-landing-page] h1')).toHaveCount(1);
      const answer = page.locator('.landing-answer');
      await expect(answer.locator('p')).not.toBeVisible();
      await answer.locator('summary').click();
      await expect(answer.locator('p')).toBeVisible();
      const graph = await page.locator('script[data-lecturesift-seo]').evaluate(node => JSON.parse(node.textContent)['@graph']);
      expect(graph.find(node => node['@type'] === 'Article').dateModified).toBe('2026-09-23');
      expect(graph.find(node => node['@type'] === 'FAQPage').mainEntity).toHaveLength(4);
      const links = await page.locator('.landing-related-grid>a').evaluateAll(nodes => nodes.map(node => node.getAttribute('href')));
      expect(links.every(href => prefix ? href.startsWith('/en/') : !href.startsWith('/en/'))).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
      if (!prefix && slug === 'document-summary' && testInfo.project.name.endsWith('light')) {
        await answer.locator('summary').click();
        await page.evaluate(() => scrollTo(0, 0));
        await page.screenshot({path: testInfo.outputPath('seo-landing-layout.jpg'), fullPage: true, type: 'jpeg', quality: 65});
      }
    }
  }
  await page.locator('.landing-related-grid a').first().click();
  await expect(page).toHaveURL('/en/active-recall');
  await page.locator('[data-guide-products] a').click();
  await expect(page).toHaveURL('/en/quiz-flashcards');
});

test.describe('search-visible product content', () => {
  test.use({javaScriptEnabled: false});
  test('source, answer and navigation are available without JavaScript', async ({page}) => {
    await page.goto('/en/document-summary');
    await expect(page.locator('h1')).toContainText('PDF');
    await expect(page.locator('.landing-source')).toContainText('150 minutes');
    await page.locator('.landing-answer summary').click();
    await expect(page.locator('.landing-answer p')).toContainText('40 minutes');
    await page.locator('.landing-related-grid a').first().click();
    await expect(page).toHaveURL('/en/study-pack-example');
    await expect(page.locator('#source-2')).toContainText('150');
  });
});
