import {test, expect} from './fixtures.mjs';

const API = 'https://api.lecturesift.com';
const CORS = {
  'Access-Control-Allow-Origin':'http://127.0.0.1:4173',
  'Access-Control-Allow-Methods':'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers':'authorization, content-type',
};
const user = {
  id:'11111111-1111-4111-8111-111111111111', name:'Örnek Öğrenci',
  first_name:'Örnek', last_name:'Öğrenci', email:'student@example.invalid',
  email_verified:true, plan_code:'plus', credit_minutes:90, total_usage_minutes:12,
  created_at:'2026-10-01T10:00:00Z', country_code:'TR', preferred_language:'tr',
};
const pagination = {page:1, page_size:50, total:1, total_pages:1};
const advertising = {
  adsense:{site_approval_confirmed:false, consent_setup_confirmed:false, management_api:{
    status:'connected', connected:true, checked_at:'2026-10-08T00:00:00Z',
    account:{state:'READY', pending_task_count:0},
    site:{domain:'lecturesift.com', state:'NEEDS_ATTENTION', auto_ads_enabled:true},
    alerts:{total:2, info:0, warning:2, severe:0, types:['adsense-onboarding-incomplete', 'ua-conflict-policy-update']},
    policy_issues:null, policy_error_code:'provider_unavailable',
  }},
  google_ads:{management_api:{status:'not_configured', connected:false}},
};

async function openAdmin(page, {override, saved = false, hash = 'overview', theme} = {}) {
  const responses = new Map([
    ['/billing/admin/overview?limit=250', {counts:{users:1, verified_users:1, active_subscriptions:1}, users:[user], orders:[], plan_distribution:{plus:1}, revenue_by_currency:{}}],
    ['/admin/instagram-rewards?status=', {rewards:[]}],
    ['/billing/admin/refund-requests', {requests:[]}],
    ['/billing/admin/credit-events?limit=250', {events:[]}],
    ['/billing/admin/contact-messages?limit=250', {messages:[]}],
    ['/billing/admin/jobs?limit=250', {jobs:[]}],
    ['/billing/admin/account-events?limit=250', {events:[]}],
    ['/billing/health', {database:{connected:true, persistent:true}, email_delivery_configured:true, commerce_identity:{configured:true}, payments:{iyzico:{configured:true, webhook_signature:{required:true}}}}],
    ['/rollout/health', {durable_processing_ready:true, queue:{connected:true}, worker:{workers:1}, storage:{connected:true}}],
    ['/ads/config', {enabled:false, adsense_auto_ads:{enabled:false}, house_campaign:{enabled:true}}],
    ['/analytics/config', {enabled:false}],
    ['/billing/admin/advertising-readiness', advertising],
    ['/billing/admin/costs?days=30&limit=250', {}],
    ['/billing/admin/referrals', {rewards:[], has_more:false, limit:100}],
    ['/billing/admin/users?search=&verification=all&plan=all&sort=created_desc&page=1&page_size=50', {users:[user], pagination}],
    ['/billing/admin/orders?search=&status=all&provider=all&page=1&page_size=50', {orders:[], pagination:{...pagination, total:0}}],
    [`/billing/admin/users/${user.id}/entitlements`, {registered:true, assistant_credits:900, assistant_available:true, permanent_ad_free:false}],
    [`/billing/admin/users/${user.id}/activity?limit=30`, {activity:[]}],
  ]);
  const unexpected = [];
  await page.addInitScript(({saved, theme}) => {
    localStorage.setItem('lecturesift-ui', 'tr');
    if (theme) localStorage.setItem('lecturesift-theme', theme);
    if (saved) sessionStorage.setItem('lecturesift-admin-session-token', 'synthetic-admin-token');
  }, {saved, theme});
  await page.route(`${API}/**`, async route => {
    const request = route.request();
    const url = new URL(request.url());
    const key = url.pathname + url.search;
    if (request.method() === 'OPTIONS') {
      await route.fulfill({status:204, headers:CORS});
      return;
    }
    if (override && await override(route, url)) return;
    if (request.method() !== 'GET' || !responses.has(key)) {
      unexpected.push(`${request.method()} ${url.pathname}`);
      await route.abort('blockedbyclient');
      return;
    }
    await route.fulfill({status:200, headers:CORS, json:responses.get(key)});
  });
  await page.goto(`/admin.html#admin-${hash}`);
  await page.locator('[data-consent="essential"]').click();
  if (!saved) {
    await page.locator('#adminToken').fill('synthetic-admin-token');
    await page.locator('#adminLoginButton').click();
  }
  return unexpected;
}

