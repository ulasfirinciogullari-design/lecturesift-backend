"""Exercise the real CMP adapter with synthetic Google callbacks, never a live tag."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required for consent runtime checks")

HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const scenario = process.argv[1];
const mode = scenario.startsWith('first_') ? scenario.slice(6) : scenario;
const callbacks = new Map(), windowCallbacks = new Map(), timers = new Map(), scripts = [], requests = [], events = [];
let timerId = 0, tcListener, modeReady, revocations = 0, reloads = 0, configAttempts = 0;
let values = {analyticsStoragePurposeConsentStatus: 1, adStoragePurposeConsentStatus: 1,
  adUserDataPurposeConsentStatus: 1, adPersonalizationPurposeConsentStatus: 1};
if (scenario === 'partial') values.adPersonalizationPurposeConsentStatus = 2;
if (scenario === 'denied') for (const key in values) values[key] = 2;
if (scenario === 'not_configured') for (const key in values) values[key] = 4;
if (scenario === 'unknown') for (const key in values) values[key] = 0;
if (scenario === 'mixed_statuses') values.analyticsStoragePurposeConsentStatus = 4;
const storage = new Map([['lecturesift-consent-v1', JSON.stringify({version: 1, necessary: true,
  analytics: true, advertising: true, updated_at: '2026-09-01T12:00:00.000Z', ...(scenario === 'private_precise' ? {google_consent: {
    analytics_storage: 'granted', ad_storage: 'granted', ad_user_data: 'granted', ad_personalization: 'denied'}} : {}),
  ...(scenario === 'config_rollback_denied' ? {google_consent: {
    analytics_storage: 'denied', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied'}} : {}),
  ...(scenario === 'config_rollback_local_refused' ? {google_local_denied: ['analytics_storage', 'ad_storage', 'ad_user_data', 'ad_personalization']} : {})})]]);
if (scenario.startsWith('first_')) storage.clear();
class Element {
  constructor(tag) { this.tagName = tag; this.hidden = false; this.checked = false;
    this.classList = {add() {}}; this.nodes = new Map(); this.listeners = new Map(); }
  append() {}
  focus() {}
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  querySelector(selector) { if (!this.nodes.has(selector)) this.nodes.set(selector, new Element(selector)); return this.nodes.get(selector); }
  click() { root.listeners.get('click')({target: {closest: () => ({dataset: {consent: 'manage'}})}}); }
}
let root;
const document = {
  documentElement: {lang: 'en'}, body: {append(element) { root = element; }},
  createElement: tag => new Element(tag),
  querySelector: selector => selector === 'script[nonce]' ? {nonce: 'test-nonce'} : null,
  addEventListener(name, fn) { callbacks.set(name, fn); },
  dispatchEvent(event) { events.push(event.detail); callbacks.get(event.type)?.(event); },
  head: {append(script) {
    scripts.push(script);
    if (mode === 'pending' || mode === 'timeout') return;
    if (mode === 'loader_failure') { script.onerror(); return; }
    context.googlefc.getGoogleConsentModeValues = () => values;
    context.googlefc.showRevocationMessage = () => {
      revocations++;
      tcListener({gdprApplies: true, cmpStatus: 'loaded', eventStatus: 'cmpuishown'}, true);
    };
    context.__tcfapi = (command, version, fn) => {
      if (command !== 'addEventListener' || version !== 2) throw new Error('wrong TCF subscription');
      tcListener = fn;
      fn({gdprApplies: !mode.startsWith('not_applicable'), cmpStatus: 'loaded',
        eventStatus: scenario === 'initial_action_complete' ? 'useractioncomplete' : 'tcloaded'}, scenario !== 'tcf_failure');
    };
    const queue = context.googlefc.callbackQueue;
    context.googlefc.callbackQueue = {push: item => Object.entries(item).forEach(([key, fn]) => {
      if (scenario.startsWith('late_') && key === 'CONSENT_MODE_DATA_READY') modeReady = fn;
      else fn();
    })};
    queue.forEach(item => context.googlefc.callbackQueue.push(item));
  }},
};
function createWindow(pathname) {
  const context = {
  document, console,
  location: {origin: scenario === 'preview' ? 'https://preview.example' : 'https://lecturesift.com',
    pathname, reload() { reloads++; }},
  addEventListener: (name, callback) => windowCallbacks.set(name, callback),
  localStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value)},
  CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
  AbortController,
  setTimeout(fn, delay) {
    const id = ++timerId; timers.set(id, {fn, delay});
    if (!delay) setImmediate(() => { if (timers.delete(id)) fn(); });
    return id;
  },
  clearTimeout: id => timers.delete(id),
  fetch: async url => {
    requests.push(url);
    if (mode === 'config_failure' || (mode.startsWith('config_rollback') && configAttempts++ === 0)) throw new Error('synthetic config failure');
    return {ok: true, json: async () => ({enabled: false, adsense_auto_ads: {enabled: false},
      consent: {google_cmp_enabled: mode !== 'off' && !mode.startsWith('config_rollback'),
        publisher_id: scenario === 'invalid_publisher' ? 'pub-1/../../other' : 'pub-7608481350058806'}})};
  },
  };
  context.window = context;
  return context;
}
let context = createWindow(scenario.startsWith('private') ? '/account' : '/en/pdf-note-check');
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), context);
const initial = context.LectureSiftConsent.get();
const initialBannerHidden = root.querySelector('.consent-banner').hidden;
function click(action) { root.listeners.get('click')({target: {closest: () => ({dataset: {consent: action}})}}); }
function navigate(pathname) {
  // Navigation discards the old browser Window and its callbacks. Reusing a
  // contextified object can retain provider globals from the previous page.
  callbacks.clear();
  windowCallbacks.clear();
  timers.clear();
  context = createWindow(pathname);
  vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), context);
}
(async () => {
  await new Promise(setImmediate); await new Promise(setImmediate);
  if (mode === 'timeout') for (const timer of [...timers.values()]) if (timer.delay === 12000) timer.fn();
  if (scenario === 'first_not_applicable_reload' || scenario === 'first_config_rollback') {
    navigate('/plans');
    await new Promise(setImmediate); await new Promise(setImmediate);
  }
  if (scenario === 'first_not_applicable_choose') click('all');
  if (scenario === 'revoked') {
    for (const key in values) values[key] = 2;
    tcListener({gdprApplies: true, cmpStatus: 'loaded', eventStatus: 'useractioncomplete'}, true);
    await new Promise(setImmediate);
    click('all'); // Even a stale local button must not overwrite Google's refusal.
  }
  if (scenario === 'manage' || scenario === 'manage_private') context.LectureSiftConsent.open();
  if (scenario === 'incomplete_private') {
    values.adUserDataPurposeConsentStatus = 4;
    tcListener({gdprApplies: true, cmpStatus: 'loaded', eventStatus: 'useractioncomplete'}, true);
    await new Promise(setImmediate);
  }
  if (scenario === 'manage_private' || scenario === 'incomplete_private') {
    navigate('/account');
    click('all');
  }
  if (scenario === 'local_refusal_public') {
    navigate('/account');
    click('essential');
    navigate('/plans');
    await new Promise(setImmediate); await new Promise(setImmediate);
  }
  if (scenario === 'new_decision_after_local_refusal') {
    navigate('/account');
    click('essential');
    navigate('/plans');
    await new Promise(setImmediate); await new Promise(setImmediate);
    context.LectureSiftConsent.open();
    tcListener({gdprApplies: true, cmpStatus: 'loaded', eventStatus: 'useractioncomplete'}, true);
    navigate('/plans'); // Finish the requested reload with a fresh provider API.
    await new Promise(setImmediate); await new Promise(setImmediate);
  }
  if (scenario === 'other_tab') {
    storage.set('lecturesift-consent-v1', JSON.stringify({version: 1, analytics: false, advertising: false,
      google_consent: {analytics_storage: 'denied', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied'}}));
    windowCallbacks.get('storage')({key: 'lecturesift-consent-v1'});
  }
  if (scenario.startsWith('late_')) {
    storage.set('lecturesift-consent-v1', JSON.stringify({version: 1, analytics: false, advertising: false,
      google_pending: true, google_consent: {analytics_storage: 'denied', ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied'}}));
    if (scenario !== 'late_storage_without_event') windowCallbacks.get('storage')({key: 'lecturesift-consent-v1'});
    if (scenario === 'late_tcf_after_storage') tcListener({gdprApplies: true, cmpStatus: 'loaded', eventStatus: 'tcloaded'}, true);
    modeReady(); // A callback holding the previous granted provider snapshot.
  }
  if (scenario.startsWith('config_rollback')) {
    navigate('/plans');
    await new Promise(setImmediate); await new Promise(setImmediate);
    click('all');
  }
  if (scenario === 'delayed_mode') {
    tcListener({gdprApplies: true, cmpStatus: 'loaded', eventStatus: 'useractioncomplete'}, true);
    context.googlefc.callbackQueue.push({CONSENT_MODE_DATA_READY() {}});
    // Google's previous granted snapshot is deliberately still available.
    await new Promise(setImmediate);
  }
  if (['pending', 'config_failure', 'loader_failure', 'timeout', 'private_precise'].includes(scenario)) click('all');
  console.log(JSON.stringify({initial, effective: context.LectureSiftConsent.get(),
    google: context.LectureSiftConsent.google(), scripts: scripts.map(s => ({src: s.src, nonce: s.nonce})),
    requests, revocations, reloads, initialBannerHidden, bannerHidden: root.querySelector('.consent-banner').hidden,
    saved: JSON.parse(storage.get('lecturesift-consent-v1') || 'null'), events,
    tagCalls: (context.dataLayer || []).map(args => Array.from(args))}));
})().catch(error => { console.error(error.message); process.exitCode = 1; });
"""


