import {test, expect, JOB_ID} from './fixtures.mjs';

// Workspace conversion steps: the guest "save your result" band, the result
// feedback prompt, the phone upload shortcut and light-theme option titles.
// Every API answer here is a synthetic stub; nothing reaches a real server.
const API = 'https://api.lecturesift.com';
const LOCAL = 'http://127.0.0.1:4173';
const CORS = {
  'Access-Control-Allow-Origin':LOCAL,
  'Access-Control-Allow-Methods':'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers':'authorization, content-type',
};
const TOKEN = 'ci-synthetic-token-not-valid-on-any-server';

async function prepare(page, {plan = 'free', language = 'tr'} = {}) {
  await page.addInitScript(([token, language]) => {
    if (location.origin !== 'http://127.0.0.1:4173') return;
    localStorage.setItem('lecturesift-consent-v1', JSON.stringify({
      version:1, analytics:false, advertising:false, updated_at:new Date().toISOString(),
    }));
    localStorage.setItem('lecturesift-ui', language);
    localStorage.setItem('lecturesift-currency', 'TRY');
    localStorage.setItem('lecturesift-billing-token', token);
  }, [TOKEN, language]);
  await page.route(`${API}/billing/me`, route => route.fulfill({status:200, headers:CORS, json:{account:{
    user:{id:`synthetic-${plan}`, email:'browser-smoke@example.invalid', first_name:'', email_verified:true},
    plan:{code:plan}, remaining_minutes:plan === 'guest' ? 30 : 60, used_minutes:0, credit_minutes:0,
  }}}));
}

// Records every feedback POST; `respond` decides the synthetic answer.
async function captureFeedback(page, respond = () => ({status:200, json:{ok:true}})) {
  const sent = [];
  await page.route(`${API}/jobs/${JOB_ID}/feedback`, async route => {
    const request = route.request();
    if (request.method() === 'OPTIONS') return route.fulfill({status:204, headers:CORS});
    if (request.method() !== 'POST') return route.fallback();
    const body = request.postDataJSON();
    sent.push({body, authorization:request.headers().authorization});
    const {status, json} = respond(body, sent.length);
    await route.fulfill({status, headers:CORS, json});
  });
  return sent;
}

test('a guest sees the save-your-result step first, wired to claim the job after signup', async ({page}, testInfo) => {
  await prepare(page, {plan:'guest'});
  await page.goto(`/workspace.html?job=${JOB_ID}`);
  await expect(page.locator('#results')).toBeVisible();
  const band = page.locator('#guestSaveResult');
  await expect(band).toBeVisible();
  await expect(band).toContainText('Sonuçların 24 saat sonra silinir.');
  await expect(band).toContainText('Ücretsiz hesap aç: kaydet ve ayda 120 dakika kazan.');
  await expect(page.locator('#guestSaveRegister')).toHaveText('Ücretsiz hesap aç');
  await expect(page.locator('#guestSaveLogin')).toHaveText('Giriş yap');
  // It is the first thing in the result panel, above the download header; the
  // feedback prompt comes last, under the result body.
  expect(await page.evaluate(() => [...document.getElementById('results').children].map(node => node.id || node.className)))
    .toEqual(['guestSaveResult', 'result-header', 'documentSampleResult', 'result-tabs', 'result-body', 'jobFeedback']);
  // Signing up from here returns to this job; register.html sends the guest
  // token along, so the server moves the job to the new account.
  const register = new URL(await page.locator('#guestSaveRegister').getAttribute('href'), page.url());
  expect(register.pathname).toBe('/register.html');
  const back = new URL(register.searchParams.get('next'), page.url());
  expect(back.pathname).toBe('/workspace.html');
  expect(back.searchParams.get('job')).toBe(JOB_ID);
  const login = new URL(await page.locator('#guestSaveLogin').getAttribute('href'), page.url());
  expect(login.pathname).toBe('/login.html');
  expect(login.searchParams.get('next')).toBe(register.searchParams.get('next'));
  const primary = await page.locator('#guestSaveRegister').boundingBox();
  expect(primary.height).toBeGreaterThanOrEqual(44);
  await band.screenshot({path:testInfo.outputPath('guest-save-band.png')});
});

