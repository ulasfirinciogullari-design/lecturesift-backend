import {test, expect} from './fixtures.mjs';

const noHorizontalOverflow = async page => {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
};

// Turkish shows the Turkish capture; every other language the English one.
for (const [path, capture] of [['/', 'tr'], ['/en/', 'en'], ['/ar/', 'en']]) {
  test(`result screenshot on ${path} reserves its space, loads lazily and fits the screen`, async ({page, isMobile}, testInfo) => {
    // Hold the screenshot back so the page can be measured before it arrives.
    const requests = [];
    let release;
    const arrived = new Promise(resolve => { release = resolve; });
    await page.route('**/assets/home/**', async route => {
      requests.push(new URL(route.request().url()).pathname);
      await arrived;
      await route.fallback();
    });
    await page.goto(path, {waitUntil: 'domcontentloaded'});
    const section = page.locator('#result-preview');
    const image = section.locator('picture > img');
    await expect(section.locator('h2')).not.toBeEmpty();
    await expect(section.locator('figcaption')).not.toBeEmpty();
    await expect(image).toHaveAttribute('loading', 'lazy');
    await expect(image).toHaveAttribute('alt', /LectureSift/);
    const [file, width, height] = isMobile
      ? [`/assets/home/result-screen-v1-${capture}-720.webp`, 720, 1280]
      : [`/assets/home/result-screen-v1-${capture}-1440.webp`, 1440, 840];
    // Not loaded yet, yet the box already has its final size: no layout shift.
    expect(await image.evaluate(node => node.naturalWidth)).toBe(0);
    const reserved = await image.boundingBox();
    release();
    const consent = page.locator('[data-consent="essential"]');
    if (await consent.isVisible()) await consent.click();
    await image.scrollIntoViewIfNeeded();
    await expect.poll(() => image.evaluate(node => node.complete && node.naturalWidth > 0)).toBe(true);
    expect(await image.evaluate(node => [new URL(node.currentSrc).pathname, node.naturalWidth, node.naturalHeight])).toEqual([file, width, height]);
    expect(requests).toEqual([file]);
    const loaded = await image.boundingBox();
    expect(Math.abs(loaded.width - reserved.width)).toBeLessThanOrEqual(1);
    expect(Math.abs(loaded.height - reserved.height)).toBeLessThanOrEqual(1);
    // The 1px border sits outside the picture's own aspect ratio.
    expect((loaded.width - 2) / (loaded.height - 2)).toBeCloseTo(width / height, 2);
    const viewport = page.viewportSize();
    expect(loaded.x).toBeGreaterThanOrEqual(0);
    expect(loaded.x + loaded.width).toBeLessThanOrEqual(viewport.width);
    expect(loaded.width).toBeLessThanOrEqual(960);
    await noHorizontalOverflow(page);
    if (path === '/en/') await section.screenshot({path: testInfo.outputPath('result-preview-layout.jpg'), quality: 80});
  });
}

test('the closing band repeats the hero trial button', async ({page}, testInfo) => {
  for (const path of ['/', '/en/', '/ar/']) {
    await page.goto(path);
    const consent = page.locator('[data-consent="essential"]');
    if (await consent.isVisible()) await consent.click();
    const hero = page.locator('.study-hero .study-primary');
    const band = page.locator('.home-cta .study-primary');
    await expect(band).toHaveCount(1);
    await expect(band).toHaveAttribute('href', await hero.getAttribute('href'));
    await expect(band).toHaveText(await hero.textContent());
    // It is the last thing on the page before the footer.
    expect(await page.locator('main > :last-child').evaluate(node => node.classList.contains('home-cta'))).toBe(true);
    await band.scrollIntoViewIfNeeded();
    await expect(band).toBeVisible();
    const box = await band.boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize().width);
    await noHorizontalOverflow(page);
    if (path === '/en/') await page.locator('.home-cta').screenshot({path: testInfo.outputPath('closing-band-layout.jpg'), quality: 80});
  }
  await page.locator('.home-cta .study-primary').click();
  await expect(page).toHaveURL(/\/ar\/workspace(?:\.html)?\?source=upload$/);
});
