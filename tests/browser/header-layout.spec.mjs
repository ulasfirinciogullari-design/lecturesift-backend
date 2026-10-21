import {test, expect} from './fixtures.mjs';

// Layout regressions from the design review: the narrow language control, the
// phone assistant button, the workspace's duplicate "My lessons", the Plus
// card buttons, the plans account strip and the header's trial action.
const CODES = ['tr', 'en', 'de', 'fr', 'es', 'it', 'pt', 'ru', 'ar', 'zh', 'ja', 'ko', 'hi'];
const home = code => code === 'tr' ? '/' : `/${code}/`;
const intersects = (a, b) => Boolean(a && b)
  && a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

async function noHorizontalOverflow(page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
}

async function expectCompactLanguage(page, code, {wholePage = true} = {}) {
  const chip = page.locator('.public-header-tools .language-code');
  const select = page.locator('.public-header-tools .language-compact > select');
  await expect(chip).toBeVisible();
  await expect(chip).toHaveText(code.toUpperCase());
  await expect(select).toHaveValue(code);
  await expect(select).toHaveAccessibleName(/\S/);
  const metrics = await page.evaluate(() => {
    const rect = node => node.getBoundingClientRect().toJSON();
    const chip = document.querySelector('.public-header-tools .language-code');
    const select = chip.parentElement.querySelector('select');
    const chipBox = rect(chip);
    const hit = document.elementFromPoint(chipBox.x + chipBox.width / 2, chipBox.y + chipBox.height / 2);
    return {
      clipped: chip.scrollWidth > chip.clientWidth,
      tapsReachSelect: hit === select,
      controls: [':is(.topbar,.legal-topbar) > .brand', '.public-header-tools .theme-toggle',
        '.public-header-tools .language-code', '.public-header-tools .public-menu-toggle']
        .map(selector => rect(document.querySelector(selector))),
      viewport: document.documentElement.clientWidth,
      headerOverflow: (header => header.scrollWidth - header.clientWidth)(chip.closest(':is(.topbar,.legal-topbar)')),
    };
  });
  expect(metrics.headerOverflow, `${code} header overflows`).toBeLessThanOrEqual(1);
  expect(metrics.clipped, `${code} language code is clipped`).toBe(false);
  expect(metrics.tapsReachSelect, `${code} taps reach the native select`).toBe(true);
  metrics.controls.forEach((box, index) => {
    expect(box.width).toBeGreaterThan(0);
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(metrics.viewport + 0.5);
    for (const other of metrics.controls.slice(index + 1)) expect(intersects(box, other), `${code} header controls overlap`).toBe(false);
  });
  if (wholePage) await noHorizontalOverflow(page);
}