test('guest limits read as an invitation with free-account and plans links', async ({page}, testInfo) => {
  await prepare(page, {plan:'guest'});
  await page.goto('/workspace.html');
  await expect(page.locator('#analyzeButton')).toBeVisible();
  await page.evaluate(() => showError('Tek seferlik 30 dakikalık denemen kullanıldı.', 'LS-GUEST-05'));
  const box = page.locator('#errorBox');
  await expect(box).toBeVisible();
  await expect(box).toHaveAttribute('data-tone', 'guest');
  await expect(page.locator('#errorTitle')).toHaveText('Ücretsiz hesapla devam et');
  await expect(page.locator('#errorCode')).toContainText('LS-GUEST-05');
  await expect(page.locator('#guestTrialUsedSignup')).toHaveText('Ücretsiz hesap oluştur');
  await expect(page.locator('#guestTrialUsedSignup')).toHaveAttribute('href', '/register.html?next=%2Fworkspace.html');
  await expect(page.locator('#guestLimitPlans')).toHaveText('Planları incele');
  await expect(page.locator('#guestLimitPlans')).toHaveAttribute('href', '/plans');
  await box.screenshot({path:testInfo.outputPath('guest-limit-card.png')});
  // The media-length refusal keeps its retry button and signup link and adds the plans link.
  await page.evaluate(() => showError('Hesapsız deneme en fazla 30 dakikalık bir kaynakla kullanılabilir.', 'LS-GUEST-04'));
  await expect(box).toHaveAttribute('data-tone', 'guest');
  await expect(page.locator('#guestSourceLimitSignup')).toHaveText('Ücretsiz hesap oluştur');
  await expect(page.locator('#guestLimitPlans')).toHaveAttribute('href', '/plans');
  await expect(page.locator('#quotaRecoveryActions button')).toHaveCount(1);
  // An ordinary error returns to the alarm tone and title.
  await page.evaluate(() => showError('Dosya çok büyük.', 'LS-UPLOAD-02'));
  await expect(box).not.toHaveAttribute('data-tone', 'guest');
  await expect(page.locator('#errorTitle')).toHaveText('İşlem tamamlanamadı');
  await expect(page.locator('#guestLimitPlans')).toHaveCount(0);
});

test('the feedback prompt sends one thumb, then the optional comment with consent', async ({page}, testInfo) => {
  await prepare(page);
  const sent = await captureFeedback(page);
  await page.goto(`/workspace.html?job=${JOB_ID}`);
  await expect(page.locator('#results')).toBeVisible();
  await expect(page.locator('#guestSaveResult')).toBeHidden();
  const prompt = page.locator('#jobFeedback');
  await expect(prompt).toBeVisible();
  await expect(page.locator('#jobFeedbackTitle')).toHaveText('Bu paket işine yaradı mı?');
  await expect(page.locator('#jobFeedbackUp')).toHaveAccessibleName('Evet, işime yaradı');
  await expect(page.locator('#jobFeedbackDown')).toHaveAccessibleName('Hayır, işime yaramadı');
  await expect(page.locator('#jobFeedbackForm')).toBeHidden();

  await page.locator('#jobFeedbackUp').click();
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0].body).toEqual({rating:'up', comment:'', consent:false, locale:'tr'});
  expect(sent[0].authorization).toBe(`Bearer ${TOKEN}`);
  await expect(page.locator('#jobFeedbackUp')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#jobFeedbackDown')).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('#jobFeedbackStatus')).toHaveText('Teşekkürler! İstersen kısa bir yorum da ekle.');
  await expect(page.locator('#jobFeedbackForm')).toBeVisible();
  await expect(page.locator('#jobFeedbackComment')).toHaveAttribute('maxlength', '280');
  await expect(page.locator('#jobFeedbackConsent')).not.toBeChecked();
  await expect(page.locator('.job-feedback-consent')).toContainText('Yorumum sitede adımın baş harfleriyle gösterilebilir');

  await prompt.screenshot({path:testInfo.outputPath('feedback-prompt.png')});
  await page.locator('#jobFeedbackComment').fill('Quiz çok işime yaradı');
  await expect(page.locator('#jobFeedbackCount')).toHaveText('21/280');
  await page.locator('#jobFeedbackConsent').check();
  await page.locator('#jobFeedbackSend').click();
  await expect.poll(() => sent.length).toBe(2);
  expect(sent[1].body).toEqual({rating:'up', comment:'Quiz çok işime yaradı', consent:true, locale:'tr'});
  await expect(page.locator('#jobFeedbackStatus')).toHaveText('Teşekkürler, yorumun alındı.');
  await expect(page.locator('#jobFeedbackForm')).toBeHidden();

  // Coming back to the same result does not ask again.
  await page.reload();
  await expect(page.locator('#results')).toBeVisible();
  await expect(page.locator('#jobFeedbackStatus')).toHaveText('Teşekkürler, yorumun alındı.');
  await expect(page.locator('#jobFeedbackUp')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#jobFeedbackForm')).toBeHidden();
  expect(sent.length).toBe(2);
});

