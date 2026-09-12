import {test, expect, JOB_ID} from './fixtures.mjs';

const creditOffers = (currency='TRY') => ({available:true,currency,image:{available:true,credits:200},packs:[
  {code:'ai_1000',credits:1000,currency,amount_minor:currency==='JPY'?600:14900},
  {code:'ai_3000',credits:3000,currency,amount_minor:currency==='JPY'?1500:34900},
  {code:'ai_10000',credits:10000,currency,amount_minor:currency==='JPY'?4500:99900},
]});

test('assistant credit choices retain amount, currency and language through sign-in', async ({page}, testInfo) => {
  const cors={'Access-Control-Allow-Origin':'http://127.0.0.1:4173','Access-Control-Allow-Methods':'GET,OPTIONS','Access-Control-Allow-Headers':'authorization,content-type'};
  await page.addInitScript(()=>localStorage.setItem('lecturesift-currency','JPY'));
  await page.route('https://api.lecturesift.com/assistant/catalog?currency=JPY',route=>route.fulfill({status:200,headers:cors,json:creditOffers('JPY')}));
  await page.route('https://api.lecturesift.com/billing/plans?currency=JPY',route=>route.fulfill({status:200,headers:cors,json:{plans:[],assistant:creditOffers('JPY')}}));
  await page.goto('/ar/assistant.html');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('.assistant-shop-pack')).toHaveCount(3);
  const first=page.locator('.assistant-shop-pack').first();
  await expect(first).toHaveAttribute('href',/\/ar\/plans(?:\.html)?\?plan=ai_1000&interval=one_time#assistantCredits/);
  await expect(first).toContainText('¥');
  await expect(first).not.toContainText('.00');
  await page.locator('#assistantCreditShop').scrollIntoViewIfNeeded();
  await noHorizontalOverflow(page);
  await page.screenshot({path:testInfo.outputPath('assistant-credit-shop-layout.jpg'),quality:75});
  await first.click();
  await expect(page).toHaveURL(/\/ar\/login(?:\.html)?\?next=/);
  expect(new URL(page.url()).searchParams.get('next')).toBe('/plans.html?plan=ai_1000&interval=one_time');
  expect(await page.evaluate(()=>localStorage.getItem('lecturesift-currency'))).toBe('JPY');
});