test('narrow headers show the language as a code without crowding the other controls', async ({page, isMobile}, testInfo) => {
  test.skip(!isMobile, 'The compact language control is the narrow-screen layout');
  test.setTimeout(120_000);
  await page.addInitScript(() => localStorage.setItem('lecturesift-currency', 'TRY'));
  const widths = testInfo.project.name === 'mobile-light' ? [390, 320] : [390];
  let consented = false;
  for (const width of widths) {
    await page.setViewportSize({width, height: 844});
    for (const code of CODES) {
      await page.goto(home(code));
      if (!consented) {
        await page.locator('[data-consent="essential"]').click();
        consented = true;
      }
      // 390px is the supported phone width and must not scroll sideways. At
      // 320px only the header is held to that here; the page body is logged.
      await expectCompactLanguage(page, code, {wholePage: width === 390});
      if (width === 320) {
        const wide = await page.evaluate(() => {
          const limit = document.documentElement.clientWidth;
          const nodes = [...document.querySelectorAll('body *')].filter(node => { const box = node.getBoundingClientRect(); return box.right > limit + 1 || box.left < -1; });
          const outermost = nodes.filter(node => !nodes.includes(node.parentElement));
          return {overflow: document.documentElement.scrollWidth - limit,
            nodes: outermost.slice(0, 3).map(node => `${node.tagName.toLowerCase()}.${[...node.classList].join('.')}`)};
        });
        console.log(`page ${code} 320px overflow=${wide.overflow} ${wide.nodes.join(' ')}`);
      }
      if (code === 'tr' && width === 320) await page.screenshot({path: testInfo.outputPath('language-tr-320-layout.jpg'), quality: 75});
      if (code === 'ar' && width === 390) await page.screenshot({path: testInfo.outputPath('language-ar-layout.jpg'), quality: 75});
    }
  }
  await page.setViewportSize({width: 390, height: 844});
  // i18n.js creates these pickers; app.js rebuilds the workspace one.
  for (const [path, code] of [['/en/features', 'en'], ['/ar/plans', 'ar'], ['/ar/workspace.html', 'ar'], ['/de/workspace.html', 'de']]) {
    await page.goto(path);
    await expectCompactLanguage(page, code);
  }
  // The transparent native control still switches the language.
  await page.goto('/features');
  await page.locator('.public-header-tools .language-compact > select').selectOption('en');
  await expect(page).toHaveURL(/\/en\/features$/);
  await expect(page.locator('.public-header-tools .language-code')).toHaveText('EN');
});

test('desktop headers keep the full language name', async ({page, isMobile}) => {
  test.skip(isMobile, 'Desktop layout');
  await page.goto('/en/');
  await expect(page.locator('.public-header-tools .language-code')).toBeHidden();
  const select = page.locator('.public-header-tools .language-compact > select');
  await expect(select).toBeVisible();
  await expect(select).toHaveCSS('opacity', '1');
  await expect(select.locator('option:checked')).toHaveText('English');
});

test('the phone assistant button does not cover the home upload button', async ({page, isMobile}, testInfo) => {
  test.skip(!isMobile, 'The floating button covers content only on narrow screens');
  await page.setViewportSize({width: 390, height: 844});
  await page.addInitScript(() => localStorage.setItem('lecturesift-currency', 'TRY'));
  for (const path of ['/', '/en/']) {
    await page.goto(path);
    if (path === '/') await page.locator('[data-consent="essential"]').click();
    const launch = page.locator('body > .assistant-launch');
    const upload = page.locator('.study-primary');
    await expect(launch).toBeVisible();
    await expect(launch).toHaveAccessibleName(path === '/' ? /^LectureSift Asistan/ : /^LectureSift Assistant/);
    await expect(launch.locator('span')).toBeHidden();
    const size = await launch.boundingBox();
    expect(size.width).toBeLessThanOrEqual(50);
    expect(size.height).toBeGreaterThanOrEqual(44);
    for (const offset of [0, 20, 40, 60, 80, 100, 140, 220]) {
      await page.evaluate(y => window.scrollTo(0, y), offset);
      try {
        await expect.poll(async () => intersects(await launch.boundingBox(), await upload.boundingBox()),
          {message: `${path} scrolled ${offset}px`}).toBe(false);
      } catch (error) {
        // Record the geometry so a failure in CI explains itself.
        console.log('launcher overlap', JSON.stringify(await page.evaluate(() => ({
          scrollY, innerHeight, bottom: document.querySelector('body > .assistant-launch').style.bottom,
          launch: document.querySelector('body > .assistant-launch').getBoundingClientRect().toJSON(),
          upload: document.querySelector('.study-primary').getBoundingClientRect().toJSON(),
          banner: Boolean(document.querySelector('.consent-banner:not([hidden])')),
        }))));
        throw error;
      }
    }
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect.poll(async () => intersects(await launch.boundingBox(), await upload.boundingBox())).toBe(false);
    if (path === '/') await page.screenshot({path: testInfo.outputPath('home-assistant-layout.jpg'), quality: 75});
  }
  // Keyboard and screen-reader use are unchanged.
  const launch = page.locator('body > .assistant-launch');
  await launch.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.assistant-dialog')).toBeVisible();
  await expect(page.locator('.assistant-dialog')).toHaveAccessibleName('LectureSift Assistant');
  await page.keyboard.press('Escape');
  await expect(launch).toBeFocused();
  await noHorizontalOverflow(page);
});

