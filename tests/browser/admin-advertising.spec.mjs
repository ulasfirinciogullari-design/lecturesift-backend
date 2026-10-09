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

async function openGrowthView(page, advertisingReadiness, {readinessStatus = 200, override, language = 'tr'} = {}) {
  const responses = adminResponses(advertisingReadiness);
  const unexpected = [];
  await page.addInitScript(value => localStorage.setItem('lecturesift-ui', value), language);
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
    if (override && await override(route, url)) return;
    if (key === '/billing/admin/advertising-readiness' && readinessStatus !== 200) {
      await route.fulfill({status:readinessStatus, headers:CORS, json:{detail:{message:'synthetic provider failure'}}});
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

test('admin growth shows read-only advertising summaries and escapes provider values', async ({page}) => {
  const unsafeDomain = 'lecturesift.com<img src=x onerror="window.__adsenseInjected=true">';
  const unsafeTimeZone = 'Europe/Istanbul<img src=x onerror="window.__googleAdsInjected=true">';
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
    google_ads:{
      management_api:{
        enabled:true, configured:true, connected:true, status:'connected',
        checked_at:'2026-09-12T18:26:00Z', cached:false,
        account:{status:'ENABLED', currency_code:'TRY', time_zone:unsafeTimeZone},
        periods:{
          today:{cost_micros:4999, impressions:1400, clicks:75, conversions:4.5},
          last_7_days:{cost_micros:900000000, impressions:11000, clicks:530, conversions:24},
          this_month:{cost_micros:1700000000, impressions:22000, clicks:910, conversions:41},
        },
        campaigns:{total:5, enabled:2, paused:2, removed:1, other:0},
        incentive:{
          status:'available', count:1,
          states:{redeemed:1, fulfilled:1, reward_granted:0, reward_exhausted:0, reward_expired:0, expired:0, invalidated:0, other:0},
          currency_totals:[{
            currency_code:'TRY', required_min_spend_micros:8000000000, current_spend_towards_fulfillment_micros:1700000000,
            reward_amount_micros:8000000000, granted_amount_micros:0, reward_balance_remaining_micros:8000000000,
          }],
          error_code:null,
        },
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
  await expect(growth).toContainText('Google Ads bağlantısıBağlı');
  await expect(growth).toContainText('Google Ads hesabıEtkin');
  await expect(growth).toContainText(unsafeTimeZone);
  await expect(growth).toContainText('Son 7 gün');
  await expect(growth).toContainText(/₺0,004999/);
  await expect(growth).toContainText('Gösterim: 11.000');
  await expect(growth).toContainText('Dönüşüm: 24');
  await expect(growth).toContainText('Kampanya: 5');
  await expect(growth).toContainText('Etkin: 2');
  await expect(growth).toContainText('Promosyon: 1');
  await expect(growth).toContainText('Gerekli harcama');
  await expect(growth).toContainText('Kalan promosyon');
  await expect(growth).toContainText(/₺8[.]000,00/);
  await expect(growth.locator('img')).toHaveCount(0);
  expect(await page.evaluate(() => window.__adsenseInjected)).toBeUndefined();
  expect(await page.evaluate(() => window.__googleAdsInjected)).toBeUndefined();
  expect(unexpected).toEqual([]);
});

test('a slow policy collection keeps the rejected site visible without implying zero findings', async ({page}) => {
  const unexpected = await openGrowthView(page, {
    ok:true,
    adsense:{management_api:{
      enabled:true, configured:true, connected:true, status:'connected',
      checked_at:'2026-09-19T16:18:54Z', cached:false,
      account:{state:'READY', pending_task_count:0},
      site:{domain:'lecturesift.com', state:'NEEDS_ATTENTION', auto_ads_enabled:true},
      alerts:{total:2, info:0, warning:2, severe:0},
      policy_issues:null, policy_error_code:'provider_unavailable', error_code:null,
    }},
    google_ads:{management_api:{status:'not_configured', connected:false}},
  });
  const growth = page.locator('#adminGrowthStatus');
  await expect(growth).toContainText('AdSense bağlantısıBağlı');
  await expect(growth).toContainText('AdSense site durumuİşlem gerekiyor');
  await expect(growth).toContainText('Politika bilgisi alınamadı.');
  await expect(growth).not.toContainText('0 bulgu');
  expect(unexpected).toEqual([]);
});

test('admin growth keeps provider failures explicit without presenting credit', async ({page}) => {
  const unexpected = await openGrowthView(page, {
    ok:true,
    adsense:{
      management_api:{
        enabled:true, configured:true, connected:false, status:'unavailable',
        checked_at:null, cached:false, account:null, site:null, alerts:null,
        policy_issues:null, error_code:'provider_unavailable',
      },
    },
    google_ads:{
      management_api:{
        enabled:true, configured:true, connected:false, status:'unavailable',
        checked_at:null, cached:false, account:null, periods:null, campaigns:null,
        incentive:{status:'unavailable', count:null, states:null, currency_totals:null, error_code:'provider_unavailable'},
        error_code:'provider_unavailable',
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
  await expect(growth).toContainText('Google Ads bağlantısıGeçici olarak okunamadı');
  await expect(growth).toContainText('Brüt harcama ve performans verisi alınamadı.');
  await expect(growth).toContainText('Kampanya sayıları alınamadı.');
  await expect(growth).not.toContainText('Google Ads promosyonu');
  await expect(growth).not.toContainText('8.000');
  expect(unexpected).toEqual([]);
});

test('admin growth isolates an unsupported promotion report from Google Ads totals', async ({page}) => {
  const unexpected = await openGrowthView(page, {
    ok:true,
    adsense:{management_api:{status:'not_configured', connected:false}},
    google_ads:{
      management_api:{
        enabled:true, configured:true, connected:true, status:'connected', checked_at:'2026-09-12T18:26:00Z', cached:true,
        account:{status:'ENABLED', currency_code:'TRY', time_zone:'Europe/Istanbul'},
        periods:{
          today:{cost_micros:0, impressions:0, clicks:0, conversions:0},
          last_7_days:{cost_micros:450000000, impressions:5000, clicks:210, conversions:9},
          this_month:{cost_micros:450000000, impressions:5000, clicks:210, conversions:9},
        },
        campaigns:{total:2, enabled:1, paused:1, removed:0, other:0},
        incentive:{status:'unsupported', count:null, states:null, currency_totals:null, error_code:'not_allowlisted_or_unsupported'},
        error_code:null,
      },
    },
  });

  const growth = page.locator('#adminGrowthStatus');
  await expect(growth).toContainText('Google Ads bağlantısıBağlı');
  await expect(growth).toContainText('Kampanya: 2');
  await expect(growth).toContainText('Bu promosyon raporu hesap için desteklenmiyor veya erişim listesiyle sınırlı.');
  await expect(growth).not.toContainText('8.000');
  expect(unexpected).toEqual([]);
});

test('admin growth treats an advertising readiness request failure as unavailable', async ({page}) => {
  const unexpected = await openGrowthView(page, null, {readinessStatus:503});
  const growth = page.locator('#adminGrowthStatus');
  await expect(growth.getByText('AdSense bağlantısı', {exact:true}).locator('..')).toContainText('Geçici olarak okunamadı');
  const googleConnection = growth.getByText('Google Ads bağlantısı', {exact:true}).locator('..');
  await expect(googleConnection).toContainText('Geçici olarak okunamadı');
  await expect(googleConnection).not.toContainText('Bağlı değil');
  await expect(growth).not.toContainText('8.000');
  expect(unexpected).toEqual([]);
});

test('admin growth never trusts a promotion outside a verified Google Ads connection', async ({page}) => {
  const unexpected = await openGrowthView(page, {
    ok:true,
    adsense:{management_api:{status:'not_configured', connected:false}},
    google_ads:{
      management_api:{
        enabled:true, configured:true, connected:false, status:'unavailable',
        checked_at:null, cached:false, account:null, periods:null, campaigns:null,
        error_code:'provider_unavailable',
      },
      incentive:{
        status:'available', count:1, states:{reward_granted:1},
        currency_totals:[{
          currency_code:'TRY', reward_amount_micros:8000000000,
          granted_amount_micros:8000000000, reward_balance_remaining_micros:8000000000,
        }],
        error_code:null,
      },
    },
  });

  const growth = page.locator('#adminGrowthStatus');
  await expect(growth).toContainText('Google Ads bağlantısıGeçici olarak okunamadı');
  await expect(growth).not.toContainText('Google Ads promosyonu');
  await expect(growth).not.toContainText('8.000');
  expect(unexpected).toEqual([]);
});

const connectedAdSense = {
  status:'connected', connected:true, checked_at:'2026-10-09T12:00:00Z', cached:true,
  account:{state:'READY', pending_task_count:0},
  site:{domain:'lecturesift.com', state:'NEEDS_ATTENTION', auto_ads_enabled:true},
  alerts:{total:0, info:0, warning:0, severe:0},
  policy_issues:{total:0, warned:0, ad_serving_disabled:0, ad_serving_restricted:0, ad_personalization_restricted:0},
};

test('AdSense income preserves empty, signed earnings and unavailable report states separately', async ({page}) => {
  const unexpected = await openGrowthView(page, {
    adsense:{publisher_configured:true, site_approval_confirmed:false, consent_setup_confirmed:false, management_api:{...connectedAdSense, reports:{
      today:{status:'empty', currency_code:'TRY', start_date:'2026-10-09', end_date:'2026-10-09', estimated_earnings_micros:null, page_views:null, impressions:null, clicks:null},
      last_7_days:{status:'available', currency_code:'USD', start_date:'2026-10-02', end_date:'2026-10-08', estimated_earnings_micros:-1250000, page_views:123, impressions:245, clicks:null},
      this_month:{status:'unavailable', currency_code:null, start_date:null, end_date:null, error_code:'provider_unavailable', diagnostics:{stage:'reports_this_month', failure_type:'timeout', elapsed_ms:1500, response_body:'secret-must-not-render'}},
    }}},
    google_ads:{management_api:{status:'not_configured', connected:false}},
  });
  const empty = page.locator('[data-adsense-report="today"]');
  await expect(empty).toContainText('Henüz veri yok');
  await expect(empty).toContainText('2026-10-09');
  await expect(empty).not.toContainText('₺0');
  await expect(empty).not.toContainText('Sayfa görüntüleme: 0');
  const available = page.locator('[data-adsense-report="last_7_days"]');
  await expect(available.locator('header span')).toContainText(/-.*1,25/);
  await expect(available).toContainText('USD');
  await expect(available).toContainText('2026-10-02 – 2026-10-08');
  await expect(available).toContainText('Sayfa görüntüleme: 123');
  await expect(available).toContainText('Gösterim: 245');
  await expect(available).toContainText('Tıklama: —');
  const unavailable = page.locator('[data-adsense-report="this_month"]');
  await expect(unavailable).toContainText('Geçici olarak okunamadı');
  await expect(unavailable).toContainText('reports_this_month');
  await expect(unavailable).toContainText('timeout');
  await expect(unavailable).toContainText('1.500 ms');
  await expect(page.locator('#adminGrowthView')).not.toContainText('secret-must-not-render');
  await expect(page.locator('#adminAdSenseSummary')).toContainText('Site onayı için düzeltme gerekiyor');
  await expect(page.locator('#adminGrowthStatus')).toContainText('AdSense bağlantısıBağlı');
  expect(unexpected).toEqual([]);
});

for (const configResponse of [{name:'503', status:503, body:{detail:{message:'synthetic config failure'}}}, {name:'null', status:200, body:null}, {name:'empty object', status:200, body:{}}]) {
  test(`unknown ${configResponse.name} configuration and counts never become off, unconfigured or zero`, async ({page}) => {
    const unexpected = await openGrowthView(page, {
      adsense:{management_api:{...connectedAdSense, account:{state:'READY', pending_task_count:null}, alerts:{total:null}, policy_issues:{total:null}}},
      google_ads:{management_api:{status:'not_configured', connected:false}},
    }, {override:async (route, url) => {
      if (!['/ads/config', '/analytics/config'].includes(url.pathname)) return false;
      await route.fulfill({status:configResponse.status, headers:CORS, contentType:'application/json', body:JSON.stringify(configResponse.body)});
      return true;
    }});
    const growth = page.locator('#adminGrowthStatus');
    for (const title of ['Site reklam yayını', 'Ziyaretçi ölçümü', 'Google Ads dönüşüm ölçümü']) {
      const card = growth.locator('article').filter({has:page.getByText(title, {exact:true})});
      await expect(card.locator('header span')).toHaveText('Geçici olarak okunamadı');
      await expect(card).toContainText('doğrulanamadı');
      await expect(card).not.toContainText('GA4 ölçümü kapalı.');
      await expect(card).not.toContainText('Site tarafındaki gösterim kapalı.');
      await expect(card).not.toContainText('tamamlanmamış');
    }
    await expect(growth).not.toContainText('0 uyarı');
    await expect(growth).not.toContainText('0 bulgu');
    await expect(growth).not.toContainText('Bekleyen görev: 0');
    await expect(page.locator('#adminAdSenseSummary li').filter({hasText:'İzin mesajı'})).toContainText('Geçici olarak okunamadı');
    expect(unexpected).toEqual([]);
  });
}

test('growth refresh requests only advertising data and recovers without duplicate provider requests', async ({page}) => {
  const requests = [];
  let capture = false;
  let releaseRefresh;
  const refreshPending = new Promise(resolve => { releaseRefresh = resolve; });
  const initial = {adsense:{management_api:{status:'unavailable', connected:false, error_code:'provider_unavailable', diagnostics:{stage:'oauth', failure_type:'network', elapsed_ms:100}}}, google_ads:{management_api:{status:'not_configured', connected:false}}};
  const unexpected = await openGrowthView(page, initial, {override:async (route, url) => {
    if (!capture) return false;
    requests.push(url.pathname);
    if (url.pathname !== '/billing/admin/advertising-readiness') return false;
    await refreshPending;
    await route.fulfill({headers:CORS, json:{adsense:{management_api:connectedAdSense}, google_ads:initial.google_ads}});
    return true;
  }});
  await expect(page.locator('#adminRefresh')).toBeEnabled();
  await page.locator('#adminAutoRefresh').uncheck();
  await expect(page.locator('#adminGrowthStatus')).toContainText('Kontrol adımı: oauth');
  await expect(page.locator('#adminGrowthStatus')).toContainText('Hata türü: network');
  capture = true;
  try {
    await page.locator('#adminGrowthRefresh').click();
    await expect(page.locator('#adminGrowthRefresh')).toBeDisabled();
    await expect(page.locator('#adminGrowthStatus')).toHaveAttribute('aria-busy', 'true');
    // A second caller must share the in-flight provider read.
    await page.evaluate(() => { void loadAdminGrowth(); });
    await expect.poll(() => requests.length).toBe(3);
    expect([...requests].sort()).toEqual(['/ads/config', '/analytics/config', '/billing/admin/advertising-readiness']);
    releaseRefresh();
    await expect(page.locator('#adminGrowthRefresh')).toBeEnabled();
    await expect(page.locator('#adminGrowthStatus')).toHaveAttribute('aria-busy', 'false');
    await expect(page.locator('#adminGrowthStatus')).toContainText('AdSense bağlantısıBağlı');
    await expect(page.locator('#adminGrowthStatus')).toContainText('önbellek');
    expect(requests).toHaveLength(3);
  } finally { releaseRefresh(); }
  expect(unexpected).toEqual([]);
});

test('one failed Google Ads period or campaign report leaves connected account and other spend visible', async ({page}) => {
  const unexpected = await openGrowthView(page, {
    adsense:{management_api:{status:'not_configured', connected:false}},
    google_ads:{id_configured:true, signup_configured:false, purchase_configured:true, management_api:{
      status:'connected', connected:true, checked_at:'2026-10-09T12:00:00Z',
      account:{status:'ENABLED', currency_code:'TRY', time_zone:'Europe/Istanbul'},
      periods:{today:{cost_micros:1500000, impressions:2, clicks:1, conversions:0}, last_7_days:null, this_month:{cost_micros:5000000, impressions:25, clicks:4, conversions:1}},
      period_errors:{last_7_days:'provider_unavailable'}, campaigns:null, campaigns_error_code:'permission_denied',
    }},
  });
  const growth = page.locator('#adminGrowthStatus');
  await expect(growth).toContainText('Google Ads bağlantısıBağlı');
  await expect(growth).toContainText('Google Ads hesabıEtkin');
  await expect(growth).toContainText('Bugün: ₺1,50');
  await expect(growth).toContainText('Son 7 gün: Geçici olarak okunamadı');
  await expect(growth).toContainText('Ay başından beri: ₺5,00');
  await expect(growth).not.toContainText('Kampanya: 0');
  await expect(growth).toContainText('Bağlı Google hesabının erişim izni yok.');
  await expect(growth).toContainText('Kayıt dönüşümü: Yapılandırılmamış');
  expect(unexpected).toEqual([]);
});

for (const language of ['en', 'ar']) {
  test(`advertising summary and groups use the selected ${language} language`, async ({page}) => {
    const unexpected = await openGrowthView(page, {adsense:{management_api:connectedAdSense}, google_ads:{management_api:{status:'not_configured', connected:false}}}, {language});
    await expect(page.locator('#adminRefresh')).toBeEnabled();
    await expect(page.locator('html')).toHaveAttribute('lang', language);
    const labels = await page.evaluate(() => ['admin.adsenseSummaryFix', 'admin.adsenseGroup', 'admin.googleAdsGroup', 'admin.adsensePublisher', 'admin.growthRefresh'].map(key => window.LectureSiftI18n.t(key)));
    for (const label of labels) {
      expect(label).not.toMatch(/^admin\./);
      await expect(page.locator('#adminGrowthView')).toContainText(label);
    }
    await expect(page.locator('#adminAdSenseSummary')).not.toContainText('Site onayı için düzeltme gerekiyor');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    expect(unexpected).toEqual([]);
  });
}
