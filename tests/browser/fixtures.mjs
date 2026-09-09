import {test as base, expect} from '@playwright/test';

export const JOB_ID = 'ci-synthetic-quiz-0001';
const LOCAL_ORIGIN = 'http://127.0.0.1:4173';
const API_ORIGIN = 'https://api.lecturesift.com';
const account = {
  user: {id: 'ci-synthetic-user', email: 'browser-smoke@example.invalid', first_name: 'Synthetic', email_verified: true},
  plan: {code: 'free'},
  remaining_minutes: 60,
  used_minutes: 0,
  credit_minutes: 0,
};
const result = {
  title: 'Synthetic browser quiz', job_type: 'study_pack',
  summary: 'A deterministic local fixture, not a processed customer document.',
  options: {include_summary: true, include_transcript: false, include_slides: false, quiz_count: 2, flashcard_count: 0},
  quiz: [
    {question: 'Which number is even?', options: ['Three', 'Four'], answer_index: 1, explanation: 'Four is divisible by two.'},
    {question: 'Which shape has three sides?', options: ['Triangle', 'Circle'], answer_index: 0, explanation: 'A triangle has three sides.'},
  ],
  artifacts: [], flashcards: [], slides: [], transcript_segments: [],
};
const stubs = new Map([
  ['/billing/me', {account}],
  ['/billing/providers', {providers: [], commerce_identity: {configured: false}}],
  ['/billing/manual-transfer', {available: false}],
  ['/billing/plans?currency=TRY', {plans: [], selected_currency: 'TRY'}],
  ['/billing/me/rollout', {guest_trial: null, rewarded_ads: {enabled: false}}],
  ['/billing/me/referrals', {enabled: false}],
  ['/ads/config', {enabled: false, provider: 'off'}],
  ['/analytics/config', {enabled: false, google_ads: {enabled: false}}],
  ['/assistant/catalog', {available: false}],
  ['/assistant/catalog?currency=TRY', {available: false}],
  ['/assistant/catalog?currency=USD', {available: false}],
  [`/jobs/${JOB_ID}`, {job_id: JOB_ID, status: 'done', stage: 'done', percent: 100, source_type: 'document', options: {job_type: 'study_pack'}}],
  [`/jobs/${JOB_ID}/result`, result],
]);

// Never continue an external request. Unexpected API calls fail the test instead
// of silently becoming an integration/production test. All values are synthetic.
export const test = base.extend({
  isolatedNetwork: [async ({context}, use) => {
    const unexpected = [];
    const apiCalls = [];
    const pageErrors = [];
    const failedAssets = [];
    context.on('page', page => page.on('pageerror', error => pageErrors.push(error.message)));
    context.on('response', response => {
      if (new URL(response.url()).origin === LOCAL_ORIGIN && response.status() >= 400) {
        failedAssets.push(`${response.status()} ${new URL(response.url()).pathname}`);
      }
    });
    await context.routeWebSocket('**/*', socket => {
      unexpected.push('Unexpected WebSocket connection');
      socket.close();
    });
    await context.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      // Query strings are part of the exact API contract, not ignored extras.
      const apiKey = url.pathname + url.search;
      if (url.origin === LOCAL_ORIGIN && ['GET', 'HEAD'].includes(request.method())) {
        await route.continue();
      } else if (url.origin === API_ORIGIN && request.method() === 'OPTIONS'
        && request.headers()['access-control-request-method'] === 'GET' && stubs.has(apiKey)) {
        await route.fulfill({status: 204, headers: {
          'Access-Control-Allow-Origin': LOCAL_ORIGIN,
          'Access-Control-Allow-Methods': 'GET',
          'Access-Control-Allow-Headers': 'authorization, content-type',
        }});
      } else if (url.origin === API_ORIGIN && request.method() === 'GET' && stubs.has(apiKey)) {
        apiCalls.push(url.pathname);
        await route.fulfill({status: 200, contentType: 'application/json', headers: {'Access-Control-Allow-Origin': LOCAL_ORIGIN}, body: JSON.stringify(stubs.get(apiKey))});
      } else {
        // Reviewed smoke-page sources load no third-party fonts/assets, so the
        // tolerated external-origin allowlist is deliberately empty. Do not log
        // query strings, which could contain sensitive data after a regression.
        unexpected.push(`${request.method()} ${url.origin}${url.pathname}`);
        await route.abort('blockedbyclient');
      }
    });
    await use({apiCalls});
    expect(unexpected, 'Only local assets and the exact synthetic GET API contract may be used').toEqual([]);
    expect(failedAssets, 'The built site must resolve all requested local assets').toEqual([]);
    expect(pageErrors, 'No uncaught browser JavaScript errors').toEqual([]);
  }, {auto: true}],
});

export {expect};
