import {test, expect} from './fixtures.mjs';

const lesson = {job_id:'lesson-1', title:'Örnek istatistik dersi', owner_id:'11111111-1111-4111-8111-111111111111', owner_email:'student@example.invalid', owner_name:'Örnek Öğrenci', status:'done', percent:100, stage:'done', source_file_count:1, created:1791410400, options:{output_language:'tr'}};

const API = 'https://api.lecturesift.com';
const CORS = {
  'Access-Control-Allow-Origin':'http://127.0.0.1:4173',
  'Access-Control-Allow-Methods':'GET,POST,DELETE,PATCH,OPTIONS',
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

test('administrator can find a lesson, inspect escaped outputs and download with authorization', async ({page}, testInfo) => {
  const unexpected = await openAdmin(page, {hash:'jobs', override:async (route, url) => {
    if (url.pathname === '/billing/admin/jobs') {
      await route.fulfill({headers:CORS, json:{jobs:[lesson]}});
      return true;
    }
    if (url.pathname === '/billing/admin/jobs/lesson-1') {
      expect(route.request().headers().authorization).toBe('Bearer synthetic-admin-token');
      await route.fulfill({headers:CORS, json:{job:lesson, result:{title:lesson.title, summary:'<img src=x onerror="alert(1)"> Ders özeti', transcript:'Özel transkript', source_files:['ders.pdf'], quiz:[{question:'2 + 2?', options:['3','4'], answer_index:1, explanation:'Toplama'}], flashcards:[{front:'Medyan?', back:'Ortadaki değer'}], artifacts:[{file:'notes.txt',label:'Ders notları'}]}}});
      return true;
    }
    if (url.pathname === '/billing/admin/jobs/lesson-1/artifacts/notes.txt') {
      expect(route.request().headers().authorization).toBe('Bearer synthetic-admin-token');
      expect(url.search).toBe('');
      await route.fulfill({headers:{...CORS,'Content-Type':'application/octet-stream'}, body:'Synthetic private output'});
      return true;
    }
    return false;
  }});
  await page.locator('#adminJobSearch').fill('student@example.invalid');
  await page.locator('[data-job-open]').click();
  const dialog = page.locator('#adminJobDialog');
  await expect(dialog).toContainText('Ders özeti');
  await expect(dialog.locator('img')).toHaveCount(0);
  await dialog.getByText('Quiz ve cevaplar', {exact:true}).click();
  await expect(dialog).toContainText('Cevap: 4');
  const downloadEvent = page.waitForEvent('download');
  await dialog.getByRole('button', {name:'Ders notları indir', exact:true}).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe('notes.txt');
  expect(await download.failure()).toBeNull();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({path:testInfo.outputPath('admin-lesson-layout.jpg'),type:'jpeg',quality:65});
  await dialog.getByRole('button', {name:'Kapat', exact:true}).click();
  await page.locator('#adminJobSearch').fill('does-not-exist');
  await expect(page.locator('#adminJobs')).toContainText('uygun ders bulunamadı');
  expect(unexpected).toEqual([]);
});

test('user detail filters lessons on the server and actual error state remains searchable', async ({page}) => {
  const paths = [];
  let failOnce = true;
  const unexpected = await openAdmin(page, {hash:'users', override:async (route, url) => {
    if (url.pathname !== '/billing/admin/jobs') return false;
    paths.push(url.search);
    if (url.searchParams.has('owner_id') && failOnce) {
      failOnce = false;
      await route.fulfill({status:503, headers:CORS, json:{detail:{message:'Ders listesi şu anda alınamadı'}}});
      return true;
    }
    await route.fulfill({headers:CORS, json:{jobs:url.searchParams.has('owner_id') ? [{...lesson, status:'error', percent:42, error_code:'LS-TEST-01'}] : []}});
    return true;
  }});
  await expect(page.locator('#adminRefresh')).toBeEnabled();
  await page.getByRole('button', {name:'Aç ve düzenle', exact:true}).click();
  await page.getByRole('button', {name:'Derslerini görüntüle', exact:true}).click();
  await expect(page.locator('#adminUserDialog')).not.toBeVisible();
  await expect(page.locator('#adminJobsView')).toBeVisible();
  await expect(page.locator('#adminJobs')).toContainText('Ders listesi şu anda alınamadı');
  await page.evaluate(() => applyAdminFilters());
  await expect(page.locator('#adminJobs')).toContainText('Ders listesi şu anda alınamadı');
  await page.locator('#adminJobs').getByRole('button', {name:'Tekrar dene'}).click();
  await expect(page.locator('#adminJobs')).toContainText(lesson.title);
  expect(paths).toContain(`?limit=250&owner_id=${user.id}`);
  await page.locator('#adminJobStatus').selectOption('error');
  await expect(page.locator('#adminJobs')).toContainText('LS-TEST-01');
  await page.getByRole('button', {name:'Tüm kullanıcılar', exact:true}).click();
  await expect(page.locator('#adminJobs')).toContainText('uygun ders bulunamadı');
  expect(unexpected).toEqual([]);
});

test('lesson inspection can recover from failure and clears content on logout', async ({page}) => {
  let failed = true;
  const unexpected = await openAdmin(page, {hash:'jobs', override:async (route, url) => {
    if (url.pathname === '/billing/admin/jobs') {
      await route.fulfill({headers:CORS, json:{jobs:[lesson]}});
      return true;
    }
    if (url.pathname !== '/billing/admin/jobs/lesson-1') return false;
    await route.fulfill({status:failed ? 503 : 200, headers:CORS, json:failed ? {detail:{message:'Geçici depolama hatası'}} : {job:lesson,result:null,result_message:'Çıktının saklama süresi dolmuş'}});
    return true;
  }});
  await page.locator('[data-job-open]').click();
  await expect(page.locator('#adminJobDialog')).toContainText('Geçici depolama hatası');
  failed = false;
  await page.locator('#adminJobDialog').getByRole('button', {name:'Tekrar dene'}).click();
  await expect(page.locator('#adminJobDialog')).toContainText('saklama süresi dolmuş');
  await page.evaluate(() => endAdminSession());
  await expect(page.locator('#adminJobDialog')).not.toBeVisible();
  await expect(page.locator('#adminJobDialogBody')).toBeEmpty();
  expect(unexpected).toEqual([]);
});

test('refresh updates scoped lessons without dropping filters and recovers from scoped failures', async ({page}) => {
  let scopedReads = 0;
  let failScoped = false;
  const unexpected = await openAdmin(page, {hash:'users', override:async (route, url) => {
    if (url.pathname !== '/billing/admin/jobs' || !url.searchParams.has('owner_id')) return false;
    expect(url.searchParams.get('owner_id')).toBe(user.id);
    scopedReads += 1;
    await route.fulfill({status:failScoped ? 503 : 200, headers:CORS,
      json:failScoped ? {detail:{message:'Kullanıcının dersleri güncellenemedi'}}
        : {jobs:[{...lesson, title:`Ders durumu ${scopedReads}`, status:'error', percent:scopedReads * 10}]}});
    return true;
  }});
  await expect(page.locator('#adminRefresh')).toBeEnabled();
  await page.getByRole('button', {name:'Aç ve düzenle', exact:true}).click();
  await page.getByRole('button', {name:'Derslerini görüntüle', exact:true}).click();
  await expect(page.locator('#adminJobs')).toContainText('Ders durumu 1');
  await page.locator('#adminJobSearch').fill(user.email);
  await page.locator('#adminJobStatus').selectOption('error');
  await page.locator('#adminRefresh').click();
  await expect(page.locator('#adminJobs')).toContainText('Ders durumu 2');
  await expect(page.locator('#adminJobSearch')).toHaveValue(user.email);
  await expect(page.locator('#adminJobStatus')).toHaveValue('error');
  await expect(page.locator('#adminJobScope')).toContainText(user.name);
  await expect(page.locator('#adminRefresh')).toBeEnabled();
  failScoped = true;
  await page.locator('#adminRefresh').click();
  await expect(page.locator('#adminJobs')).toContainText('Kullanıcının dersleri güncellenemedi');
  await expect(page.locator('#adminJobs')).not.toContainText('Ders durumu 2');
  await expect(page.locator('#adminRefresh')).toBeEnabled();
  failScoped = false;
  await page.locator('#adminRefresh').click();
  await expect(page.locator('#adminJobs')).toContainText('Ders durumu 4');
  await expect(page.locator('#adminJobSearch')).toHaveValue(user.email);
  await expect(page.locator('#adminJobStatus')).toHaveValue('error');
  expect(unexpected).toEqual([]);
});

test('global refresh clears a failed return from user lessons to all lessons', async ({page}) => {
  let failGlobal = false;
  const unexpected = await openAdmin(page, {hash:'users', override:async (route, url) => {
    if (url.pathname !== '/billing/admin/jobs') return false;
    const failed = failGlobal && !url.searchParams.has('owner_id');
    await route.fulfill({status:failed ? 503 : 200, headers:CORS,
      json:failed ? {detail:{message:'Tüm dersler alınamadı'}} : {jobs:[lesson]}});
    return true;
  }});
  await expect(page.locator('#adminRefresh')).toBeEnabled();
  await page.getByRole('button', {name:'Aç ve düzenle', exact:true}).click();
  await page.getByRole('button', {name:'Derslerini görüntüle', exact:true}).click();
  await expect(page.locator('#adminJobs')).toContainText(lesson.title);
  failGlobal = true;
  await page.getByRole('button', {name:'Tüm kullanıcılar', exact:true}).click();
  await expect(page.locator('#adminJobs')).toContainText('Tüm dersler alınamadı');
  failGlobal = false;
  await page.locator('#adminRefresh').click();
  await expect(page.locator('#adminJobs')).toContainText(lesson.title);
  await expect(page.locator('#adminJobs')).not.toContainText('Tüm dersler alınamadı');
  await expect(page.locator('#adminJobScope')).toBeEmpty();
  expect(unexpected).toEqual([]);
});

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

test('advertising gets a longer bounded timeout while the panel remains usable', async ({page}) => {
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
    await expect(page.locator('#adminRefresh')).toBeDisabled();
    await page.locator('[data-admin-view-button="users"]').click();
    await expect(page.locator('#adminUserList')).toContainText(user.email);
    await page.clock.runFor(25_000);
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

test('unverified account closure accepts SİL, allows cancellation and dismisses notices', async ({page}) => {
  await page.clock.install();
  let closed = false;
  let releaseAdvertising;
  const advertisingReady = new Promise(resolve => { releaseAdvertising = resolve; });
  const deletions = [];
  const nativeDialogs = [];
  page.on('dialog', async dialog => { nativeDialogs.push(dialog.message()); await dialog.dismiss(); });
  const unexpected = await openAdmin(page, {hash:'users', override:async (route, url) => {
    if (url.pathname === '/billing/admin/advertising-readiness') {
      await advertisingReady;
      await route.fulfill({status:200, headers:CORS, json:advertising});
    } else if (url.pathname === '/billing/admin/users') {
      await route.fulfill({status:200, headers:CORS, json:{users:closed ? [] : [{...user, email_verified:false}], pagination:{...pagination, total:closed ? 0 : 1}}});
    } else if (url.pathname === `/billing/admin/users/${user.id}` && route.request().method() === 'DELETE') {
      deletions.push(route.request().postDataJSON());
      closed = true;
      await route.fulfill({status:200, headers:CORS, json:{ok:true, status:'closed', message:'Hesap kapatıldı ve ders dosyaları silindi.'}});
    } else return false;
    return true;
  }});
  try {
    await expect(page.locator('#adminUserList')).toContainText(user.email);
    await expect(page.locator('#adminRefresh')).toBeDisabled();
    await page.getByRole('button', {name:'Aç ve düzenle', exact:true}).click();
    await page.locator('#adminUserDialog .admin-dialog-close').click();
    expect(deletions).toEqual([]);
    await page.getByRole('button', {name:'Aç ve düzenle', exact:true}).click();
    const form = page.locator('[data-user-close-form]');
    await form.locator('[name="reason"]').fill('Doğrulanmamış test hesabı');
    await form.locator('[name="confirmation_word"]').fill('yanlış');
    await form.locator('button[type="submit"]').click();
    await expect(page.locator('.admin-dialog-notice')).toContainText('SİL yaz');
    expect(deletions).toEqual([]);
    await page.locator('.admin-dialog-notice .admin-notice-close').click();
    await expect(page.locator('.admin-dialog-notice')).toBeHidden();
    await form.locator('[name="confirmation_word"]').fill('SİL');
    await form.locator('button[type="submit"]').click();
    await expect(page.locator('#adminUserDialog')).toBeHidden();
    await expect(page.locator('#adminOperationNotice')).toContainText('Hesap kapatıldı');
    await expect(page.locator('#adminUserList')).not.toContainText(user.email);
    expect(deletions).toEqual([{confirmation_email:user.email, reason:'Doğrulanmamış test hesabı'}]);
    expect(nativeDialogs).toEqual([]);
    await page.clock.runFor(8_100);
    await expect(page.locator('#adminOperationNotice')).toBeHidden();
  } finally { releaseAdvertising(); }
  await expect(page.locator('#adminRefresh')).toBeEnabled();
  expect(unexpected).toEqual([]);
});

test('bulk closure keeps failed accounts selected, shows their errors and retries only those accounts', async ({page}, testInfo) => {
  await page.clock.install();
  const second = {...user, id:'22222222-2222-4222-8222-222222222222', email:'second@example.invalid', email_verified:false};
  let users = [{...user, email_verified:false}, second];
  const requests = [];
  const nativeDialogs = [];
  let overviewReads = 0;
  page.on('dialog', async dialog => { nativeDialogs.push(dialog.message()); await dialog.dismiss(); });
  const unexpected = await openAdmin(page, {hash:'users', override:async (route, url) => {
    if (url.pathname === '/billing/admin/overview') { overviewReads += 1; return false; }
    if (url.pathname === '/billing/admin/users') {
      await route.fulfill({status:200, headers:CORS, json:{users, pagination:{...pagination, total:users.length}}});
    } else if (url.pathname === '/billing/admin/users/bulk-action') {
      requests.push(route.request().postDataJSON());
      const first = requests.length === 1;
      users = first ? [second] : [];
      await route.fulfill({status:200, headers:CORS, json:{
        ok:true, succeeded:1, failed:first ? 1 : 0,
        message:`1 kullanıcı için işlem tamamlandı; ${first ? 1 : 0} işlem uygulanamadı.`,
        results:first ? [{user_id:user.id, ok:true}, {user_id:second.id, ok:false, message:'Hesap işlemi tamamlanamadı. <img src=x onerror=alert(1)>'}]
          : [{user_id:second.id, ok:true}],
      }});
    } else return false;
    return true;
  }});
  await expect(page.locator('#adminRefresh')).toBeEnabled();
  await page.locator('#adminSelectVisibleUsers').check();
  await page.clock.runFor(60_100);
  expect(overviewReads).toBe(1);
  await page.locator('#adminBulkAction').selectOption('delete');
  await expect(page.locator('#adminBulkDeleteWarning')).toContainText('geri alınamaz');
  await page.locator('#adminBulkReason').fill('Doğrulanmamış test hesapları');
  await page.locator('#adminBulkConfirmation').fill('SIL');
  await page.locator('#adminBulkApply').click();
  await expect(page.locator('#adminBulkApply')).toBeEnabled();
  await expect(page.locator('#adminOperationNotice')).toContainText(`${second.email}: Hesap işlemi tamamlanamadı.`);
  expect(await page.locator('#adminOperationNotice img').count()).toBe(0);
  await expect(page.locator(`[data-user-select="${second.id}"]`)).toBeChecked();
  await expect(page.locator('#adminSelectedCount')).toContainText('1 kullanıcı');
  await expect(page.locator('#adminUserList')).not.toContainText(user.email);
  await expect(page.locator('#adminBulkConfirmation')).toHaveValue('');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({path:testInfo.outputPath('admin-bulk-notice-layout.jpg'), type:'jpeg', quality:75, fullPage:true});
  await page.locator('#adminOperationNotice .admin-notice-close').click();
  await expect(page.locator('#adminOperationNotice')).toBeHidden();
  await page.locator('#adminBulkApply').click();
  await expect(page.locator('#adminOperationNotice')).toContainText('SİL yaz');
  expect(requests).toHaveLength(1);
  await page.locator('#adminBulkConfirmation').fill('SİL');
  await page.locator('#adminBulkApply').click();
  await expect(page.locator('#adminBulkToolbar')).toBeHidden();
  await expect(page.locator('#adminOperationNotice')).toContainText('0 işlem uygulanamadı');
  expect(requests.map(request => request.user_ids)).toEqual([[user.id, second.id], [second.id]]);
  expect(nativeDialogs).toEqual([]);
  expect(unexpected).toEqual([]);
});

test('late account closure keeps a different user editing dialog and its draft open', async ({page}) => {
  const second = {...user, id:'22222222-2222-4222-8222-222222222222', email:'second@example.invalid'};
  let users = [user, second];
  let deletionStarted = false;
  let releaseDelete;
  const pendingDelete = new Promise(resolve => { releaseDelete = resolve; });
  const unexpected = await openAdmin(page, {hash:'users', override:async (route, url) => {
    if (url.pathname === '/billing/admin/users') {
      await route.fulfill({headers:CORS, json:{users, pagination:{...pagination, total:users.length}}});
    } else if (url.pathname === `/billing/admin/users/${user.id}` && route.request().method() === 'DELETE') {
      deletionStarted = true;
      await pendingDelete;
      users = [second];
      await route.fulfill({headers:CORS, json:{ok:true, message:'İlk hesap kapatıldı.'}});
    } else if (url.pathname === `/billing/admin/users/${second.id}/entitlements`) {
      await route.fulfill({headers:CORS, json:{registered:true, assistant_credits:0, assistant_available:true}});
    } else if (url.pathname === `/billing/admin/users/${second.id}/activity`) {
      await route.fulfill({headers:CORS, json:{activity:[]}});
    } else return false;
    return true;
  }});
  try {
    await expect(page.locator('#adminRefresh')).toBeEnabled();
    await page.locator(`[data-user-open="${user.id}"]`).first().click();
    const closing = page.locator('[data-user-close-form]');
    await closing.locator('[name="reason"]').fill('Test hesabını kapat');
    await closing.locator('[name="confirmation_word"]').fill('SİL');
    await closing.locator('button[type="submit"]').click();
    await expect.poll(() => deletionStarted).toBe(true);
    await page.locator('#adminUserDialog .admin-dialog-close').click();
    await page.locator(`[data-user-open="${second.id}"]`).first().click();
    const draft = page.locator('[data-user-profile-form] [name="first_name"]');
    await draft.fill('İkinci kullanıcının taslağı');
    releaseDelete();
    await expect(page.locator('#adminUserList')).not.toContainText(user.email);
    await expect(page.locator('#adminUserDialog')).toBeVisible();
    await expect(page.locator('[data-user-profile-form]')).toHaveAttribute('data-user-profile-form', second.id);
    await expect(draft).toHaveValue('İkinci kullanıcının taslağı');
  } finally { releaseDelete(); }
  expect(unexpected).toEqual([]);
});

for (const mutation of [
  {name:'profile save', method:'PATCH', suffix:'', form:'data-user-profile-form'},
  {name:'minute adjustment', method:'POST', suffix:'/credit-adjustment', form:'data-user-credit-form'},
  {name:'subscription update', method:'POST', suffix:'/subscription', form:'data-user-subscription-form'},
  {name:'session revocation', method:'POST', suffix:'/revoke-sessions'},
]) {
  test(`late ${mutation.name} preserves reopened and different user drafts`, async ({page}) => {
    const second = {...user, id:'22222222-2222-4222-8222-222222222222', email:'second@example.invalid'};
    const mutationPath = `/billing/admin/users/${user.id}${mutation.suffix}`;
    let mutationCount = 0;
    let pendingMutation, releaseMutation;
    page.on('dialog', dialog => dialog.accept());
    const unexpected = await openAdmin(page, {hash:'users', override:async (route, url) => {
      if (url.pathname === mutationPath && route.request().method() === mutation.method) {
        mutationCount += 1;
        await pendingMutation;
        await route.fulfill({headers:CORS, json:{ok:true, message:`Kayıt güncellendi ${mutationCount}`}});
      } else if (url.pathname === '/billing/admin/users') {
        await route.fulfill({headers:CORS, json:{users:[user, second], pagination:{...pagination, total:2}}});
      } else if (url.pathname === `/billing/admin/users/${second.id}/entitlements`) {
        await route.fulfill({headers:CORS, json:{registered:true, assistant_credits:0, assistant_available:true}});
      } else if (url.pathname === `/billing/admin/users/${second.id}/activity`) {
        await route.fulfill({headers:CORS, json:{activity:[]}});
      } else return false;
      return true;
    }});
    for (const [index, target] of [user, second].entries()) {
      pendingMutation = new Promise(resolve => { releaseMutation = resolve; });
      try {
        await expect(page.locator('#adminRefresh')).toBeEnabled();
        await page.locator(`[data-user-open="${user.id}"]`).first().click();
        if (mutation.form) {
          const form = page.locator(`[${mutation.form}]`);
          if (mutation.suffix === '/credit-adjustment') {
            await form.locator('[name="minutes_delta"]').fill('10');
            await form.locator('[name="reason"]').fill('Sentetik test düzeltmesi');
          }
          await form.locator('button[type="submit"]').click();
        } else await page.locator('[data-user-revoke]').click();
        await expect.poll(() => mutationCount).toBe(index + 1);
        await page.locator('#adminUserDialog .admin-dialog-close').click();
        await page.locator(`[data-user-open="${target.id}"]`).first().click();
        const draft = page.locator('[data-user-profile-form] [name="first_name"]');
        await draft.fill(`Yeni taslak ${index + 1}`);
        releaseMutation();
        await expect(page.locator('#adminOperationNotice')).toContainText(`Kayıt güncellendi ${index + 1}`);
        await expect(page.locator('#adminRefresh')).toBeEnabled();
        await expect(page.locator('#adminUserDialog')).toBeVisible();
        await expect(page.locator('[data-user-profile-form]')).toHaveAttribute('data-user-profile-form', target.id);
        await expect(draft).toHaveValue(`Yeni taslak ${index + 1}`);
        await page.locator('#adminUserDialog .admin-dialog-close').click();
      } finally { releaseMutation(); }
    }
    expect(unexpected).toEqual([]);
  });
}

test('late support deletion preserves a different conversation and its unsent reply', async ({page}) => {
  const first = {id:'support-first', name:user.name, email:user.email, topic:'İlk konuşma', message:'İlk destek mesajı', status:'new'};
  const second = {...first, id:'support-second', topic:'İkinci konuşma', message:'İkinci destek mesajı'};
  let messages = [first, second];
  let deletionStarted = false;
  let releaseDelete;
  const pendingDelete = new Promise(resolve => { releaseDelete = resolve; });
  const base = '/billing/admin/contact-messages';
  page.on('dialog', dialog => dialog.accept());
  const unexpected = await openAdmin(page, {hash:'support', override:async (route, url) => {
    if (url.pathname === base) {
      await route.fulfill({headers:CORS, json:{messages}});
    } else if (url.pathname === `${base}/${first.id}` && route.request().method() === 'DELETE') {
      deletionStarted = true;
      await pendingDelete;
      messages = [second];
      await route.fulfill({headers:CORS, json:{ok:true}});
    } else if ([`${base}/${first.id}`, `${base}/${second.id}`].includes(url.pathname)) {
      await route.fulfill({headers:CORS, json:{message:url.pathname.endsWith(first.id) ? first : second, replies:[]}});
    } else return false;
    return true;
  }});
  try {
    await expect(page.locator('#adminRefresh')).toBeEnabled();
    await page.locator(`[data-contact-open="${first.id}"]`).click();
    await page.locator('#adminContactDialog [data-contact-delete]').click();
    await expect.poll(() => deletionStarted).toBe(true);
    await page.locator('#adminContactDialog .admin-dialog-close').click();
    await page.locator(`[data-contact-open="${second.id}"]`).click();
    const draft = page.locator('[data-contact-reply-form] textarea');
    await draft.fill('İkinci konuşmaya henüz gönderilmemiş yanıt');
    releaseDelete();
    await expect(page.locator('#adminContactMessages')).not.toContainText(first.topic);
    await expect(page.locator('#adminContactDialog')).toBeVisible();
    await expect(page.locator('[data-contact-reply-form]')).toHaveAttribute('data-contact-reply-form', second.id);
    await expect(draft).toHaveValue('İkinci konuşmaya henüz gönderilmemiş yanıt');
  } finally { releaseDelete(); }
  expect(unexpected).toEqual([]);
});

test('admin advertising layout stays readable in each viewport and theme', async ({page}, testInfo) => {
  const assertTextContrast = async selector => {
    const contrastRatios = await page.locator(selector).evaluateAll(nodes => {
      const rgb = value => (value.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
      const luminance = values => values.map(value => {
        const channel = value / 255;
        return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
      }).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
      return nodes.map(node => {
        const ink = luminance(rgb(getComputedStyle(node).color));
        const background = node.matches('.admin-sidebar-label') ? node.closest('.admin-sidebar')
          : node.matches('p') ? node.closest('article') : node;
        const paper = luminance(rgb(getComputedStyle(background).backgroundColor));
        return {text:node.textContent, ratio:(Math.max(ink, paper) + .05) / (Math.min(ink, paper) + .05)};
      });
    });
    expect(contrastRatios.length).toBeGreaterThan(0);
    for (const {text, ratio} of contrastRatios) expect(ratio, text).toBeGreaterThanOrEqual(4.5);
  };
  const theme = testInfo.project.name.endsWith('dark') ? 'dark' : 'light';
  const unexpected = await openAdmin(page, {hash:'growth', theme});
  await expect(page.locator('#adminRefresh')).toBeEnabled();
  await expect(page.locator('#adminAdSenseSummary')).toBeVisible();
  await expect(page.locator('#adminGrowthRefresh')).toBeEnabled();
  await expect(page.locator('#adminGrowthStatus .admin-growth-group')).toHaveCount(3);
  await expect(page.locator('#adminAdSenseSummary li')).toHaveCount(5);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await assertTextContrast('#adminGrowthStatus article p, #adminGrowthStatus article header span');
  if (await page.locator('.admin-sidebar-label').isVisible()) await assertTextContrast('.admin-sidebar-label');
  await page.screenshot({path:testInfo.outputPath('admin-growth-layout.jpg'), type:'jpeg', quality:75, fullPage:true});
  await page.locator('[data-admin-view-button="users"]').click();
  await expect(page.locator('#adminUserList')).toContainText(user.email);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await assertTextContrast('#adminUsersResultCount');
  await page.screenshot({path:testInfo.outputPath('admin-users-layout.jpg'), type:'jpeg', quality:75, fullPage:true});
  expect(unexpected).toEqual([]);
});
