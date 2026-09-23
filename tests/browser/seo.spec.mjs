import {test, expect} from './fixtures.mjs';

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