test('referrals keep sharing, reward choices and coupons usable on narrow screens', async ({page}, testInfo) => {
  const cors={'Access-Control-Allow-Origin':'http://127.0.0.1:4173','Access-Control-Allow-Methods':'GET,POST,OPTIONS','Access-Control-Allow-Headers':'authorization,content-type'};
  const first='referral-2026-09-v2', renewal='referral-2026-09-renewal-v1';
  const terms=percent=>({percent,max_discount_minor:percent===10?5000:2500,currency:'TRY',valid_days:90,monthly_only:true});
  const id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const code='LSR-'+'A'.repeat(24);
  const summary={enabled:true,referral_code:code,referral_url:'https://lecturesift.com/register.html?ref='+code,
    reward_minutes:60,invitee_reward_minutes:30,monthly_invitation_cap:5,monthly_reserved_count:2,monthly_remaining_count:3,
    earned_minutes:60,pending_minutes:90,hold_days:14,history_limit:50,has_more_rewards:false,has_more_coupons:false,
    settlement:'admin_reconciliation_after_14_days',month_utc:'2026-09',coupon:terms(10),
    renewal:{reward_minutes:30,invitee_reward_minutes:0,monthly_per_invitee_cap:1,coupon:terms(5)},
    coupon_policies:{'referral-2026-09-v1':{TRY:terms(10)},[first]:{TRY:terms(10)},[renewal]:{TRY:terms(5)}},
    redemption_currencies:['TRY'],rewards:[{id,policy_version:first,kind:'first_purchase',role:'inviter',status:'pending',
      reward_choice:'minutes',inviter_minutes:60,invitee_minutes:30,coupon_currency:'TRY',coupon_currency_selected:true,
      coupon_percent:10,coupon_max_discount_minor:5000,created_at:'2026-09-08T12:00:00Z',pending_until:'2026-09-22T12:00:00Z'}],
    coupons:[{...terms(5),code:'LSC-'+'B'.repeat(24),status:'ready',expires_at:'2026-12-08T12:00:00Z'}]};
  await page.addInitScript(()=>{localStorage.setItem('lecturesift-billing-token','synthetic-referral-owner');localStorage.setItem('lecturesift-currency','TRY');});
  await page.route('https://api.lecturesift.com/billing/referrals',route=>route.fulfill({status:200,headers:cors,json:{ok:true,referrals:summary}}));
  await page.route('https://api.lecturesift.com/jobs?limit=30',route=>route.fulfill({status:200,headers:cors,json:{jobs:[]}}));
  await page.route('https://api.lecturesift.com/billing/me/refund-requests',route=>route.fulfill({status:200,headers:cors,json:{requests:[]}}));
  const choices=[];
  await page.route(`https://api.lecturesift.com/billing/referrals/rewards/${id}/choice`,async route=>{
    if(route.request().method()==='OPTIONS'){await route.fulfill({status:204,headers:cors});return;}
    const payload=route.request().postDataJSON();choices.push(payload);summary.rewards[0].reward_choice=payload.reward_choice;
    await route.fulfill({status:200,headers:cors,json:{ok:true,referrals:summary}});
  });
  await page.goto('/en/account.html#account-referrals');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#referralContent')).toBeVisible();
  await expect(page.locator('#referralLink')).toHaveValue(summary.referral_url);
  await expect(page.locator('#referralNoCode')).toBeHidden();
  await expect(page.locator('#referralReserved')).toHaveText('2 / 5');
  await expect(page.locator('#referralCoupons')).toContainText('5%');
  await page.locator('[data-referral-choice="coupon"]').click();
  await expect(page.locator('[data-referral-choice="coupon"]')).toHaveAttribute('aria-pressed','true');
  expect(choices).toEqual([{reward_choice:'coupon',currency:'TRY'}]);
  await page.locator('.referral-history-panel summary').click();
  await expect(page.locator('#referralHistory')).toBeVisible();
  await page.locator('.referral-explainer summary').click();
  await expect(page.locator('.referral-explainer')).toContainText('14 days');
  await page.locator('.referral-explainer summary').click();
  await page.locator('.referral-heading').scrollIntoViewIfNeeded();
  await noHorizontalOverflow(page);
  await page.screenshot({path:testInfo.outputPath('referral-account-layout.jpg'),quality:75});
});

test('workspace assistant tab preserves the lesson and supports keyboard navigation', async ({page}, testInfo) => {
  await page.addInitScript(() => {
    localStorage.setItem('lecturesift-billing-token','ci-synthetic-token-not-valid-on-any-server');
    localStorage.setItem('lecturesift-ui','en');
    localStorage.setItem('lecturesift-currency','TRY');
  });
  await page.goto(`/workspace.html?job=${JOB_ID}`);
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('#results')).toBeVisible();
  await page.locator('#workspaceAssistantTab').click();
  await expect(page.locator('#workspaceStudyPanel')).toBeHidden();
  await expect(page.locator('#workspaceAssistantPanel .assistant-page-chat')).toBeVisible();
  await expect(page.locator('#workspaceAssistantTab')).toHaveAttribute('aria-selected','true');
  const draft=page.locator('#workspaceAssistantPanel textarea');
  await draft.fill('Keep my question while I check my lesson.');
  await page.locator('#workspaceAssistantTab').focus();
  await page.keyboard.press('ArrowLeft');
  await expect(page.locator('#workspaceStudyTab')).toBeFocused();
  await expect(page.locator('#resultHeading')).toHaveText('Synthetic browser quiz');
  await page.keyboard.press('ArrowRight');
  await expect(draft).toHaveValue('Keep my question while I check my lesson.');
  await expect(page.locator('.assistant-launch')).toHaveCount(0);
  await page.locator('.workspace-mode-tabs').scrollIntoViewIfNeeded();
  await noHorizontalOverflow(page);
  await expect(page.locator('#workspaceAssistantPanel button[type=submit]')).toBeInViewport();
  await page.screenshot({path:testInfo.outputPath('workspace-assistant-tab-layout.jpg'),quality:75});
  await page.goto('/ar/workspace.html#assistant');
  await expect(page.locator('#workspaceAssistantPanel')).toBeVisible();
  await expect(page.locator('#workspaceAssistantTab')).toHaveAttribute('aria-selected','true');
  await page.locator('#workspaceAssistantTab').focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('#workspaceStudyTab')).toBeFocused();
  await noHorizontalOverflow(page);
});