def run(scenario):
    completed = subprocess.run(
        [NODE, "-e", HARNESS, scenario, str(ROOT / "frontend" / "consent.js")],
        check=True, capture_output=True, text=True, timeout=8,
    )
    return json.loads(completed.stdout)


def test_cmp_loads_with_ads_disabled_without_loading_any_ad_or_measurement_script():
    result = run("granted")
    assert result["initial"]["analytics"] is False  # Ignore old grant until CMP resolves.
    assert result["effective"]["analytics"] is True
    assert result["effective"]["advertising"] is True
    assert result["scripts"] == [{"src": "https://fundingchoicesmessages.google.com/i/pub-7608481350058806?ers=1", "nonce": "test-nonce"}]
    assert result["requests"] == ["https://api.lecturesift.com/ads/config"]
    assert result["tagCalls"][0][0:2] == ["consent", "default"]
    assert result["tagCalls"][0][2]["ad_storage"] == "denied"
    assert result["bannerHidden"] is True


@pytest.mark.parametrize("scenario", ["first_not_applicable", "first_not_applicable_reload",
    "first_config_failure", "first_loader_failure", "first_timeout", "first_off", "first_config_rollback"])
def test_first_visit_keeps_local_banner_until_a_choice_exists(scenario):
    result = run(scenario)
    assert result["initialBannerHidden"] is False
    assert result["bannerHidden"] is False
    assert "updated_at" not in (result["saved"] or {})
    assert set(result["google"].values()) == {"denied"}


