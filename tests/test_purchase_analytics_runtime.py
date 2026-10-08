"""Check purchase deduplication against the real browser helpers, without network."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required for purchase measurement checks")

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const scenario = process.argv[1];
const auth = fs.readFileSync(process.argv[2], 'utf8');
const analytics = fs.readFileSync(process.argv[3], 'utf8');
const begin = auth.indexOf('function recordAnalytics(');
const end = auth.indexOf('function errorMessage(', begin);
if (begin < 0 || end < begin) throw new Error('Purchase helper extraction failed');
const storage = new Map(), listeners = new Map(), requests = [], scripts = [];
let consent = {analytics: scenario !== 'consent_only', advertising: !['partial', 'consent_only'].includes(scenario)};
let failConfig = scenario === 'config_failure';
let releaseConfig;
const configReady = scenario === 'concurrent'
  ? new Promise(resolve => { releaseConfig = resolve; }) : Promise.resolve();
const context = {
  URL,
  location: {origin: 'https://lecturesift.com', pathname: '/account.html',
    href: 'https://lecturesift.com/account.html?order=synthetic-order&payment=success',
    search: '?order=synthetic-order&payment=success', hash: ''},
  document: {
    readyState: 'loading', documentElement: {lang: 'tr'}, referrer: '',
    querySelector: () => null,
    createElement: () => ({}), head: {append: script => scripts.push(script.src)},
    addEventListener: (name, callback) => {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(callback);
    },
  },
  LectureSiftConsent: {get: () => consent},
  sessionStorage: {
    getItem(key) {
      if (scenario === 'storage_failure') throw new Error('Storage unavailable');
      return storage.get(key) || null;
    },
    setItem(key, value) {
      if (scenario === 'storage_failure') throw new Error('Storage unavailable');
      storage.set(key, value);
    },
  },
  fetch: async url => {
    requests.push(url);
    await configReady;
    if (failConfig) throw new Error('Configuration unavailable');
    return {ok: true, json: async () => ({
      enabled: true,
      measurement_id: ['invalid_both', 'invalid_ga'].includes(scenario) ? 'invalid-id' : 'G-SYNTHETIC',
      google_ads: {
        enabled: true,
        id: ['invalid_both', 'invalid_ads'].includes(scenario) ? 'invalid-id' : 'AW-123456789',
        signup_label: 'signup-test', purchase_label: 'purchase-test',
      },
    })};
  },
};
context.window = context;
vm.createContext(context);
vm.runInContext(auth.slice(begin, end) + '\nwindow.recordPurchase = recordPurchaseAnalytics;', context);
const loadAnalytics = () => vm.runInContext(analytics, context);
const purchase = {transaction_id: 'synthetic-order', value: 59.90, currency: 'TRY',
  items: [{item_id: 'synthetic-plan', quantity: 1}]};
const tick = () => new Promise(setImmediate);
const snapshot = () => ({
  markers: Object.fromEntries(storage),
  events: (context.dataLayer || []).map(args => Array.from(args)).filter(call => call[0] === 'event'),
  queued: (context.__lecturesiftAnalyticsQueue || []).length,
  requests: requests.length,
});
const consentChanged = () => {
  for (const callback of listeners.get('lecturesift:consent') || []) callback();
};
(async () => {
  const result = {};
  if (scenario === 'deferred') {
    let settled = false;
    const first = context.recordPurchase(purchase).then(() => { settled = true; });
    const duplicate = context.recordPurchase(purchase);
    await tick();
    result.before = {...snapshot(), settled};
    loadAnalytics();
    await Promise.all([first, duplicate]);
    result.settled = settled;
  } else {
    loadAnalytics();
    if (scenario === 'concurrent') {
      const first = context.recordPurchase(purchase);
      const duplicate = context.recordPurchase(purchase);
      await tick();
      result.before = snapshot();
      releaseConfig();
      await Promise.all([first, duplicate]);
    } else {
      await context.recordPurchase(purchase);
      result.before = snapshot();
      if (scenario === 'partial' || scenario === 'consent_only') {
        consent = {analytics: true, advertising: true};
        consentChanged();
        await tick();
        result.afterConsent = snapshot();
        if (scenario === 'partial') await context.recordPurchase(purchase);
      } else if (scenario === 'config_failure') {
        failConfig = false;
        await context.recordPurchase(purchase);
      }
    }
  }
  result.final = snapshot();
  result.scripts = scripts;
  console.log(JSON.stringify(result));
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
"""


