import {test, expect} from './fixtures.mjs';

test('runtime SEO retains the localized product identity on policy and article pages', async ({page, request}) => {
  for (const language of ['en', 'ar']) {
    const home = await request.get(`/${language}/`);
    expect(home.status()).toBe(200);
    const html = await home.text();
    const source = html.match(/<script type="application\/ld\+json" data-lecturesift-seo>([\s\S]*?)<\/script>/);
    expect(source).not.toBeNull();
    const expected = JSON.parse(source[1])['@graph'].find(node => node['@type'] === 'SoftwareApplication').description;
    for (const slug of ['privacy', 'document-summary']) {
      await page.goto(`/${language}/${slug}`);
      // This marker is added by seo.js, after prerendered content has loaded.
      await expect(page.locator('meta[property="og:locale:alternate"]')).toHaveCount(12);
      const graph = await page.locator('script[data-lecturesift-seo]').evaluate(node => JSON.parse(node.textContent)['@graph']);
      const application = graph.find(node => node['@type'] === 'SoftwareApplication');
      const webpage = graph.find(node => node['@type'] === 'WebPage');
      expect(application.description).toBe(expected);
      expect(application.description).not.toBe(webpage.description);
      if (slug === 'document-summary') {
        expect(graph.find(node => node['@type'] === 'Article').description).toBe(webpage.description);
      }
    }
  }
});

test('runtime SEO recovers a product description when the static graph is malformed', async ({page}) => {
  await page.route('**/en/privacy', async route => {
    const response = await route.fetch();
    const body = (await response.text()).replace(
      /(<script type="application\/ld\+json" data-lecturesift-seo>)[\s\S]*?(<\/script>)/,
      '$1{broken-json$2',
    );
    await route.fulfill({response, body});
  });
  await page.goto('/en/privacy');
  await expect(page.locator('meta[property="og:locale:alternate"]')).toHaveCount(12);
  const graph = await page.locator('script[data-lecturesift-seo]').evaluate(node => JSON.parse(node.textContent)['@graph']);
  const application = graph.find(node => node['@type'] === 'SoftwareApplication');
  const webpage = graph.find(node => node['@type'] === 'WebPage');
  expect(application.description).toContain('summaries, transcripts, quizzes, flashcards');
  expect(application.description).not.toBe(webpage.description);
});