def test_first_non_gdpr_visitor_can_record_local_choice_and_dismiss_banner():
    result = run("first_not_applicable_choose")
    assert result["initialBannerHidden"] is False
    assert result["bannerHidden"] is True
    assert result["saved"]["updated_at"]
    assert set(result["google"].values()) == {"granted"}


def test_previous_explicit_local_choice_still_hides_non_gdpr_banner():
    result = run("not_applicable")
    assert result["initialBannerHidden"] is True
    assert result["bannerHidden"] is True
    assert set(result["google"].values()) == {"granted"}


@pytest.mark.parametrize("scenario", ["pending", "timeout", "config_failure", "loader_failure", "tcf_failure", "not_configured", "unknown", "invalid_publisher", "denied", "revoked"])
def test_pending_failed_or_denied_google_consent_never_falls_back_to_old_local_grant(scenario):
    result = run(scenario)
    assert result["effective"]["analytics"] is False
    assert result["effective"]["advertising"] is False
    assert set(result["google"].values()) == {"denied"}


@pytest.mark.parametrize("scenario", ["partial", "private_precise"])
def test_distinct_google_purposes_survive_local_storage_and_checkbox_changes(scenario):
    result = run(scenario)
    assert result["effective"]["analytics"] is True
    assert result["effective"]["advertising"] is False
    assert result["google"] == {"analytics_storage": "granted", "ad_storage": "granted", "ad_user_data": "granted", "ad_personalization": "denied"}


