import {test, expect} from './fixtures.mjs';

const API_ORIGIN = 'https://api.lecturesift.com';
const LOCAL_ORIGIN = 'http://127.0.0.1:4173';
const CORS = {
  'Access-Control-Allow-Origin': LOCAL_ORIGIN,
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers': 'authorization, content-type',
};

test('admin referral releases require live reconciliation and refresh the queue', async ({page}) => {
  const notDueId = '11111111-1111-4111-8111-111111111111';
  const choicePendingId = '22222222-2222-4222-8222-222222222222';
  const readyId = '33333333-3333-4333-8333-333333333333';
  const rewards = [
    {
      id:notDueId, order_reference:'LS-NOT-DUE', policy_version:'referral-synthetic-v1',
      kind:'first_purchase', status:'pending', reward_choice:'minutes', inviter_minutes:60,
      invitee_minutes:30, pending_until:'2099-01-01T00:00:00Z', hold_complete:false, actionable:false,
    },
    {
      id:choicePendingId, order_reference:'LS-CHOICE-PENDING', policy_version:'referral-synthetic-v1',
      kind:'renewal', status:'pending', reward_choice:null, inviter_minutes:30,
      pending_until:'2026-01-01T00:00:00Z', hold_complete:true, actionable:false,
    },
    {
      id:readyId, order_reference:'LS-READY', policy_version:'referral-synthetic-v1',
      kind:'first_purchase', status:'pending', reward_choice:'minutes', inviter_minutes:60,
      invitee_minutes:30, pending_until:'2026-01-01T00:00:00Z', hold_complete:true, actionable:true,
    },
  ];
  const emptyPage = {page:1, page_size:50, total:0, total_pages:1};
  const responses = new Map([
    ['/billing/admin/overview?limit=250', {counts:{}, orders:[], users:[], plan_distribution:{}, revenue_by_currency:{}}],
    ['/admin/instagram-rewards?status=', {rewards:[]}],
    ['/billing/admin/refund-requests', {requests:[]}],
    ['/billing/admin/credit-events?limit=250', {events:[]}],
    ['/billing/admin/contact-messages?limit=250', {messages:[]}],
    ['/billing/admin/jobs?limit=250', {jobs:[], counts:{}}],
    ['/billing/admin/account-events?limit=250', {events:[]}],
    ['/billing/health', {}],
    ['/rollout/health', {}],
    ['/ads/config', {}],
    ['/analytics/config', {}],
    ['/billing/admin/costs?days=30&limit=250', {}],
    ['/billing/admin/users?search=&verification=all&plan=all&sort=created_desc&page=1&page_size=50', {users:[], pagination:emptyPage}],
    ['/billing/admin/orders?search=&status=all&provider=all&page=1&page_size=50', {orders:[], pagination:emptyPage}],
  ]);
  const unexpected = [];
  const releaseBodies = [];
  let released = false;
  let referralQueueReads = 0;

  await page.route(`${API_ORIGIN}/**`, async route => {
    const request = route.request();
    const url = new URL(request.url());
    const key = url.pathname + url.search;
    const requestedMethod = request.method() === 'OPTIONS'
      ? request.headers()['access-control-request-method']
      : request.method();
    const isReferralQueue = requestedMethod === 'GET' && key === '/billing/admin/referrals';
    const isRelease = requestedMethod === 'POST' && key === `/billing/admin/referrals/${readyId}/release`;
    const isStaticResponse = requestedMethod === 'GET' && responses.has(key);

    if (!isReferralQueue && !isRelease && !isStaticResponse) {
      unexpected.push(`${request.method()} ${url.origin}${url.pathname}`);
      await route.abort('blockedbyclient');
      return;
    }
    if (request.method() === 'OPTIONS') {
      await route.fulfill({status:204, headers:CORS});
      return;
    }
    if (isReferralQueue) {
      referralQueueReads += 1;
      await route.fulfill({
        status:200, headers:CORS,
        json:{rewards:released ? rewards.filter(reward => reward.id !== readyId) : rewards, has_more:false, limit:100},
      });
      return;
    }
    if (isRelease) {
      releaseBodies.push(request.postDataJSON());
      released = true;
      await route.fulfill({status:200, headers:CORS, json:{reward:{...rewards[2], status:'released'}}});
      return;
    }
    await route.fulfill({status:200, headers:CORS, json:responses.get(key)});
  });

  await page.goto('/admin.html#admin-referrals');
  await page.locator('#adminToken').fill('synthetic-admin-token');
  await page.locator('#adminLoginButton').click();
  await expect(page.locator('#adminPanel')).toBeVisible();
  await expect(page.locator('#adminReferralsView')).toBeVisible();

  const releaseForm = id => page.locator(`[data-referral-release-form="${id}"]`);
  const evidence = form => form.locator('[name="evidence_reference"]');
  const provider = form => form.locator('[name="provider_reconciled"]');
  const releaseButton = form => form.locator('[data-referral-release-button]');
  const notDue = releaseForm(notDueId);
  const choicePending = releaseForm(choicePendingId);
  const ready = releaseForm(readyId);

  for (const blocked of [notDue, choicePending]) {
    await expect(evidence(blocked)).toBeDisabled();
    await expect(provider(blocked)).toBeDisabled();
    await expect(releaseButton(blocked)).toBeDisabled();
  }
  await expect(evidence(ready)).toBeEnabled();
  await expect(provider(ready)).toBeEnabled();
  await expect(releaseButton(ready)).toBeDisabled();
  await provider(ready).check();
  await evidence(ready).fill('short');
  await expect(releaseButton(ready)).toBeDisabled();
  await evidence(ready).fill('A       ');
  await expect(releaseButton(ready)).toBeDisabled();
  await evidence(ready).fill('PSP-STATEMENT-2026-0001');
  await expect(releaseButton(ready)).toBeEnabled();

  await page.locator('[data-admin-view-button="overview"]').click();
  await page.locator('[data-admin-view-button="referrals"]').click();
  await expect(provider(ready)).not.toBeChecked();
  await expect(evidence(ready)).toHaveValue('PSP-STATEMENT-2026-0001');
  await expect(releaseButton(ready)).toBeDisabled();

  await provider(ready).check();
  page.once('dialog', dialog => dialog.dismiss());
  await releaseButton(ready).click();
  await expect(provider(ready)).not.toBeChecked();
  await expect(releaseButton(ready)).toBeDisabled();
  expect(releaseBodies).toEqual([]);

  await provider(ready).check();
  page.once('dialog', dialog => dialog.accept());
  await releaseButton(ready).click();
  await expect.poll(() => releaseBodies).toEqual([
    {provider_reconciled:true, evidence_reference:'PSP-STATEMENT-2026-0001'},
  ]);
  await expect.poll(() => referralQueueReads).toBe(2);
  await expect(releaseForm(readyId)).toHaveCount(0);
  await expect(releaseForm(notDueId)).toHaveCount(1);
  await expect(releaseForm(choicePendingId)).toHaveCount(1);
  expect(unexpected).toEqual([]);
});

