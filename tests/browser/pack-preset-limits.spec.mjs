import {test, expect} from './fixtures.mjs';

const API = 'https://api.lecturesift.com';
const headers = {
  'Access-Control-Allow-Origin':'http://127.0.0.1:4173',
  'Access-Control-Allow-Methods':'GET,OPTIONS',
  'Access-Control-Allow-Headers':'authorization,content-type',
};
// Counts as /billing/me reports them; they mirror PLANS in lecturesift/billing.py.
const plans = {
  free:{code:'free', entitlements:{quiz_questions:10, flashcards:20}},
  plus:{code:'plus', entitlements:{quiz_questions:20, flashcards:40}},
  pro:{code:'pro', entitlements:{quiz_questions:30, flashcards:60}},
};

// The upload is captured in the page instead of being sent anywhere.
async function openWorkspace(page, {plan = null, accountReady = Promise.resolve()} = {}) {
  await page.addInitScript(registered => {
    localStorage.setItem('lecturesift-ui', 'en');
    localStorage.setItem('lecturesift-currency', 'TRY');
    if (registered) localStorage.setItem('lecturesift-billing-token', 'synthetic-preset-owner');
    const open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send, header = XMLHttpRequest.prototype.setRequestHeader;
    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
      this.syntheticUpload = method === 'POST' && url === 'https://api.lecturesift.com/jobs';
      return open.call(this, method, url, ...rest);
    };
    XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
      if (this.syntheticUpload && name.toLowerCase() === 'content-type') this.syntheticType = value;
      return header.call(this, name, value);
    };
    XMLHttpRequest.prototype.send = function(body) {
      if (!this.syntheticUpload) return send.call(this, body);
      window.syntheticUpload = {body, type:this.syntheticType};
    };
  }, Boolean(plan));
  if (plan) {
    await page.route(`${API}/billing/me`, async route => {
      if (route.request().method() === 'OPTIONS') return route.fulfill({status:204, headers});
      await accountReady;
      return route.fulfill({headers, json:{account:{user:{id:'synthetic-preset-owner', email:'presets@example.invalid'},
        remaining_minutes:120, plan, job_entitlements:plan.entitlements}}});
    });
  }
  await page.goto('/en/workspace.html');
  await page.locator('[data-consent="essential"]').click();
}

async function expectCounts(page, {quiz, cards, disabledQuiz, disabledCards}) {
  await expect(page.locator('#quizCount')).toHaveValue(quiz);
  await expect(page.locator('#cardCount')).toHaveValue(cards);
  await expect(page.locator('#quizCount option:disabled')).toHaveText(disabledQuiz);
  await expect(page.locator('#cardCount option:disabled')).toHaveText(disabledCards);
  await expect(page.locator('[data-pack-preset="exam"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-pack-preset="balanced"]')).toHaveAttribute('aria-pressed', 'false');
}

test('a free account choosing Exam submits the counts its plan allows', async ({page}) => {
  await openWorkspace(page, {plan:plans.free});
  await page.waitForFunction(() => billingAccount?.plan?.code === 'free');
  await page.locator('[data-pack-preset="exam"]').click();
  await expectCounts(page, {quiz:'10', cards:'20', disabledQuiz:['20', '30'], disabledCards:['40', '60']});
  await expect(page.locator('.content-choice:has(#quizCount) small')).toHaveText('Your plan allows up to 10 quiz questions.');
  await expect(page.locator('.content-choice:has(#cardCount) small')).toHaveText('Your plan allows up to 20 flashcards.');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);

  await page.locator('#classicFiles').setInputFiles({name:'chapter.pdf', mimeType:'application/pdf', buffer:Buffer.from('Synthetic selection only.')});
  await page.locator('#analyzeButton').click();
  await page.waitForFunction(() => Boolean(window.syntheticUpload));
  const submitted = await page.evaluate(async () => {
    const {body, type} = window.syntheticUpload;
    const form = await new Response(body, {headers:{'Content-Type':type}}).formData();
    return {quiz:form.get('quiz_count'), cards:form.get('flashcard_count')};
  });
  expect(submitted).toEqual({quiz:'10', cards:'20'});
});

test('a signed-out visitor gets the guest trial counts', async ({page}) => {
  await openWorkspace(page);
  await page.locator('[data-pack-preset="exam"]').click();
  await expectCounts(page, {quiz:'10', cards:'20', disabledQuiz:['20', '30'], disabledCards:['40', '60']});
});

for (const [name, disabledQuiz, disabledCards] of [['plus', ['30'], ['60']], ['pro', [], []]]) {
  test(`a ${name} plan keeps Exam at 20 questions and 40 cards when its entitlements load late`, async ({page}) => {
    let releaseAccount;
    const accountReady = new Promise(resolve => { releaseAccount = resolve; });
    try {
      await openWorkspace(page, {plan:plans[name], accountReady});
      // Chosen while the account is still loading, so the free counts apply first.
      await page.locator('[data-pack-preset="exam"]').click();
      await expectCounts(page, {quiz:'10', cards:'20', disabledQuiz:['20', '30'], disabledCards:['40', '60']});
      releaseAccount();
      await page.waitForFunction(plan => billingAccount?.plan?.code === plan, name);
      await expectCounts(page, {quiz:'20', cards:'40', disabledQuiz, disabledCards});
      await expect(page.locator('.content-choice:has(#quizCount) small')).toHaveText(
        disabledQuiz.length ? 'Your plan allows up to 20 quiz questions.' : 'Optional');
    } finally { releaseAccount(); }
  });
}

// Precise timestamps and speaker detection are Pro-and-up and bill four times
// the minutes; other plans see them disabled, and presets never turn them on.
test('precise transcript options stay off and disabled below Pro', async ({page}) => {
  await openWorkspace(page, {plan:plans.free});
  await page.waitForFunction(() => billingAccount?.plan?.code === 'free');
  await expect(page.locator('#transcriptTimestamps')).toBeDisabled();
  await expect(page.locator('#speakerDetection')).toBeDisabled();
  await expect(page.locator('#transcriptPlanHint')).toHaveText('On the Pro plan');
  await expect(page.locator('#transcriptTimestampsCost')).toHaveText('With this option on, minutes count 4×');
  await page.evaluate(() => {
    document.getElementById('transcriptTimestamps').checked = true;
    applyPackPreset('exam');
  });
  await expect(page.locator('#transcriptTimestamps')).not.toBeChecked();
  await expect(page.locator('#speakerDetection')).not.toBeChecked();
});

test('a pro plan can turn precise timestamps and speaker detection on', async ({page}) => {
  await openWorkspace(page, {plan:plans.pro});
  await page.waitForFunction(() => billingAccount?.plan?.code === 'pro');
  await expect(page.locator('#transcriptPlanHint')).toBeHidden();
  await expect(page.locator('#transcriptTimestamps')).toBeEnabled();
  await expect(page.locator('#speakerDetection')).toBeDisabled();
  await page.locator('#transcriptTimestampsControl').click();
  await expect(page.locator('#transcriptTimestamps')).toBeChecked();
  await expect(page.locator('#speakerDetection')).toBeEnabled();
  await page.locator('#speakerDetectionControl').click();
  await expect(page.locator('#speakerDetection')).toBeChecked();
});
