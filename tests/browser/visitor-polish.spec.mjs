import {test, expect, JOB_ID} from './fixtures.mjs';

// Visitor-facing fixes from the UX review: readable light-theme checkout and
// results, the folded workspace settings, guest-only trial copy, the register
// card, payment methods, the phone comparison table and small polish items.
const API = 'https://api.lecturesift.com';
const CORS = {'Access-Control-Allow-Origin':'http://127.0.0.1:4173'};
const signedIn = page => page.addInitScript(() => {
  localStorage.setItem('lecturesift-billing-token', 'ci-synthetic-token-not-valid-on-any-server');
  localStorage.setItem('lecturesift-currency', 'TRY');
});
const below = (upper, lower) => lower.y >= upper.y + upper.height - 1;
const intersects = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

test('the workspace folds optional settings so the analyze button follows the source', async ({page}) => {
  await page.goto('/en/workspace.html');
  await page.locator('[data-consent="essential"]').click();
  const settings = page.locator('#workspaceSettings');
  await expect(settings).not.toHaveAttribute('open', '');
  await expect(settings.locator('summary h2')).toHaveText('Settings (optional)');
  await expect(page.locator('[data-pack-preset="balanced"]')).toBeHidden();
  const drop = await page.locator('#classicDropZone').boundingBox();
  const analyze = await page.locator('#analyzeButton').boundingBox();
  expect(analyze.y - (drop.y + drop.height)).toBeLessThan(600);
  // The guest trial note is for visitors without an account.
  await expect(page.locator('.rollout-guest-note[data-guest-trial-note]')).toContainText('No-account trial');
  await settings.locator('summary').click();
  await expect(page.locator('[data-pack-preset="balanced"]')).toBeVisible();
  await expect(page.locator('#formatPdf')).toBeChecked();
});

test('a signed-in workspace shows remaining minutes, and a linked pack opens above the form', async ({page}) => {
  await signedIn(page);
  await page.goto(`/en/workspace.html?job=${JOB_ID}`);
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#results')).toBeVisible();
  await expect(page.locator('.rollout-guest-note[data-guest-trial-note]')).toHaveText('Processing time left: 60 minutes');
  await expect(page.getByText('No-account trial')).toHaveCount(0);
  expect(await page.evaluate(() => Boolean(document.querySelector('#results')
    .compareDocumentPosition(document.querySelector('#workspace')) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
  const results = await page.locator('#results').boundingBox();
  const workspace = await page.locator('#workspace').boundingBox();
  expect(below(results, workspace)).toBe(true);
});

test('flashcards keep the question and the answer cue apart, with one-line buttons', async ({page}) => {
  await signedIn(page);
  await page.route(`${API}/jobs/${JOB_ID}/result`, route => route.request().method() === 'OPTIONS' ? route.fallback() : route.fulfill({headers:CORS, json:{
    title:'Synthetic flashcards', job_type:'study_pack', summary:'A deterministic local fixture.',
    options:{include_summary:true, include_transcript:false, include_slides:false, quiz_count:0, flashcard_count:2},
    quiz:[], artifacts:[], slides:[], transcript_segments:[],
    flashcards:[{front:'What is a synapse?', back:'The junction between two neurons.'}, {front:'Second card', back:'Second answer'}],
  }}));
  await page.goto(`/en/workspace.html?job=${JOB_ID}`);
  await page.locator('[data-consent="essential"]').click();
  await page.locator('.result-tab[data-pane="cards"]').click();
  const question = await page.locator('#activeFlash strong').boundingBox();
  const cue = await page.locator('#activeFlash small').boundingBox();
  expect(below(question, cue)).toBe(true);
  const buttons = await page.locator('.flash-actions button').evaluateAll(nodes => nodes.map(node => {
    const range = document.createRange();
    range.selectNodeContents(node);
    return range.getBoundingClientRect().height < parseFloat(getComputedStyle(node).fontSize) * 1.8;
  }));
  expect(buttons).toEqual([true, true, true, true]);
});

test('the register card keeps the privacy line whole and the terms apart from the password rule', async ({page, isMobile}) => {
  await page.goto('/en/register.html');
  await page.locator('[data-consent="essential"]').click();
  const privacy = page.locator('.privacy-note');
  await expect(privacy).toHaveCSS('display', 'block');
  await expect(page.locator('label:has(#terms)')).toHaveText('I have read and accept the terms of use. Read the terms');
  await expect(page.locator('#passwordHelp')).toHaveText('At least 10 characters, including an uppercase letter, a lowercase letter and a number.');
  await expect(page.locator('#chosenPlan')).toBeHidden();
  if (!isMobile) return;
  // The floating assistant moves above the sign-in link instead of covering it.
  const signIn = page.locator('.auth-card .inline-links a');
  await signIn.scrollIntoViewIfNeeded();
  await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight));
  const launch = page.locator('.assistant-launch');
  await expect(launch).toBeVisible();
  await expect.poll(async () => intersects(await launch.boundingBox(), await signIn.boundingBox())).toBe(false);
});

test('a free account leads with the plan upgrade and greets by first name or shows the email once', async ({page}) => {
  await signedIn(page);
  const base = {user:{id:'synthetic-account', email:'student@example.invalid', email_verified:true},
    plan:{code:'free'}, remaining_minutes:60, used_minutes:0, credit_minutes:0};
  let account = {...base, user:{...base.user, first_name:'Deniz'}};
  await page.route(`${API}/**`, async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === 'OPTIONS') return route.fulfill({status:204, headers:{...CORS,
      'Access-Control-Allow-Methods':'GET,POST,OPTIONS', 'Access-Control-Allow-Headers':'authorization, content-type'}});
    if (path === '/billing/me') return route.fulfill({headers:CORS, json:{account}});
    if (path === '/billing/referrals') return route.fulfill({headers:CORS, json:{ok:true, referrals:{enabled:false}}});
    if (path === '/billing/me/refund-requests') return route.fulfill({headers:CORS, json:{requests:[]}});
    return route.fallback();
  });
  await page.goto('/en/account.html');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#accountName')).toHaveText('Deniz');
  await expect(page.locator('#accountEmail')).toHaveText('student@example.invalid');
  await expect(page.locator('#managePlanContinue')).toHaveText('Upgrade to Student');
  await expect(page.locator('#managePlanContinue')).toHaveClass(/\bprimary-action\b/);
  await expect(page.locator('#buyMinutesLink')).toHaveClass(/\bsecondary-action\b/);
  account = base;
  await page.reload();
  await expect(page.locator('#accountName')).toHaveText('student@example.invalid');
  await expect(page.locator('#accountEmail')).toBeHidden();
  await expect(page.getByText('student@example.invalid', {exact:true})).toHaveCount(1);
});