test('the workspace names My lessons once, as its tab', async ({page}, testInfo) => {
  await page.addInitScript(() => localStorage.setItem('lecturesift-currency', 'TRY'));
  await page.goto('/en/workspace.html');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#workspaceLibraryTab')).toHaveText('My lessons');
  await expect(page.locator('#workspaceStudyPanel').getByText('My lessons', {exact: true})).toHaveCount(0);
  await expect(page.locator('.workspace-intro-actions a')).toHaveCount(1);
  await expect(page.locator('.workspace-intro-actions a')).toHaveAttribute('href', /\/en\/features(?:\.html)?$/);
  await expect(page.locator('.public-try-link')).toBeHidden();
  await noHorizontalOverflow(page);
  await page.screenshot({path: testInfo.outputPath('polish-workspace-layout.jpg'), quality: 75});
});

test('plan buttons keep one-line labels and the account strip uses the button style', async ({page}, testInfo) => {
  await page.addInitScript(() => {
    localStorage.setItem('lecturesift-billing-token', 'ci-synthetic-token-not-valid-on-any-server');
    localStorage.setItem('lecturesift-currency', 'TRY');
  });
  await page.goto('/en/plans');
  await page.locator('[data-consent="essential"]').click();
  const actions = page.locator('[data-plan-card="lite"] .plan-card-actions .plan-action');
  await expect(actions).toHaveCount(2);
  await expect(actions.first()).toHaveText('Choose 30-day package');
  const layout = await actions.evaluateAll(nodes => nodes.map(node => {
    const range = document.createRange();
    range.selectNodeContents(node);
    const text = range.getBoundingClientRect();
    return {singleLine: text.height < parseFloat(getComputedStyle(node).fontSize) * 1.8, ...node.getBoundingClientRect().toJSON()};
  }));
  for (const button of layout) expect(button.singleLine).toBe(true);
  expect(layout[1].top).toBeGreaterThanOrEqual(layout[0].bottom);
  expect(Math.abs(layout[0].width - layout[1].width)).toBeLessThanOrEqual(1);

  const account = page.locator('#accountStatus .cta-login');
  await expect(account).toBeVisible();
  await expect(account).toHaveText('My account');
  const styles = await page.evaluate(() => {
    const pick = node => {
      const style = getComputedStyle(node);
      return {fontSize: style.fontSize, fontWeight: style.fontWeight, minHeight: style.minHeight,
        paddingInline: style.paddingInlineStart, radius: style.borderTopLeftRadius, decoration: style.textDecorationLine};
    };
    // The signed-out strip's sign-in link is the site's secondary button size.
    return {account: pick(document.querySelector('#accountStatus .cta-login')), signIn: pick(document.querySelector('#authForm .cta-login'))};
  });
  expect(styles.account).toEqual(styles.signIn);
  expect((await account.boundingBox()).height).toBeGreaterThanOrEqual(40);
  await noHorizontalOverflow(page);
  await page.locator('#accountPanel').scrollIntoViewIfNeeded();
  await page.screenshot({path: testInfo.outputPath('polish-plans-layout.jpg'), quality: 75});
  await actions.last().scrollIntoViewIfNeeded();
  await page.screenshot({path: testInfo.outputPath('polish-plus-layout.jpg'), quality: 75});
});

