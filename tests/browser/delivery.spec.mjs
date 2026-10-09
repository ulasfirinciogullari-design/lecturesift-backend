import {test, expect} from './fixtures.mjs';

test('the assistant reuses a stylesheet already present on the page', async ({page}) => {
  const stylesheets = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/assistant.css') stylesheets.push(request.url());
  });
  for (const pathname of ['/', '/en/', '/assistant.html']) {
    stylesheets.length = 0;
    await page.goto(pathname);
    await expect(page.locator('.assistant-surface')).toHaveCount(1);
    const links = await page.locator('link[rel="stylesheet"]').evaluateAll(nodes =>
      nodes.filter(node => new URL(node.href).pathname === '/assistant.css').map(node => node.href),
    );
    expect(links).toHaveLength(1);
    await expect.poll(() => stylesheets).toEqual(links);
  }
});

test('homepage illustration selects responsive sources and reserves its space', async ({page}) => {
  const requests = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/.netlify/images' || url.pathname.endsWith('/study-desk-v1.png')) requests.push(url);
  });
  await page.goto('/en/');
  const illustration = page.locator('picture img.study-illustration');
  await expect(illustration).toHaveAttribute('src', '/assets/study/study-desk-v1.png');
  await expect(illustration).toHaveAttribute('width', '1536');
  await expect(illustration).toHaveAttribute('height', '1024');
  await expect(illustration).toHaveAttribute('loading', 'lazy');
  await expect(illustration).toHaveAttribute('alt', '');
  await illustration.scrollIntoViewIfNeeded();
  await expect.poll(() => illustration.evaluate(node => node.complete && node.naturalWidth > 0)).toBe(true);
  const selected = new URL(await illustration.evaluate(node => node.currentSrc));
  expect(selected.pathname).toBe('/.netlify/images');
  expect(selected.searchParams.get('w')).toBe('400');
  expect(selected.searchParams.get('fm')).toBe('webp');
  expect(requests.every(url => url.pathname === '/.netlify/images')).toBe(true);

  await page.setViewportSize({width: 768, height: 900});
  await expect.poll(async () => new URL(await illustration.evaluate(node => node.currentSrc)).searchParams.get('w')).toBe('480');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  const box = await illustration.boundingBox();
  expect(box.width).toBeLessThanOrEqual(480);
  expect(box.width / box.height).toBeCloseTo(1.5, 1);

  // Browsers without the advertised format can still display the original.
  await page.locator('picture source').evaluate(node => { node.type = 'image/unsupported'; });
  await expect.poll(async () => new URL(await illustration.evaluate(node => node.currentSrc)).pathname).toBe('/assets/study/study-desk-v1.png');
  await expect.poll(() => illustration.evaluate(node => node.complete && node.naturalWidth > 0)).toBe(true);
});