test('a refused feedback request shows a retry message and a 503 hides the prompt', async ({page}) => {
  await prepare(page, {language:'en'});
  let status = 429;
  const sent = await captureFeedback(page, () => ({status, json:{detail:{code:'LS-SEC-01', message:'Çok fazla deneme.'}}}));
  await page.goto(`/en/workspace.html?job=${JOB_ID}`);
  await expect(page.locator('#jobFeedback')).toBeVisible();
  await expect(page.locator('#jobFeedbackTitle')).toHaveText('Was this pack useful to you?');
  await page.locator('#jobFeedbackDown').click();
  await expect(page.locator('#jobFeedbackStatus')).toHaveText('The feedback could not be sent. Try again in a moment.');
  await expect(page.locator('#jobFeedbackDown')).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('#jobFeedbackForm')).toBeHidden();
  status = 503;
  await page.locator('#jobFeedbackDown').click();
  await expect(page.locator('#jobFeedback')).toBeHidden();
  expect(sent.map(item => item.body.locale)).toEqual(['en', 'en']);
});

test('on a phone a sticky upload button appears once the drop zone scrolls away', async ({page, isMobile}, testInfo) => {
  test.skip(!isMobile, 'The sticky bar is the narrow-screen layout');
  await prepare(page);
  await page.goto('/workspace.html');
  await expect(page.locator('#classicDropZone')).toBeVisible();
  const bar = page.locator('#mobileUploadBar');
  await expect(bar).toBeHidden();

  await page.locator('#analyzeButton').scrollIntoViewIfNeeded();
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await expect(bar).toBeVisible();
  await expect(page.locator('#mobileUploadButton')).toHaveText('Dosya yükle');
  const barBox = await bar.boundingBox();
  expect(barBox.x).toBe(0);
  expect(Math.round(barBox.y + barBox.height)).toBe(844);
  // The workspace hosts the assistant in its own tab, so no floating launcher
  // can cover the bar. The button still carries .primary-button, the class
  // the launcher's overlap check (assistant.js) lifts above on pages that
  // float one, and the bar announces each toggle with the layout event.
  await expect(page.locator('body > .assistant-launch')).toHaveCount(0);
  await expect(page.locator('#mobileUploadButton')).toHaveClass(/\bprimary-button\b/);
  expect(await page.evaluate(() => {
    let announced = 0;
    document.addEventListener('lecturesift:layout', () => { announced += 1; });
    document.getElementById('results').hidden = false;
    return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => {
      document.getElementById('results').hidden = true;
      requestAnimationFrame(() => requestAnimationFrame(() => resolve(announced)));
    })));
  })).toBe(2);
  await expect(bar).toBeVisible();
  // Nothing else fixed sits over the bar: the point under its button is the button.
  expect(await page.evaluate(() => {
    const box = document.getElementById('mobileUploadButton').getBoundingClientRect();
    return document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)?.closest('#mobileUploadButton') !== null;
  })).toBe(true);
  // The page grows by the bar's height, so the footer's own consent control
  // is still reachable above it once the reader scrolls on.
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  expect(await page.evaluate(() => {
    const manage = document.querySelector('.consent-manage');
    if (!manage) return 'absent';
    const control = manage.getBoundingClientRect();
    const bar = document.getElementById('mobileUploadBar').getBoundingClientRect();
    return control.bottom <= bar.top + 0.5 ? 'above' : 'covered';
  })).toBe('above');
  await page.screenshot({path:testInfo.outputPath('workspace-mobile-upload-bar.png')});
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);

  // An open result or an error takes the bar away; closing them brings it back.
  await page.evaluate(() => { document.getElementById('results').hidden = false; });
  await expect(bar).toBeHidden();
  await page.evaluate(() => { document.getElementById('results').hidden = true; });
  await expect(bar).toBeVisible();
  await page.evaluate(() => showError('Dosya çok büyük.', 'LS-UPLOAD-02'));
  await expect(page.locator('#errorBox')).toBeVisible();
  await expect(bar).toBeHidden();
  await page.locator('#closeError').click();
  await expect(bar).toBeVisible();

  // Tapping it opens the file picker and brings the drop zone back into view;
  // the bar then steps aside and the launcher settles back down.
  const chooser = page.waitForEvent('filechooser');
  await page.locator('#mobileUploadButton').click();
  expect((await chooser).isMultiple()).toBe(true);
  await expect.poll(() => page.evaluate(() => {
    const box = document.getElementById('classicDropZone').getBoundingClientRect();
    return box.top >= 0 && box.bottom <= window.innerHeight;
  })).toBe(true);
  await expect(bar).toBeHidden();
});

