import {test, expect} from './fixtures.mjs';

const API_ORIGIN = 'https://api.lecturesift.com';
const LOCAL_ORIGIN = 'http://127.0.0.1:4173';
const CORS = {
  'Access-Control-Allow-Origin': LOCAL_ORIGIN,
  'Access-Control-Allow-Methods': 'GET,OPTIONS',
  'Access-Control-Allow-Headers': 'authorization, content-type',
};

function adminResponses(advertisingReadiness) {
  const emptyPage = {page:1, page_size:50, total:0, total_pages:1};
  return new Map([
    ['/billing/admin/overview?limit=250', {counts:{}, orders:[], users:[], plan_distribution:{}, revenue_by_currency:{}}],
    ['/admin/instagram-rewards?status=', {rewards:[]}],
    ['/billing/admin/refund-requests', {requests:[]}],
    ['/billing/admin/credit-events?limit=250', {events:[]}],
    ['/billing/admin/contact-messages?limit=250', {messages:[]}],
    ['/billing/admin/jobs?limit=250', {jobs:[], counts:{}}],
    ['/billing/admin/account-events?limit=250', {events:[]}],
    ['/billing/health', {}],
    ['/rollout/health', {}],
    ['/ads/config', {enabled:false, adsense_auto_ads:{enabled:false}, house_campaign:{enabled:false}}],
    ['/analytics/config', {enabled:false, google_ads:{enabled:false}}],
    ['/billing/admin/advertising-readiness', advertisingReadiness],
    ['/billing/admin/costs?days=30&limit=250', {}],
    ['/billing/admin/referrals', {rewards:[], has_more:false, limit:100}],
    ['/billing/admin/users?search=&verification=all&plan=all&sort=created_desc&page=1&page_size=50', {users:[], pagination:emptyPage}],
    ['/billing/admin/orders?search=&status=all&provider=all&page=1&page_size=50', {orders:[], pagination:emptyPage}],
  ]);
}

async function openGrowthView(page, advertisingReadiness) {
  const responses = adminResponses(advertisingReadiness);
  const unexpected = [];
  await page.addInitScript(() => localStorage.setItem('lecturesift-ui', 'tr'));
  await page.route(`${API_ORIGIN}/**`, async route => {
    const request = route.request();
    const url = new URL(request.url());
    const key = url.pathname + url.search;
    const requestedMethod = request.method() === 'OPTIONS'
      ? request.headers()['access-control-request-method']
      : request.method();
    if (requestedMethod !== 'GET' || !responses.has(key)) {
      unexpected.push(`${request.method()} ${url.origin}${url.pathname}`);
      await route.abort('blockedbyclient');
      return;
    }
    if (request.method() === 'OPTIONS') {
      await route.fulfill({status:204, headers:CORS});
      return;
    }
    await route.fulfill({status:200, headers:CORS, json:responses.get(key)});
  });

  await page.goto('/admin.html#admin-growth');
  await page.locator('[data-consent="essential"]').click();
  await page.locator('#adminToken').fill('synthetic-admin-token');
  await page.locator('#adminLoginButton').click();
  await expect(page.locator('#adminPanel')).toBeVisible();
  await expect(page.locator('#adminGrowthView')).toBeVisible();
  return unexpected;
}

test('admin growth shows the read-only AdSense account summary and escapes provider values', async ({page}) => {
  const unsafeDomain = 'lecturesift.com<img src=x onerror="window.__adsenseInjected=true">';
  const unexpected = await openGrowthView(page, {
    ok:true,
    adsense:{
      management_api:{
        enabled:true, configured:true, connected:true, status:'connected',
        checked_at:'2026-09-12T18:25:00Z', cached:true,
        account:{state:'READY', pending_task_count:0},
        site:{domain:unsafeDomain, state:'GETTING_READY', auto_ads_enabled:true},
        alerts:{total:3, info:1, warning:1, severe:1, types:['SYNTHETIC']},
        policy_issues:{total:2, warned:1, ad_serving_restricted:1, ad_serving_disabled:0, ad_personalization_restricted:0},
        error_code:null,
      },
    },
  });

  const growth = page.locator('#adminGrowthStatus');
  await expect(growth.getByText('AdSense bağlantısı', {exact:true}).locator('..')).toContainText('Bağlı');
  await expect(growth).toContainText('Son kontrol');
  await expect(growth).toContainText('önbellek');
  await expect(growth).toContainText('AdSense hesap durumuHazır');
  await expect(growth).toContainText('Bekleyen görev: 0');
  await expect(growth).toContainText('Hazırlanıyor');
  await expect(growth).toContainText(unsafeDomain);
  await expect(growth).toContainText('Otomatik reklamlarAçık');
  await expect(growth).toContainText('3 uyarı');
  await expect(growth).toContainText('2 bulgu');
  await expect(growth).toContainText('Kişiselleştirme kısıtlı: 0');
  await expect(growth.locator('img')).toHaveCount(0);
  expect(await page.evaluate(() => window.__adsenseInjected)).toBeUndefined();
  expect(unexpected).toEqual([]);
});

test('admin growth clearly reports a temporary AdSense provider failure', async ({page}) => {
  const unexpected = await openGrowthView(page, {
    ok:true,
    adsense:{
      management_api:{
        enabled:true, configured:true, connected:false, status:'unavailable',
        checked_at:null, cached:false, account:null, site:null, alerts:null,
        policy_issues:null, error_code:'provider_unavailable',
      },
    },
  });

  const growth = page.locator('#adminGrowthStatus');
  await expect(growth).toContainText('AdSense bağlantısıGeçici olarak okunamadı');
  await expect(growth).toContainText('Google sağlayıcısı şu anda yanıt vermiyor.');
  await expect(growth).toContainText('Henüz başarılı kontrol yok');
  await expect(growth).toContainText('Hesap durumu alınamadı.');
  await expect(growth).toContainText('Site durumu alınamadı.');
  await expect(growth).toContainText('Uyarı bilgisi alınamadı.');
  await expect(growth).toContainText('Politika bilgisi alınamadı.');
  expect(unexpected).toEqual([]);
});
