import {test, expect, JOB_ID} from './fixtures.mjs';

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
    await page.locator('#pageTitle').click();
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

test('workspace restores a synthetic result and scores the real quiz once', async ({page, context, isolatedNetwork}, testInfo) => {
  await context.addInitScript(() => {
    localStorage.setItem('lecturesift-billing-token', 'ci-synthetic-token-not-valid-on-any-server');
    localStorage.setItem('lecturesift-ui', 'en');
    localStorage.setItem('lecturesift-currency', 'TRY');
  });
  // Exercise the real account -> requested job -> result -> quiz rendering path.
  await page.goto(`/workspace.html?job=${JOB_ID}`);
  await expect(page.locator('#results')).toBeVisible();
  await expect(page.locator('#resultHeading')).toHaveText('Synthetic browser quiz');
  expect(isolatedNetwork.apiCalls).toContain(`/jobs/${JOB_ID}/result`);
  await themeControl(page, testInfo.project.use.colorScheme);
  await page.locator('#resultTab-quiz').click();
  await expect(page.locator('#pane-quiz')).toBeVisible();
  await expect(page.locator('#pane-summary')).toBeHidden();
  await expect(page.locator('#quizStatus')).toContainText('0/2');

  const first = page.locator('.quiz-item[data-question="0"]');
  await first.locator('[data-option="0"]').click();
  await expect(first.locator('[data-option="0"]')).toHaveClass(/selected wrong/);
  await expect(first.locator('[data-option="1"]')).toHaveClass(/correct/);
  await expect(first.locator('[data-option="0"]')).toBeDisabled();
  await expect(first.locator('[data-option="1"]')).toBeDisabled();
  await expect(page.locator('#quiz-feedback-0')).toBeFocused();
  await expect(page.locator('#quiz-feedback-0')).toContainText('Four is divisible by two.');
  await expect(page.locator('#quizStatus')).toContainText('0/2 · 1/2');
  await page.locator('.quiz-item[data-question="1"] [data-option="0"]').press('Enter');
  await expect(page.locator('#quizStatus')).toContainText('1/2 · 2/2');
  await page.locator('#resultTab-summary').click();
  await page.locator('#resultTab-quiz').click();
  await expect(page.locator('#quizStatus')).toContainText('1/2 · 2/2');
  await expect(page.locator('#errorBox')).toBeHidden();
  await noHorizontalOverflow(page);

  await page.locator('#resultTab-exam').click();
  await page.locator('#startExamButton').click();
  await expect(page.locator('#resultTab-quiz')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#quizStatus')).toContainText('0/2');
  await expect(page.locator('.quiz-option:disabled')).toHaveCount(0);
});

test('Arabic localized demo retains RTL keyboard navigation', async ({page}) => {
  await page.goto('/ar/');
  await expect(page.locator('html')).toHaveAttribute('lang', 'ar');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await page.locator('#demoSummaryTab').focus();
  await page.keyboard.press('ArrowLeft');
  await expect(page.locator('#demoQuizTab')).toBeFocused();
  await expect(page.locator('#demoQuiz')).toBeVisible();
  await page.locator('[data-demo-answer="0"]').click();
  await expect(page.locator('#demoFeedback')).toContainText('✓');
  await noHorizontalOverflow(page);
});