test('permanent ad-free purchase explains its scope and disappears as a repeat purchase', async ({page}, testInfo) => {
  const cors={'Access-Control-Allow-Origin':'http://127.0.0.1:4173','Access-Control-Allow-Methods':'GET,OPTIONS','Access-Control-Allow-Headers':'authorization,content-type'};
  await page.addInitScript(() => {localStorage.setItem('lecturesift-currency','TRY');});
  await page.route('https://api.lecturesift.com/billing/plans?currency=TRY',route=>route.fulfill({status:200,headers:cors,json:{plans:[],ad_free:{code:'ad_free',kind:'one_time',display_price:{currency:'TRY',amount_minor:5990},entitlements:{minutes:0,ad_free:true}}}}));
  await page.goto('/en/plans.html');
  await page.locator('[data-consent="essential"]').click();
  const offer=page.locator('#adFreeAccess');
  await expect(offer).toContainText('Permanent ad-free access');
  await expect(offer).toContainText('59.90');
  await expect(offer).toContainText('No extra minutes or assistant credits');
  await expect(page.locator('.plan-card')).not.toContainText(['Payment Test']);
  await offer.scrollIntoViewIfNeeded();
  await noHorizontalOverflow(page);
  await page.screenshot({path:testInfo.outputPath('ad-free-offer-layout.jpg'),quality:75});
  await offer.getByRole('button').click();
  await expect(page).toHaveURL(/\/en\/login(?:\.html)?\?next=/);
  expect(new URL(page.url()).searchParams.get('next')).toBe('/plans.html?plan=ad_free&interval=one_time');
  await page.addInitScript(()=>localStorage.setItem('lecturesift-billing-token','synthetic-ad-free-owner'));
  await page.route('https://api.lecturesift.com/billing/me',route=>route.fulfill({status:200,headers:cors,json:{account:{user:{email:'synthetic@example.invalid'},plan:{code:'free'},remaining_minutes:60,permanent_ad_free:true}}}));
  await page.goto('/en/plans.html');
  await expect(page.locator('#adFreeAccess button')).toBeDisabled();
  await expect(page.locator('#adFreeAccess button')).toHaveText('Active on your account');
});