test('signed-out public pages offer a compact free trial next to sign-in', async ({page, isMobile}, testInfo) => {
  await page.addInitScript(() => localStorage.setItem('lecturesift-currency', 'TRY'));
  await page.goto('/en/');
  await page.locator('[data-consent="essential"]').click();
  const trial = page.locator('.public-try-link');
  const account = page.locator('#accountButton');
  const menu = page.locator('.public-menu-toggle');
  await expect(account).toHaveAttribute('data-session-state', 'signed-out');
  await expect(trial).toHaveText('Try free');
  await expect(trial).toHaveAttribute('href', /^\/en\/workspace(?:\.html)?\?source=upload$/);
  expect(await account.evaluate(node => node.nextElementSibling?.classList.contains('public-try-link'))).toBe(true);
  // Same primary colour as the page's own upload button.
  const primary = await page.locator('.study-primary').evaluate(node => getComputedStyle(node).backgroundColor);
  await expect(trial).toHaveCSS('background-color', primary);
  if (isMobile) {
    await expect(trial).toBeHidden();
    await menu.click();
    await expect(trial).toBeVisible();
    await expect(trial).toBeInViewport();
    await page.screenshot({path: testInfo.outputPath('polish-menu-layout.jpg'), quality: 75});
  } else {
    await expect(trial).toBeVisible();
    const [trialBox, accountBox] = [await trial.boundingBox(), await account.boundingBox()];
    expect(trialBox.height).toBeLessThanOrEqual(46);
    expect(Math.abs((trialBox.y + trialBox.height / 2) - (accountBox.y + accountBox.height / 2))).toBeLessThanOrEqual(2);
    expect(trialBox.x).toBeGreaterThan(accountBox.x);
    await page.screenshot({path: testInfo.outputPath('polish-home-layout.jpg'), quality: 75});
  }
  await noHorizontalOverflow(page);
  await trial.click();
  await expect(page).toHaveURL(/\/en\/workspace(?:\.html)?\?source=upload$/);
  await expect(page.locator('.public-try-link')).toBeHidden();

  await page.goto('/features');
  await expect(page.locator('.public-try-link')).toHaveText('Ücretsiz dene');
  if (isMobile) await menu.click();
  await expect(page.locator('.public-try-link')).toBeVisible();

  // Signed-in visitors already have an account, so the header does not offer the trial.
  await page.evaluate(() => localStorage.setItem('lecturesift-billing-token', 'ci-synthetic-token-not-valid-on-any-server'));
  await page.goto('/en/features');
  await expect(account).toHaveAttribute('data-session-state', 'signed-in');
  if (isMobile) await menu.click();
  await expect(account).toBeVisible();
  await expect(page.locator('.public-try-link')).toBeHidden();
});

test('the desktop trial button fits the bar or stays out of it in every language', async ({page}, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop-light', 'One bounded multi-language layout pass');
  test.setTimeout(120_000);
  await page.addInitScript(() => localStorage.setItem('lecturesift-currency', 'TRY'));
  let consented = false;
  for (const width of [1365, 1280]) {
    await page.setViewportSize({width, height: 900});
    for (const code of CODES) {
      await page.goto(home(code));
      if (!consented) {
        await page.locator('[data-consent="essential"]').click();
        consented = true;
      }
      const state = await page.evaluate(() => {
        const nav = document.querySelector('#publicNavigation');
        return {
          shown: nav.querySelector('.public-try-link').checkVisibility(),
          overflow: nav.scrollWidth - nav.clientWidth,
          leftOut: Boolean(document.querySelector('.public-try-overflow')),
        };
      });
      console.log(`trial ${code} ${width}px ${state.shown ? 'shown' : 'left out'} overflow=${state.overflow}`);
      if (state.shown) expect(state.overflow, `${code} at ${width}px`).toBeLessThanOrEqual(1);
      else expect(state.leftOut, `${code} at ${width}px`).toBe(true);
      if (width === 1365 && ['tr', 'en'].includes(code)) expect(state.shown, `${code} at ${width}px`).toBe(true);
      await noHorizontalOverflow(page);
    }
  }
});