test('a pending advertising provider does not block the panel or user records', async ({page}) => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const unexpected = await openAdmin(page, {hash:'users', override:async (route, url) => {
    if (url.pathname !== '/billing/admin/advertising-readiness') return false;
    await pending;
    await route.fulfill({status:200, headers:CORS, json:advertising});
    return true;
  }});
  try {
    await expect(page.locator('#adminPanel')).toBeVisible();
    await expect(page.locator('#adminUserList')).toContainText(user.email);
    await expect(page.locator('#adminRefresh')).toBeDisabled();
  } finally { release(); }
  await expect(page.locator('#adminRefresh')).toBeEnabled();
  await page.locator('[data-admin-view-button="growth"]').click();
  await expect(page.locator('#adminAdSenseSummary')).toContainText('Site onayı için düzeltme gerekiyor');
  await expect(page.locator('#adminAdSenseSummary')).toContainText('genel bir duyurudur');
  await expect(page.locator('#adminAdSenseSummary')).not.toContainText('reklam yayını açık');
  expect(unexpected).toEqual([]);
});

test('a failed section stays unavailable and can recover without another login', async ({page}) => {
  let fail = true;
  const unexpected = await openAdmin(page, {hash:'support', override:async (route, url) => {
    if (url.pathname !== '/billing/admin/contact-messages') return false;
    await route.fulfill({status:fail ? 503 : 200, headers:CORS, json:fail ? {detail:{message:'Geçici kesinti'}} : {messages:[]}});
    return true;
  }});
  await expect(page.locator('#adminRefresh')).toBeEnabled();
  await expect(page.locator('#adminDataStatus')).toContainText('Destek mesajları');
  await expect(page.locator('#adminContactMessages')).toContainText('yüklenemedi');
  await expect(page.locator('#adminExportMessages')).toBeDisabled();
  await expect(page.locator('#adminNewMessages')).toHaveText('—');
  expect(await page.evaluate(() => sessionStorage.getItem('lecturesift-admin-session-token'))).toBe('synthetic-admin-token');
  fail = false;
  await page.locator('[data-admin-retry]').click();
  await expect(page.locator('#adminDataStatus')).toBeHidden();
  await expect(page.locator('#adminContactMessages')).toContainText('Henüz iletişim mesajı yok');
  await expect(page.locator('#adminExportMessages')).toBeEnabled();
  expect(unexpected).toEqual([]);
});

test('late search responses cannot replace the current filter or overview history', async ({page}) => {
  let release;
  let olderStarted = false;
  const older = new Promise(resolve => { release = resolve; });
  const unexpected = await openAdmin(page, {hash:'users', override:async (route, url) => {
    const search = url.searchParams.get('search');
    if (url.pathname !== '/billing/admin/users' || !search) return false;
    if (search === 'older') { olderStarted = true; await older; }
    await route.fulfill({status:200, headers:CORS, json:{users:[{...user, name:search, email:`${search}@example.invalid`}], pagination}});
    return true;
  }});
  await expect(page.locator('#adminRefresh')).toBeEnabled();
  await page.locator('#adminUserSearch').fill('older');
  await expect.poll(() => olderStarted).toBe(true);
  await page.locator('#adminUserSearch').fill('newer');
  await expect(page.locator('#adminUserList')).toContainText('newer@example.invalid');
  const oldResponse = page.waitForResponse(response => response.url().includes('search=older'));
  release();
  await oldResponse;
  await expect(page.locator('#adminUserList')).not.toContainText('older@example.invalid');
  await page.locator('[data-admin-view-button="overview"]').click();
  await page.locator('#adminTimelineFilter').selectOption('user');
  await expect(page.locator('#adminTimeline')).toContainText(user.email);
  await expect(page.locator('#adminTimeline')).not.toContainText('newer@example.invalid');
  expect(unexpected).toEqual([]);
});