test('assistant page keeps credits visible and account actions under user control', async ({page}, testInfo) => {
  const cors={'Access-Control-Allow-Origin':'http://127.0.0.1:4173','Access-Control-Allow-Methods':'GET,POST,OPTIONS','Access-Control-Allow-Headers':'authorization,content-type'};
  await page.addInitScript(()=>localStorage.setItem('lecturesift-billing-token','synthetic-browser-token'));
  await page.route('https://api.lecturesift.com/assistant/catalog*',route=>route.fulfill({status:200,headers:cors,json:creditOffers()}));
  await page.route('https://api.lecturesift.com/assistant/wallet',route=>route.fulfill({status:200,headers:cors,json:{balance:1000}}));
  const requests=[];
  await page.route('https://api.lecturesift.com/assistant/chat',async route=>{
    if(route.request().method()==='OPTIONS'){await route.fulfill({status:204,headers:cors});return;}
    requests.push(route.request().postDataJSON());
    await route.fulfill({status:200,headers:cors,json:{
      answer:'Here is your current account summary.',action:'account',balance:998,charged_credits:2,
      account_summary:{plan_code:'plus',remaining_minutes:712,used_minutes:188,credit_minutes:0,assistant_credits:998,subscription:{status:'active',interval:'monthly',ends_at:'2026-10-08T12:00:00+00:00',cancel_at_period_end:false},recent_lessons:[{title:'Cell biology',status:'done'}]},
    }});
  });
  await page.goto('/en/assistant.html');
  await page.locator('[data-consent="essential"]').click();
  const chat=page.locator('.assistant-page-chat');
  await expect(chat).toBeVisible();
  await expect(page.locator('.assistant-launch')).toHaveCount(0);
  await expect(chat.locator('.assistant-balance')).toHaveText('Credits left: 1,000');
  await expect(chat.locator('.assistant-limit')).toContainText('Credits are used when a reply arrives');
  await expect(chat.locator('.assistant-details')).toHaveCount(0);
  await expect(chat.locator('textarea')).not.toBeFocused();
  await expect(chat.locator('button[type=submit]')).toBeInViewport();
  await chat.getByRole('button',{name:'Where can I see my plan?',exact:true}).click();
  await expect(chat.locator('textarea')).toHaveValue('Where can I see my plan?');
  await chat.locator('button[type=submit]').click();
  await expect(chat.locator('.assistant-balance')).toHaveText('Credits left: 998');
  await expect(chat.locator('.assistant-status')).toBeEmpty();
  await expect(page).toHaveURL(/\/en\/assistant\.html$/);
  expect(requests).toHaveLength(1);
  expect(requests[0].language).toBe('en');
  const summary=chat.locator('.assistant-account-summary');
  await expect(summary).toContainText('Current account summary');
  await expect(summary).toContainText('Plus');
  await expect(summary).toContainText('712');
  await expect(summary).toContainText('Cell biology');
  await expect(chat.locator('.assistant-message-charge')).toHaveText('This reply: 2 credits');
  const action=chat.locator('a.assistant-action').last();
  await expect(action).toHaveText('My account');
  await expect(action).toHaveAttribute('href',/\/en\/account(?:\.html)?$/);
  await expect(chat.locator('.assistant-credit-bar a')).toHaveAttribute('href','#assistantCreditShop');
  await noHorizontalOverflow(page);
  await page.locator('.assistant-page-intro').scrollIntoViewIfNeeded();
  await expect(chat.locator('button[type=submit]')).toBeInViewport();
  await page.screenshot({path:testInfo.outputPath('assistant-page-layout.jpg'),quality:75});
  await page.evaluate(()=>localStorage.removeItem('lecturesift-billing-token'));
  await chat.locator('textarea').fill('Changed session');
  await chat.locator('button[type=submit]').click();
  await expect(chat.locator('.assistant-balance')).not.toContainText('998');
  await expect(chat.locator('.assistant-message')).toHaveCount(0);
  expect(requests).toHaveLength(1);
});