def observe(scenario):
    result = subprocess.run(
        [NODE, "-e", HARNESS, scenario, str(ROOT / "frontend/auth.js"), str(ROOT / "frontend/analytics.js")],
        check=True, capture_output=True, text=True, timeout=8,
    )
    return json.loads(result.stdout)


EVENT_KEY = "lecturesift-purchase-synthetic-order-event"
ADS_KEY = "lecturesift-purchase-synthetic-order-conversion"


def event_names(snapshot):
    return [event[1] for event in snapshot["events"]]


def test_purchase_destinations_are_marked_independently_and_only_retry_explicitly():
    row = observe("partial")
    assert row["before"]["markers"] == {EVENT_KEY: "1"}
    assert event_names(row["before"]) == ["purchase"]
    assert row["afterConsent"]["markers"] == {EVENT_KEY: "1"}
    assert event_names(row["afterConsent"]) == ["purchase"]
    assert row["final"]["markers"] == {EVENT_KEY: "1", ADS_KEY: "1"}
    assert event_names(row["final"]) == ["purchase", "conversion"]


def test_configuration_failure_leaves_destinations_unmarked_and_retryable():
    row = observe("config_failure")
    assert row["before"]["markers"] == {}
    assert row["before"]["events"] == []
    assert row["before"]["requests"] == 1
    assert row["final"]["requests"] == 2
    assert row["final"]["markers"] == {EVENT_KEY: "1", ADS_KEY: "1"}
    assert sorted(event_names(row["final"])) == ["conversion", "purchase"]


@pytest.mark.parametrize("scenario,markers,names", [
    ("invalid_both", {}, []),
    ("invalid_ga", {ADS_KEY: "1"}, ["conversion"]),
    ("invalid_ads", {EVENT_KEY: "1"}, ["purchase"]),
])
def test_invalid_destination_never_receives_a_success_marker(scenario, markers, names):
    row = observe(scenario)
    assert row["final"]["markers"] == markers
    assert event_names(row["final"]) == names


def test_deferred_analytics_loading_does_not_mark_before_accepting_each_event():
    row = observe("deferred")
    assert row["before"] == {"markers": {}, "events": [], "queued": 2, "requests": 0, "settled": False}
    assert row["settled"] is True
    assert row["final"]["queued"] == 0
    assert row["final"]["markers"] == {EVENT_KEY: "1", ADS_KEY: "1"}
    assert sorted(event_names(row["final"])) == ["conversion", "purchase"]


def test_concurrent_purchase_reporting_does_not_enqueue_duplicates():
    row = observe("concurrent")
    assert row["before"]["markers"] == {}
    assert row["before"]["events"] == []
    assert row["before"]["requests"] == 1
    assert row["final"]["markers"] == {EVENT_KEY: "1", ADS_KEY: "1"}
    assert sorted(event_names(row["final"])) == ["conversion", "purchase"]


def test_unavailable_session_storage_does_not_reject_purchase_reporting():
    row = observe("storage_failure")
    assert row["final"]["markers"] == {}
    assert sorted(event_names(row["final"])) == ["conversion", "purchase"]


def test_consent_grant_alone_does_not_replay_an_earlier_denied_purchase():
    row = observe("consent_only")
    assert row["before"]["markers"] == {}
    assert row["before"]["events"] == []
    assert row["before"]["requests"] == 0
    assert row["afterConsent"]["markers"] == {}
    assert row["afterConsent"]["events"] == []
    assert row["afterConsent"]["requests"] == 1
    assert row["final"]["events"] == []