test('request timeouts release loading controls and preserve the admin session', async ({page}) => {
  await page.clock.install();
  let release;
  let providerStarted = false;
  const blocked = new Promise(resolve => { release = resolve; });
  const unexpected = await openAdmin(page, {saved:true, override:async (route, url) => {
    if (url.pathname !== '/billing/admin/advertising-readiness') return false;
    providerStarted = true;
    await blocked;
    await route.fulfill({status:200, headers:CORS, json:advertising}).catch(() => {});
    return true;
  }});
  try {
    await expect(page.locator('#adminUserList')).toContainText(user.email);
    await expect.poll(() => providerStarted).toBe(true);
    await page.clock.runFor(20_100);
    await expect(page.locator('#adminRefresh')).toBeEnabled();
    await expect(page.locator('#adminDataStatus')).toContainText('AdSense ve Google Ads');
    await expect(page.locator('#adminPanel')).toBeVisible();
    expect(await page.evaluate(() => sessionStorage.getItem('lecturesift-admin-session-token'))).toBe('synthetic-admin-token');
  } finally { release(); }
  expect(unexpected).toEqual([]);
});

test('a real authentication rejection closes the panel, but a server failure keeps the key', async ({page}) => {
  let status = 503;
  const unexpected = await openAdmin(page, {saved:true, override:async (route, url) => {
    if (url.pathname !== '/billing/admin/overview') return false;
    await route.fulfill({status, headers:CORS, json:{detail:{message:'Geçici kesinti'}}});
    return true;
  }});
  await expect(page.locator('#adminNotice')).toContainText('Geçici kesinti');
  expect(await page.evaluate(() => sessionStorage.getItem('lecturesift-admin-session-token'))).toBe('synthetic-admin-token');
  status = 401;
  await page.locator('#adminLoginButton').click();
  await expect(page.locator('#adminNotice')).toContainText('Yönetici erişimi doğrulanamadı');
  await expect(page.locator('#adminPanel')).toBeHidden();
  expect(await page.evaluate(() => sessionStorage.getItem('lecturesift-admin-session-token'))).toBeNull();
  expect(unexpected).toEqual([]);
});

test('automatic refresh pauses for edits, resumes after closing the form, and logout clears access', async ({page}) => {
  await page.clock.install();
  let overviewReads = 0;
  const unexpected = await openAdmin(page, {hash:'users', override:async (_route, url) => {
    if (url.pathname === '/billing/admin/overview') overviewReads += 1;
    return false;
  }});
  await expect(page.locator('#adminRefresh')).toBeEnabled();
  await page.locator('[data-user-open]').first().click();
  await page.locator('[data-user-profile-form] input[name="first_name"]').fill('Taslak');
  await page.clock.runFor(60_100);
  await expect(page.locator('[data-user-profile-form] input[name="first_name"]')).toHaveValue('Taslak');
  expect(overviewReads).toBe(1);
  await page.locator('#adminUserDialog .admin-dialog-close').click();
  await page.clock.runFor(60_100);
  await expect.poll(() => overviewReads).toBe(2);
  await expect(page.locator('#adminRefresh')).toBeEnabled();
  await page.locator('#adminLogout').click();
  await expect(page.locator('#adminPanel')).toBeHidden();
  expect(await page.evaluate(() => sessionStorage.getItem('lecturesift-admin-session-token'))).toBeNull();
  expect(unexpected).toEqual([]);
});