test('the sticky upload button never shows while a result is open', async ({page, isMobile}) => {
  test.skip(!isMobile, 'The sticky bar is the narrow-screen layout');
  await prepare(page);
  await page.goto(`/workspace.html?job=${JOB_ID}`);
  await expect(page.locator('#results')).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await expect.poll(() => page.evaluate(() => document.getElementById('classicDropZone').getBoundingClientRect().bottom < 0)).toBe(true);
  await expect(page.locator('#jobFeedback')).toBeVisible();
  await expect(page.locator('#mobileUploadBar')).toBeHidden();
});

test('workspace option titles keep AA contrast in the light theme and their dark colour', async ({page}, testInfo) => {
  await prepare(page);
  await page.goto('/workspace.html');
  await expect(page.locator('#includeSummary')).toBeAttached();
  const metrics = await page.evaluate(() => {
    const channels = value => (value.match(/[\d.]+/g) || []).map(Number);
    const luminance = ([r, g, b]) => {
      const linear = channel => { const c = channel / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
      return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
    };
    const backdrop = node => {
      for (let element = node; element; element = element.parentElement) {
        const color = channels(getComputedStyle(element).backgroundColor);
        if (color.length === 3 || (color.length === 4 && color[3] >= 0.99)) return color.slice(0, 3);
      }
      return [255, 255, 255];
    };
    const titles = [...document.querySelectorAll('.content-choice label b')];
    return {
      theme: document.documentElement.dataset.theme,
      colors: [...new Set(titles.map(title => getComputedStyle(title).color))],
      ratios: titles.map(title => {
        const foreground = luminance(channels(getComputedStyle(title).color).slice(0, 3));
        const background = luminance(backdrop(title));
        return (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05);
      }),
    };
  });
  expect(metrics.ratios.length).toBeGreaterThanOrEqual(5);
  if (testInfo.project.name.endsWith('-dark')) {
    expect(metrics.theme).toBe('dark');
    // The dark theme keeps its original title colour.
    expect(metrics.colors).toEqual(['rgb(216, 228, 245)']);
  } else {
    expect(metrics.theme).toBe('light');
    metrics.ratios.forEach(ratio => expect(ratio).toBeGreaterThanOrEqual(4.5));
  }
  await page.locator('.content-choice-grid').screenshot({path:testInfo.outputPath('workspace-content-choices.png')});
});