for (const [caseName, referralPayload] of [
  ['non-numeric limit', {rewards:[], has_more:false, limit:'100'}],
  ['contradictory actionable state', {
    rewards:[{
      id:'44444444-4444-4444-8444-444444444444', order_reference:'LS-CONTRADICTORY',
      policy_version:'referral-synthetic-v1', kind:'renewal', reward_choice:null,
      inviter_minutes:30, pending_until:'2026-01-01T00:00:00Z',
      hold_complete:false, actionable:true,
    }],
    has_more:false,
    limit:100,
  }],
]) {
  test(`admin referral queue fails closed for ${caseName}`, async ({page}) => {
    const emptyPage = {page:1, page_size:50, total:0, total_pages:1};
    const allowedPaths = new Set([
      '/billing/admin/overview', '/admin/instagram-rewards', '/billing/admin/refund-requests',
      '/billing/admin/credit-events', '/billing/admin/contact-messages', '/billing/admin/jobs',
      '/billing/admin/account-events', '/billing/health', '/rollout/health', '/ads/config',
      '/analytics/config', '/billing/admin/costs', '/billing/admin/referrals',
      '/billing/admin/users', '/billing/admin/orders',
    ]);
    const unexpected = [];
    await page.route(`${API_ORIGIN}/**`, async route => {
      const request = route.request();
      const url = new URL(request.url());
      const requestedMethod = request.method() === 'OPTIONS'
        ? request.headers()['access-control-request-method']
        : request.method();
      if (requestedMethod !== 'GET' || !allowedPaths.has(url.pathname)) {
        unexpected.push(`${request.method()} ${url.origin}${url.pathname}`);
        await route.abort('blockedbyclient');
        return;
      }
      if (request.method() === 'OPTIONS') {
        await route.fulfill({status:204, headers:CORS});
        return;
      }
      let json = {};
      if (url.pathname === '/billing/admin/overview') json = {counts:{}, orders:[], users:[], plan_distribution:{}, revenue_by_currency:{}};
      else if (url.pathname === '/billing/admin/referrals') json = referralPayload;
      else if (url.pathname === '/admin/instagram-rewards') json = {rewards:[]};
      else if (url.pathname === '/billing/admin/refund-requests') json = {requests:[]};
      else if (url.pathname === '/billing/admin/contact-messages') json = {messages:[]};
      else if (url.pathname === '/billing/admin/jobs') json = {jobs:[], counts:{}};
      else if (url.pathname === '/billing/admin/users') json = {users:[], pagination:emptyPage};
      else if (url.pathname === '/billing/admin/orders') json = {orders:[], pagination:emptyPage};
      else if (url.pathname === '/billing/admin/credit-events' || url.pathname === '/billing/admin/account-events') json = {events:[]};
      await route.fulfill({status:200, headers:CORS, json});
    });

    await page.goto('/admin.html#admin-referrals');
    await page.locator('#adminToken').fill('synthetic-admin-token');
    await page.locator('#adminLoginButton').click();
    await expect(page.locator('#adminPanel')).toBeVisible();
    await expect(page.locator('#adminReferralRewards')).toContainText("Davet kuyruğunun güvenlik metadata'sı eksik veya geçersiz.");
    await expect(page.locator('[data-referral-release-form]')).toHaveCount(0);
    expect(unexpected).toEqual([]);
  });
}
