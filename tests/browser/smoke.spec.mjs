import {test, expect} from './fixtures.mjs';

async function noHorizontalOverflow(page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
}

async function themeControl(page, theme) {
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  const toggle = page.locator('.theme-toggle');
  await expect(toggle).toBeVisible();
  await expect(toggle).toHaveAccessibleName(/\S/);
  await toggle.click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme === 'light' ? 'dark' : 'light');
  await toggle.click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
}

test('localized home, navigation and demo quiz respond to real clicks', async ({page, isMobile}, testInfo) => {
  await page.goto('/en/');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.locator('#pageTitle')).toBeVisible();
  await expect(page.locator('.source-file')).toHaveCount(3);
  await themeControl(page, testInfo.project.use.colorScheme);

  const menu = page.locator('.public-menu-toggle');
  const navigation = page.locator('#publicNavigation');
  if (isMobile) {
    await expect(navigation).toBeHidden();
    await menu.click();
    await expect(menu).toHaveAttribute('aria-expanded', 'true');
    await expect(navigation).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(menu).toHaveAttribute('aria-expanded', 'false');
    await expect(menu).toBeFocused();
    await menu.click();
    // The expanded menu intentionally overlays the heading. Click an exposed
    // point below the header to exercise genuine outside-click dismissal.
    const outside = {x: page.viewportSize().width - 8, y: page.viewportSize().height - 8};
    expect(await page.evaluate(({x, y}) => {
      const target = document.elementFromPoint(x, y);
      return Boolean(target && !target.closest('.topbar'));
    }, outside)).toBe(true);
    await page.mouse.click(outside.x, outside.y);
    await expect(navigation).toBeHidden();
  } else {
    await expect(menu).toBeHidden();
    await expect(navigation).toBeVisible();
  }

  await page.locator('#demoQuizTab').click();
  await expect(page.locator('#demoQuizTab')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#demoSummary')).toBeHidden();
  await page.locator('[data-demo-answer="1"]').click();
  await expect(page.locator('[data-demo-answer="1"]')).toHaveClass(/is-wrong/);
  await expect(page.locator('[data-demo-answer="0"]')).toHaveClass(/is-correct/);
  await expect(page.locator('#demoFeedback')).toContainText('✕');
  for (const choice of await page.locator('[data-demo-answer]').all()) await expect(choice).toBeDisabled();
  await page.locator('#demoReset').click();
  await expect(page.locator('[data-demo-answer="0"]')).toBeFocused();
  await page.locator('[data-demo-answer="0"]').press('Enter');
  await expect(page.locator('#demoFeedback')).toContainText('✓');
  await noHorizontalOverflow(page);

  // Canonical links must resolve generated localized HTML, not source fallbacks.
  if (isMobile) await menu.click();
  await navigation.locator('a[href="/en/features"]').click();
  await expect(page).toHaveURL(/\/en\/features$/);
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.locator('h1')).toBeVisible();
  await noHorizontalOverflow(page);
});


test('about removes the product journey section', async ({page}) => {
  await page.goto('/about');
  await expect(page.locator('h1')).toBeVisible();
  await expect(page.locator('article section')).toHaveCount(4);
  await expect(page.getByRole('heading', {name:'Ürün yolculuğu'})).toHaveCount(0);
  await expect(page.locator('.legal-nav').getByText('Ürün', {exact:true})).toHaveCount(0);
  await noHorizontalOverflow(page);
});


test('official white payment marks remain visible in both themes', async ({page}, testInfo) => {
  await page.goto('/en/');
  const band = page.locator('.footer-payment-band');
  await band.scrollIntoViewIfNeeded();
  await expect(band).toBeVisible();
  await expect(band).toHaveCSS('background-color', 'rgb(17, 35, 59)');
  expect(await band.evaluate(img => img.complete && img.naturalWidth === 912)).toBe(true);
  const box = await band.boundingBox();
  expect(box.width).toBeGreaterThan(280);
  await noHorizontalOverflow(page);
  await page.screenshot({path: testInfo.outputPath('payment-marks.png')});
});


test('rebuilt study entry opens the real workspace and key screens remain usable', async ({page, context}, testInfo) => {
  await context.addInitScript(() => localStorage.setItem('lecturesift-currency', 'TRY'));
  await page.goto('/en/');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('.study-sources a')).toHaveCount(3);
  await expect(page.locator('.study-launcher')).toBeVisible();
  await noHorizontalOverflow(page);
  const capture = async name => {
    if (testInfo.project.name.endsWith('light')) await page.screenshot({path:testInfo.outputPath(name+'.jpg'), quality:75, fullPage:false});
  };
  await capture('home-layout');
  const illustration = page.locator('.study-illustration');
  await illustration.scrollIntoViewIfNeeded();
  await expect.poll(() => illustration.evaluate(img => img.complete && img.naturalWidth > 0)).toBe(true);
  await noHorizontalOverflow(page);
  await page.locator('#study-example').screenshot({path:testInfo.outputPath('study-illustration-layout.jpg'), quality:80});
  await page.locator('.source-youtube').click();
  await expect(page).toHaveURL(/workspace\.html\?source=link/);
  await expect(page.locator('#linkTab')).toHaveAttribute('aria-selected','true');
  await page.locator('#uploadTab').click();
  await expect(page.locator('#classicDropZone')).toBeVisible();
  await noHorizontalOverflow(page);
  await capture('workspace-layout');
  await page.goto('/en/plans');
  await expect(page.locator('#plansGrid .plan-card').first()).toBeVisible();
  await expect(page.locator('#billingCurrency option')).toHaveCount(22);
  await expect(page.locator('#errorBox')).toBeHidden();
  const marks=page.locator('.payment-brand-band');
  for (const mark of await marks.all()) {
    await expect(mark).toHaveCSS('background-color','rgb(17, 35, 59)');
    expect(await mark.evaluate(img=>img.complete && img.naturalWidth>0)).toBe(true);
  }
  await noHorizontalOverflow(page);
  await page.locator('#plansGrid').scrollIntoViewIfNeeded();
  await capture('plans-layout');
  await page.goto('/en/login');
  await expect(page.locator('input[type="email"]')).toBeVisible();
  await noHorizontalOverflow(page);
  const emailBox=await page.locator('input[type="email"]').boundingBox();
  expect(emailBox.y+emailBox.height).toBeLessThanOrEqual(page.viewportSize().height);
  const contrast=await page.locator('.auth-card .field>span').first().evaluate(node=>{
    const rgb=value=>(value.match(/[\d.]+/g)||[]).slice(0,3).map(Number);
    const luminance=values=>values.map(v=>{const c=v/255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4;}).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
    const ink=luminance(rgb(getComputedStyle(node).color));
    const paper=luminance(rgb(getComputedStyle(node.closest('.auth-card')).backgroundColor));
    return (Math.max(ink,paper)+.05)/(Math.min(ink,paper)+.05);
  });
  expect(contrast).toBeGreaterThanOrEqual(4.5);
  await capture('login-layout');
  await page.goto('/');
  await capture('turkish-layout');
  await page.goto('/ar/');
  await expect(page.locator('html')).toHaveAttribute('dir','rtl');
  await noHorizontalOverflow(page);
  await capture('arabic-layout');
});
