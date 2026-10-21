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

test('homepage illustration serves sized WebP files and reserves its space', async ({page, isMobile}) => {
  const requests = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/.netlify/images' || url.pathname.startsWith('/assets/study/study-desk')) requests.push(url.pathname);
  });
  await page.goto('/en/');
  const illustration = page.locator('img.study-illustration');
  await expect(illustration).toHaveAttribute('src', '/assets/study/study-desk-v1-480.webp');
  await expect(illustration).toHaveAttribute('srcset', '/assets/study/study-desk-v1-480.webp 480w, /assets/study/study-desk-v1-960.webp 960w');
  await expect(illustration).toHaveAttribute('width', '480');
  await expect(illustration).toHaveAttribute('height', '320');
  await expect(illustration).toHaveAttribute('loading', 'lazy');
  await expect(illustration).toHaveAttribute('alt', 'Study desk: an open notebook, a tablet playing a lecture video, headphones and flashcards');
  await illustration.scrollIntoViewIfNeeded();
  await expect.poll(() => illustration.evaluate(node => node.complete && node.naturalWidth > 0)).toBe(true);
  // At one device pixel per CSS pixel the 480-pixel file covers the slot, which is never wider than 480px.
  expect(new URL(await illustration.evaluate(node => node.currentSrc)).pathname).toBe('/assets/study/study-desk-v1-480.webp');
  expect(requests).toEqual(['/assets/study/study-desk-v1-480.webp']);
  // Each file is exactly the size its srcset descriptor promises.
  expect(await page.evaluate(paths => Promise.all(paths.map(async path => {
    const bitmap = await createImageBitmap(await (await fetch(path)).blob());
    return [bitmap.width, bitmap.height];
  })), ['/assets/study/study-desk-v1-480.webp', '/assets/study/study-desk-v1-960.webp'])).toEqual([[480, 320], [960, 640]]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  const box = await illustration.boundingBox();
  expect(box.width).toBeLessThanOrEqual(480);
  expect(box.width / box.height).toBeCloseTo(1.5, 1);

  // A 2x screen gets the 960-pixel file.
  const session = await page.context().newCDPSession(page);
  await session.send('Emulation.setDeviceMetricsOverride', {...page.viewportSize(), deviceScaleFactor: 2, mobile: isMobile});
  await page.reload();
  await illustration.scrollIntoViewIfNeeded();
  await expect.poll(() => illustration.evaluate(node => node.complete && node.naturalWidth > 0
    ? new URL(node.currentSrc).pathname : null)).toBe('/assets/study/study-desk-v1-960.webp');
});