test('assistant applies fixed preferences only after confirmation and ignores response paths', async ({page}) => {
  const cors={'Access-Control-Allow-Origin':'http://127.0.0.1:4173','Access-Control-Allow-Methods':'GET,POST,OPTIONS','Access-Control-Allow-Headers':'authorization,content-type'};
  await page.addInitScript(()=>{
    localStorage.setItem('lecturesift-billing-token','synthetic-browser-token');
    localStorage.setItem('lecturesift-theme','light');
  });
  await page.route('https://api.lecturesift.com/assistant/catalog*',route=>route.fulfill({status:200,headers:cors,json:creditOffers()}));
  await page.route('https://api.lecturesift.com/assistant/wallet',route=>route.fulfill({status:200,headers:cors,json:{balance:1000}}));
  await page.route('https://api.lecturesift.com/assistant/chat',async route=>{
    if(route.request().method()==='OPTIONS'){await route.fulfill({status:204,headers:cors});return;}
    const payload=route.request().postDataJSON();
    const latest=payload.message.includes('latest');
    const dark=payload.message.includes('dark');
    await route.fulfill({status:200,headers:cors,json:latest
      ? {answer:'Your latest lesson is ready.',action:'latest_lesson',path:'https://attacker.invalid/escape',latest_lesson:{job_id:'owned-latest-1',title:'Owned lesson',status:'done'},balance:998,charged_credits:2}
      : dark
        ? {answer:'I can propose dark mode.',action:'dark',path:'https://attacker.invalid/escape',balance:996,charged_credits:2}
        : {answer:'I can propose German for this interface.',action:'language_de',path:'https://attacker.invalid/escape',latest_lesson:{job_id:'foreign-job',title:'Foreign',status:'done'},balance:994,charged_credits:2}});
  });
  await page.goto('/en/assistant.html');
  await page.locator('[data-consent="essential"]').click();
  const chat=page.locator('.assistant-page-chat');

  await chat.locator('textarea').fill('Open my latest lesson');
  await chat.locator('button[type=submit]').click();
  const latest=chat.locator('a.assistant-action').last();
  await expect(latest).toHaveText('Open latest lesson');
  await expect(latest).toHaveAttribute('href',/\/en\/workspace(?:\.html)?\?job=owned-latest-1$/);
  await expect(latest).not.toHaveAttribute('href',/attacker/);

  await chat.locator('textarea').fill('Use dark theme');
  await chat.locator('button[type=submit]').click();
  let confirmation=chat.locator('.assistant-confirmation').last();
  await expect(confirmation).toContainText('Dark');
  await expect(page.locator('html')).toHaveAttribute('data-theme','light');
  await confirmation.getByRole('button',{name:'Cancel'}).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme','light');

  await chat.locator('textarea').fill('Use dark theme');
  await chat.locator('button[type=submit]').click();
  confirmation=chat.locator('.assistant-confirmation').last();
  await confirmation.getByRole('button',{name:'Apply'}).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
  expect(await page.evaluate(()=>localStorage.getItem('lecturesift-theme'))).toBe('dark');

  await chat.locator('textarea').fill('Use German');
  await chat.locator('button[type=submit]').click();
  confirmation=chat.locator('.assistant-confirmation').last();
  await expect(confirmation).toContainText('Deutsch');
  expect(await page.evaluate(()=>localStorage.getItem('lecturesift-ui'))).not.toBe('de');
  await confirmation.getByRole('button',{name:'Cancel'}).click();
  await expect(page).toHaveURL(/\/en\/assistant(?:\.html)?$/);
  expect(await page.evaluate(()=>localStorage.getItem('lecturesift-ui'))).not.toBe('de');

  await chat.locator('textarea').fill('Use German');
  await chat.locator('button[type=submit]').click();
  confirmation=chat.locator('.assistant-confirmation').last();
  await expect(confirmation).toContainText('Deutsch');
  await confirmation.getByRole('button',{name:'Apply'}).click();
  await expect(page).toHaveURL(/\/de\/assistant(?:\.html)?$/);
  expect(await page.evaluate(()=>localStorage.getItem('lecturesift-ui'))).toBe('de');
});

test('invitation discovery keeps the requested account section through sign-in', async ({page}) => {
  await page.goto('/en/');
  const invite=page.locator('.referral-promo');
  await expect(invite).toContainText('Signing up alone earns no reward.');
  await invite.getByRole('link',{name:'Explore invitations'}).click();
  await expect(page).toHaveURL(/\/en\/login\.html\?next=/);
  const next=new URL(page.url()).searchParams.get('next');
  expect(next).toBe('/en/account.html#account-referrals');
  await noHorizontalOverflow(page);
});