def test_unconfigured_google_purpose_does_not_discard_other_explicit_grants():
    result = run("mixed_statuses")
    assert result["google"] == {"analytics_storage": "denied", "ad_storage": "granted", "ad_user_data": "granted", "ad_personalization": "granted"}
    assert result["effective"]["analytics"] is False
    assert result["effective"]["advertising"] is True


@pytest.mark.parametrize("scenario", ["config_rollback", "config_rollback_denied", "config_rollback_local_refused"])
def test_config_failure_then_explicit_cmp_off_preserves_real_choices_without_inventing_a_refusal(scenario):
    result = run(scenario)
    expected = "granted" if scenario == "config_rollback" else "denied"
    assert set(result["google"].values()) == {expected}
    assert "google_pending" not in result["saved"]


@pytest.mark.parametrize("scenario", ["late_storage_event", "late_storage_without_event", "late_tcf_after_storage"])
def test_delayed_provider_writer_cannot_replace_a_newer_external_revocation(scenario):
    result = run(scenario)
    assert set(result["google"].values()) == {"denied"}
    assert result["saved"]["google_pending"] is True
    assert set(result["saved"]["google_consent"].values()) == {"denied"}


@pytest.mark.parametrize("scenario", ["off", "not_applicable", "private", "preview"])
def test_local_choices_remain_effective_when_google_cmp_is_not_applicable(scenario):
    result = run(scenario)
    assert result["effective"]["analytics"] is True
    assert result["effective"]["advertising"] is True
    if scenario != "not_applicable":
        assert result["scripts"] == []
    if scenario in {"private", "preview"}:
        assert result["requests"] == []


def test_preferences_reopen_real_google_message_and_deny_until_new_decision():
    result = run("manage")
    assert result["revocations"] == 1
    assert set(result["google"].values()) == {"denied"}


def test_initial_completed_tcf_status_does_not_cause_a_reload_loop():
    result = run("initial_action_complete")
    assert result["reloads"] == 0
    assert set(result["google"].values()) == {"granted"}


def test_explicit_new_google_decision_can_replace_a_local_refusal_after_fresh_load():
    result = run("new_decision_after_local_refusal")
    assert result["revocations"] == 1
    assert result["reloads"] == 1
    assert set(result["google"].values()) == {"granted"}
    assert "google_pending" not in result["saved"]
    assert "google_local_denied" not in result["saved"]


@pytest.mark.parametrize("scenario", ["manage_private", "incomplete_private"])
def test_private_navigation_cannot_restore_a_grant_while_google_decision_is_pending(scenario):
    result = run(scenario)
    assert result["saved"]["google_pending"] is True
    assert set(result["google"].values()) == {"denied"}
    assert result["effective"]["advertising"] is False


@pytest.mark.parametrize("scenario", ["local_refusal_public", "other_tab", "delayed_mode"])
def test_later_refusal_is_not_replaced_by_stale_granted_google_state(scenario):
    result = run(scenario)
    assert set(result["google"].values()) == {"denied"}
    assert result["events"][-1]["analytics"] is False
    assert result["events"][-1]["advertising"] is False
    if scenario == "delayed_mode":
        assert result["reloads"] == 1