test('support status failures and late replies preserve the active response draft', async ({page}) => {
  const message = {id:'support-example', name:user.name, email:user.email, topic:'Örnek destek', message:'Ders dosyam hakkında', status:'new'};
  const base = '/billing/admin/contact-messages';
  let failStatus = true;
  let reads = 0;
  let replyStarted = false;
  let releaseReply;
  const pendingReply = new Promise(resolve => { releaseReply = resolve; });
  const unexpected = await openAdmin(page, {hash:'support', override:async (route, url) => {
    if (url.pathname === base) {
      await route.fulfill({status:200, headers:CORS, json:{messages:[message]}});
    } else if (url.pathname === `${base}/${message.id}`) {
      reads += 1;
      await route.fulfill({status:200, headers:CORS, json:{message, replies:[]}});
    } else if (url.pathname === `${base}/${message.id}/status`) {
      if (!failStatus) message.status = route.request().postDataJSON().status;
      await route.fulfill({status:failStatus ? 503 : 200, headers:CORS, json:failStatus ? {detail:{message:'Durum güncellenemedi'}} : {message}});
    } else if (url.pathname === `${base}/${message.id}/reply`) {
      replyStarted = true;
      await pendingReply;
      await route.fulfill({status:200, headers:CORS, json:{message, replies:[{direction:'admin', body:'İlk yanıt', delivery_status:'sent'}], notice:'Yanıt gönderildi'}});
    } else return false;
    return true;
  }});
  await expect(page.locator('#adminRefresh')).toBeEnabled();
  await page.locator('[data-contact-open]').click();
  const draft = page.locator('[data-contact-reply-form] textarea');
  await draft.fill('İlk yanıt');
  await page.locator('[data-contact-dialog-status]').click();
  await expect(page.locator('.admin-dialog-notice')).toContainText('Durum güncellenemedi');
  await expect(draft).toHaveValue('İlk yanıt');
  expect(reads).toBe(1);
  failStatus = false;
  await page.locator('[data-contact-dialog-status]').click();
  await expect(page.locator('[data-contact-dialog-status]')).toHaveText('Konuşmayı yeniden aç');
  await expect(draft).toHaveValue('İlk yanıt');
  expect(reads).toBe(2);
  try {
    await page.locator('[data-contact-reply-form] button[type="submit"]').click();
    await expect.poll(() => replyStarted).toBe(true);
    await page.locator('#adminContactDialog .admin-dialog-close').click();
    await page.locator('[data-contact-open]').click();
    await draft.fill('Yeni konuşma taslağı');
    const response = page.waitForResponse(item => item.url().endsWith(`/${message.id}/reply`));
    releaseReply();
    await response;
    await expect(page.locator('#adminContactMessages')).toContainText('1 yanıt');
    await expect(draft).toHaveValue('Yeni konuşma taslağı');
  } finally { releaseReply(); }
  expect(unexpected).toEqual([]);
});

test('admin advertising layout stays readable in each viewport and theme', async ({page}, testInfo) => {
  const theme = testInfo.project.name.endsWith('dark') ? 'dark' : 'light';
  const unexpected = await openAdmin(page, {hash:'growth', theme});
  await expect(page.locator('#adminRefresh')).toBeEnabled();
  await expect(page.locator('#adminAdSenseSummary')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({path:testInfo.outputPath('admin-growth-layout.jpg'), type:'jpeg', quality:75, fullPage:true});
  await page.locator('[data-admin-view-button="users"]').click();
  await expect(page.locator('#adminUserList')).toContainText(user.email);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({path:testInfo.outputPath('admin-users-layout.jpg'), type:'jpeg', quality:75, fullPage:true});
  expect(unexpected).toEqual([]);
});