test('plans list only the payment methods that are switched on', async ({page}) => {
  await page.addInitScript(() => localStorage.setItem('lecturesift-currency', 'TRY'));
  await page.goto('/en/plans.html');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#plansGrid')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('#paymentMethodsStatus')).toHaveText('Online payment is not available right now. Please try again shortly or write to us.');
  await expect(page.locator('.payment-methods .intro')).toBeHidden();
  for (const id of ['#cardMethod', '#protectedBankMethod', '#bankMethod']) await expect(page.locator(id)).toBeHidden();
  await page.route(`${API}/billing/providers`, route => route.request().method() === 'OPTIONS' ? route.fallback()
    : route.fulfill({headers:CORS, json:{commerce_identity:{configured:true}, providers:[{code:'iyzico', configured:true, currencies:['TRY'], capabilities:['cards']}]}}));
  await page.reload();
  await expect(page.locator('#cardMethod')).toBeVisible();
  await expect(page.locator('#paymentMethodsStatus')).toBeHidden();
  await expect(page.locator('.payment-methods .intro')).toHaveText('Below are the payment methods you can use right now, each with when your package starts.');
  for (const id of ['#protectedBankMethod', '#bankMethod']) await expect(page.locator(id)).toBeHidden();
  await expect(page.locator('.payment-methods')).not.toContainText('not available yet');
});

test('the comparison table keeps its row labels in view and says when it scrolls', async ({page, isMobile}) => {
  await page.addInitScript(() => localStorage.setItem('lecturesift-currency', 'TRY'));
  await page.goto('/en/plans.html');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#compareBody tr').first()).toBeVisible();
  const hint = page.locator('.compare-scroll-hint');
  const frame = page.locator('.compare-scroll');
  if (!isMobile) {
    await expect(hint).toBeHidden();
    await expect(frame).toHaveAttribute('data-scroll-end', 'true');
    return;
  }
  await expect(hint).toHaveText('Swipe the table sideways to see every plan.');
  await expect(frame).toHaveAttribute('data-scroll-end', 'false');
  const label = page.locator('#compareBody tr:first-child td:first-child');
  const before = await label.boundingBox();
  await page.locator('.compare-wrap').evaluate(node => { node.scrollLeft = node.scrollWidth; });
  await expect(frame).toHaveAttribute('data-scroll-end', 'true');
  expect(Math.abs((await label.boundingBox()).x - before.x)).toBeLessThanOrEqual(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test('Turkish upper-case labels keep the brand as LECTURESIFT', async ({page}) => {
  await page.addInitScript(() => localStorage.setItem('lecturesift-ui', 'tr'));
  await page.goto('/features.html');
  const eyebrow = page.locator('.detail-hero .eyebrow').first();
  await expect(eyebrow).toHaveText('LECTURESIFT ÖZELLİKLERİ', {useInnerText:true});
  await page.goto('/register.html');
  await expect(page.locator('.auth-card .eyebrow')).toHaveText('LECTURESIFT HESABI', {useInnerText:true});
});

test('the 404 header keeps the logo first and the feature guide links keep titles on their own line', async ({page}) => {
  await page.goto('/404.html');
  const brand = await page.locator('.auth-topbar .brand').boundingBox();
  const language = await page.locator('.auth-topbar .language-switcher').boundingBox();
  expect(brand.x).toBeLessThan(language.x);
  await page.goto('/en/features');
  const link = page.locator('.feature-deep-links a').first();
  expect(below(await link.locator('strong').boundingBox(), await link.locator('span').boundingBox())).toBe(true);
});