test('assistant page fits a narrow Arabic screen and tablet navigation', async ({page},testInfo) => {
  test.skip(testInfo.project.name!=='mobile-light','One bounded extra layout review');
  await page.setViewportSize({width:320,height:740});
  await page.goto('/ar/assistant.html');
  await page.locator('[data-consent="essential"]').click();
  await expect(page.locator('html')).toHaveAttribute('dir','rtl');
  await expect(page.locator('.assistant-page-chat')).toBeVisible();
  await expect(page.locator('.assistant-balance')).toHaveText('تخضع الخدمة لحدود استخدام.');
  await noHorizontalOverflow(page);
  await page.screenshot({path:testInfo.outputPath('assistant-arabic-layout.jpg'),quality:75});
  await page.setViewportSize({width:980,height:850});
  await page.goto('/de/assistant.html');
  await page.locator('.public-menu-toggle').click();
  await expect(page.locator('.public-nav-link[aria-current="page"]')).toHaveText('Assistent');
  await noHorizontalOverflow(page);
});

test('assistant guide opens, remains localized and does not claim live AI availability', async ({page}) => {
  await page.goto('/en/');
  await page.locator('.assistant-launch').click();
  const dialog=page.locator('.assistant-dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAccessibleName('LectureSift Assistant');
  await expect(dialog.locator('.assistant-status')).toContainText('not available yet');
  await expect(dialog.locator('.assistant-message')).toContainText('Create a free account');
  await expect(dialog.locator('a.assistant-action')).toHaveAttribute('href', /\/en\/register(?:\.html)?$/);
  await dialog.locator('textarea').fill('<img src=x onerror=alert(1)>');
  await dialog.locator('button[type=submit]').click();
  await expect(dialog.locator('img')).toHaveCount(0);
  await noHorizontalOverflow(page);
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(page.locator('.assistant-launch')).toBeFocused();
});

test('owned image creation shows its credit price and offers a safe download', async ({page}, testInfo) => {
  const cors={'Access-Control-Allow-Origin':'http://127.0.0.1:4173','Access-Control-Allow-Methods':'GET,POST,OPTIONS','Access-Control-Allow-Headers':'authorization,content-type'};
  await page.addInitScript(()=>localStorage.setItem('lecturesift-billing-token','synthetic-browser-token'));
  await page.route('https://api.lecturesift.com/assistant/catalog*',route=>route.fulfill({status:200,headers:cors,json:{available:true,image:{available:true,credits:200}}}));
  await page.route('https://api.lecturesift.com/assistant/wallet',route=>route.fulfill({status:200,headers:cors,json:{balance:1050}}));
  await page.goto('/en/');
  const syntheticImage=await page.evaluate(()=>{const canvas=document.createElement('canvas');canvas.width=1024;canvas.height=1024;const ctx=canvas.getContext('2d');ctx.fillStyle='#bad3f5';ctx.fillRect(0,0,1024,1024);return canvas.toDataURL('image/jpeg');});
  const requests=[];
  await page.route('https://api.lecturesift.com/assistant/image',async route=>{
    if(route.request().method()==='OPTIONS'){await route.fulfill({status:204,headers:cors});return;}
    requests.push(route.request().postDataJSON());
    await route.fulfill({status:200,headers:cors,json:{kind:'image',image:syntheticImage,balance:850,charged_credits:200,action:'none'}});
  });
  await page.locator('.assistant-launch').click();
  const dialog=page.locator('.assistant-dialog');
  await expect(dialog.locator('.assistant-mode')).toBeVisible();
  await expect(dialog.locator('[data-mode=image]')).toContainText('200');
  await dialog.locator('[data-mode=image]').click();
  await expect(dialog.locator('.assistant-attach')).toBeHidden();
  await dialog.locator('textarea').fill('Synthetic water cycle diagram');
  await dialog.locator('button[type=submit]').click();
  await expect(dialog.locator('.assistant-balance')).toContainText('850');
  await expect(dialog.locator('.assistant-message img')).toBeVisible();
  await expect(dialog.locator('a[download]')).toBeInViewport();
  await expect(dialog.locator('a[download]')).toHaveAttribute('download','lecturesift-image.jpg');
  await expect(dialog.locator('a[download]')).toHaveAttribute('href',/^data:image\/jpeg;base64,/);
  expect(requests).toHaveLength(1);
  expect(requests[0].prompt).toBe('Synthetic water cycle diagram');
  expect(Object.keys(requests[0]).sort()).toEqual(['prompt','request_id']);
  await noHorizontalOverflow(page);
  await dialog.screenshot({path:testInfo.outputPath('assistant-image-layout.jpg'),quality:75});
});

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
  await page.locator('#demoCardTab').click();
  await expect(page.locator('#demoCardAnswer')).toBeHidden();
  await page.locator('#demoReveal').click();
  await expect(page.locator('#demoCardAnswer')).toBeVisible();
  await expect(page.locator('#demoReveal')).toHaveAttribute('aria-expanded', 'true');
  await page.locator('#demoReveal').click();
  await expect(page.locator('#demoCardAnswer')).toBeHidden();
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
  await expect.poll(() => band.evaluate(img => img.complete && img.naturalWidth === 912)).toBe(true);
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
  await page.locator('.source-images').click();
  await expect(page).toHaveURL(/workspace(?:\.html)?$/);
  await expect(page.locator('#videoUrl, #linkTab, #linkPanel')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('YouTube');
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
  await page.locator('#assistantCredits').scrollIntoViewIfNeeded();
  await expect(page.locator('#assistantCredits')).toContainText('Credits are used when a reply arrives.');
  await expect(page.locator('#assistantCredits .assistant-pack-price').first()).toContainText('₺');
  await noHorizontalOverflow(page);
  await capture('assistant-credits-layout');
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


test('mobile offer numbers, descriptions and links stay inside separate card rows', async ({page, isMobile}, testInfo) => {
  test.skip(!isMobile, 'Regression concerns the narrow offer-card layout');
  for (const width of [320, 390]) {
    await page.setViewportSize({width, height:844});
    for (const locale of ['tr', 'ar']) {
      await page.goto(locale === 'tr' ? '/' : '/ar/');
      const consent = page.locator('[data-consent="essential"]');
      if (await consent.isVisible()) await consent.click();
      const skipLink = page.locator('.skip-link');
      await expect(skipLink).toHaveCSS('clip-path', 'inset(50%)');
      await skipLink.focus();
      await expect(skipLink).toHaveCSS('clip-path', 'none');
      await page.keyboard.press('Tab');
      await expect(skipLink).not.toBeFocused();
      const cards = page.locator('.campaign-card');
      await expect(cards).toHaveCount(3);
      await cards.first().scrollIntoViewIfNeeded();
      for (const card of await cards.all()) {
        const bounds = await card.boundingBox();
        let previousBottom = bounds.y;
        for (const child of await card.locator(':scope > *').all()) {
          const box = await child.boundingBox();
          expect(box.x).toBeGreaterThanOrEqual(bounds.x);
          expect(box.x + box.width).toBeLessThanOrEqual(bounds.x + bounds.width + 1);
          expect(box.y).toBeGreaterThanOrEqual(previousBottom);
          expect(box.y + box.height).toBeLessThanOrEqual(bounds.y + bounds.height);
          previousBottom = box.y + box.height;
        }
      }
      await noHorizontalOverflow(page);
      if (width === 320) await page.locator('.campaign-section').screenshot({path:testInfo.outputPath('offers-'+locale+'-layout.jpg'), quality:80});
    }
  }
});
