"""Exercise real measurement code without contacting Google or the live API."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required for measurement runtime checks")

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const paths = JSON.parse(process.argv[1]);
const scenario = process.argv[2];
const source = fs.readFileSync(process.argv[3], 'utf8');
async function run(pathname) {
  let consent = {analytics: scenario !== 'ads_only', advertising: scenario !== 'analytics_only'};
  if (scenario === 'denied') consent = {analytics: false, advertising: false};
  const pending = /^pending_(cold|warm)_(analytics|advertising|both)$/.exec(scenario);
  let releaseConfig;
  const configReady = pending?.[1] === 'cold'
    ? new Promise(resolve => { releaseConfig = resolve; })
    : Promise.resolve();
  const scripts = [], requests = [], listeners = new Map();
  const context = {
    URL,
    location: {pathname, origin: process.argv[4], href: process.argv[4] + pathname, search: '?token=private-value', hash: '#private-fragment'},
    document: {
      documentElement: {lang: pathname.startsWith('/en/') ? 'en' : 'tr'},
      readyState: 'complete',
      createElement: () => ({}),
      querySelector: selector => selector === 'script[nonce]' ? {nonce: 'preview-nonce'} : null,
      head: {append: element => scripts.push(element)},
      addEventListener: (name, callback) => listeners.set(name, callback),
    },
    LectureSiftConsent: {get: () => consent},
    fetch: async url => {
      requests.push(url);
      if (scenario === 'unavailable') throw new Error('synthetic unavailable config');
      await configReady;
      return {ok: true, json: async () => ({
        enabled: true, measurement_id: 'G-SYNTHETIC',
        google_ads: {enabled: scenario !== 'ads_disabled', id: 'AW-123456789', signup_label: 'signup-test', purchase_label: 'purchase-test'},
      })};
    },
  };
  context.window = context;
  vm.runInNewContext(source, context);
  if (!pending || pending[1] === 'warm') await context.LectureSiftAnalytics.refresh();
  const eventPromise = context.LectureSiftAnalytics.track('test_event', {value: 1});
  const conversionPromise = context.LectureSiftAnalytics.trackConversion('purchase', {transaction_id: 'synthetic-order', value: 59.90, currency: 'TRY'});
  if (pending) {
    consent = {
      analytics: pending[2] === 'advertising',
      advertising: pending[2] === 'analytics',
    };
    listeners.get('lecturesift:consent')();
    releaseConfig?.();
  }
  const [event, conversion] = await Promise.all([eventPromise, conversionPromise]);
  await context.LectureSiftAnalytics.refresh();
  const clickContent = async () => {
    for (const item of JSON.parse(process.argv[5])) {
      const link = {href: item.href,
        hasAttribute: name => name === 'download' && !!item.download,
        closest: selector => selector === item.placement ? {} : null};
      listeners.get('click')({target: {closest: selector => selector === 'a[href]' ? link : null}});
    }
    await new Promise(setImmediate);
  };
  await clickContent();
  const beforeRevoke = context.dataLayer?.length || 0;
  if (scenario === 'revoked') {
    consent = {analytics: false, advertising: false};
    listeners.get('lecturesift:consent')();
    await context.LectureSiftAnalytics.refresh();
    await context.LectureSiftAnalytics.track('after_revoke');
    await context.LectureSiftAnalytics.trackConversion('signup');
    await clickContent();
  }
  return {pathname, scripts: scripts.map(element => element.src),
    scriptNonces: scripts.map(element => element.nonce || ''), requests, event, conversion, beforeRevoke,
    calls: (context.dataLayer || []).map(args => Array.from(args))};
}
(async () => {
  const rows = [];
  for (const pathname of paths) rows.push(await run(pathname));
  console.log(JSON.stringify(rows));
})().catch(error => { console.error(error.message); process.exitCode = 1; });
"""


def observe(paths, scenario="allowed", origin="https://lecturesift.com", links=()):
    result = subprocess.run(
        [NODE, "-e", HARNESS, json.dumps(paths), scenario, str(ROOT / "frontend/analytics.js"), origin, json.dumps(links)],
        check=True, capture_output=True, text=True, timeout=8,
    )
    return json.loads(result.stdout)


def configurations(row):
    return {call[1]: call[2] for call in row["calls"] if call[0] == "config"}


@pytest.mark.parametrize("origin", [
    "http://localhost:4173", "http://127.0.0.1:4173",
    "https://deploy-preview-107--clever-horse-22b1a8.netlify.app",
    "https://lecturesift.com.example.invalid", "https://preview.lecturesift.com",
])
def test_preview_and_local_origins_never_start_measurement(origin):
    for row in observe(["/", "/en/document-summary", "/register", "/account"], "revoked", origin):
        assert row["scripts"] == []
        assert row["requests"] == []
        assert row["calls"] == []
        assert row["event"] is False
        assert row["conversion"] is False


def test_www_production_origin_still_measures_with_consent():
    row, = observe(["/en/document-summary"], origin="https://www.lecturesift.com")
    assert row["event"] is True
    assert configurations(row)["G-SYNTHETIC"]["send_page_view"] is True


def test_all_published_guides_have_consent_gated_pageviews():
    manifest = json.loads((ROOT / "frontend/study-resources.json").read_text())
    paths = [f'{prefix}/{page["slug"]}{suffix}' for page in manifest["pages"]
             for prefix in ["", "/en"] for suffix in ["", ".html"]]
    for row in observe(paths):
        assert configurations(row)["G-SYNTHETIC"]["send_page_view"] is True, row["pathname"]
        assert len(row["requests"]) == 1
    for row in observe(paths, "denied"):
        assert row["requests"] == [] and row["scripts"] == []
        assert not row["event"] and not row["conversion"]


def test_content_intent_uses_fixed_labels_and_strips_link_query_values():
    row, = observe(["/en/cornell-notes"], links=[
        {"href": "/en/workspace.html?file=private-filename#private", "placement": "header"},
        {"href": "/en/lecture-video-summary?email=private@example.invalid"},
        {"href": "/assets/study/cornell-notes-en.txt?private=value", "download": True},
        {"href": "/en/plans#compare"},
        {"href": "/en/register.html?token=private"},
        {"href": "https://external.example.invalid/workspace.html"},
        {"href": "/en/account.html?token=private"},
        {"href": "/en/cornell-notes#template"},
        {"href": "/assets/study/private-file.txt", "download": True},
    ])
    events = [c[2] for c in row["calls"] if c[:2] == ["event", "content_action"]]
    assert [e["action"] for e in events] == ["open_workspace", "read_related", "download_resource", "view_plans", "open_registration"]
    assert [e["target_path"] for e in events] == ["/workspace", "/lecture-video-summary", "/assets/study/cornell-notes-en.txt", "/plans", "/register"]
    assert events[0]["link_placement"] == "header"
    for event in events:
        assert event["content_id"] == "/cornell-notes"
        assert event["content_language"] == "en"
        assert event["page_location"] == "https://lecturesift.com/en/cornell-notes"
        assert event["send_to"] == "G-SYNTHETIC"
        assert "private" not in json.dumps(event)


@pytest.mark.parametrize("scenario", ["denied", "ads_only", "unavailable", "revoked"])
def test_content_intent_respects_analytics_consent_and_revocation(scenario):
    row, = observe(["/cornell-notes"], scenario, links=[{"href": "/workspace.html"}])
    events = [c for c in row["calls"] if c[:2] == ["event", "content_action"]]
    assert len(events) == (1 if scenario == "revoked" else 0)
    if scenario == "revoked":
        assert not any(c[0] == "event" for c in row["calls"][row["beforeRevoke"]:])


@pytest.mark.parametrize("path,origin", [
    ("/cornell-notes", "https://preview.lecturesift.com"),
    ("/workspace.html", "https://lecturesift.com"),
    ("/unknown-guide", "https://lecturesift.com"),
])
def test_content_intent_stays_off_preview_private_and_unknown_pages(path, origin):
    row, = observe([path], origin=origin, links=[{"href": "/workspace.html"}])
    assert row["requests"] == [] and row["calls"] == []


def test_all_localized_clean_and_legacy_public_routes_measure_once():
    languages = ["", "tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi"]
    routes = ["/", "/index.html", "/plans", "/plans.html", "/features/",
              "/document-summary", "/lecture-video-summary", "/quiz-flashcards"]
    paths = [f"/{language}{route}" if language else route for language in languages for route in routes]
    for row in observe(paths):
        assert len(row["scripts"]) == 1, row["pathname"]
        assert len(row["requests"]) == 1
        assert configurations(row)["G-SYNTHETIC"]["send_page_view"] is True
        assert len([call for call in row["calls"] if call[0] == "config"]) == 2
        assert row["event"] is True and row["conversion"] is True


def test_google_tag_copies_the_static_preview_nonce():
    row, = observe(["/plans"])
    assert row["scriptNonces"] == ["preview-nonce"]


def test_private_token_pages_and_workspace_never_start_measurement():
    paths = ["/verify.html", "/en/verify", "/reset-password", "/tr/reset-password.html",
             "/admin.html", "/workspace.html", "/en/workspace", "/support.html", "/assistant.html"]
    for row in observe(paths):
        assert row["scripts"] == [] and row["requests"] == []
        assert row["calls"] == []
        assert not row["event"] and not row["conversion"]


def test_account_and_registration_convert_without_automatic_pageviews_or_private_url_values():
    for row in observe(["/account.html", "/en/account", "/register", "/ar/register.html"]):
        assert configurations(row)["G-SYNTHETIC"]["send_page_view"] is False
        for call in row["calls"]:
            if call[0] in {"event", "config"}:
                assert call[2]["page_location"] == "https://lecturesift.com" + row["pathname"]
        conversion = next(call for call in row["calls"] if call[:2] == ["event", "conversion"])
        assert conversion[2]["send_to"] == "AW-123456789/purchase-test"
        assert conversion[2]["currency"] == "TRY" and conversion[2]["value"] == 59.9
        assert row["conversion"] is True


@pytest.mark.parametrize("scenario", ["denied", "unavailable"])
def test_no_tag_or_event_when_consent_or_configuration_is_unavailable(scenario):
    row, = observe(["/en/plans"], scenario)
    assert row["scripts"] == []
    assert not row["event"] and not row["conversion"]
    assert not any(call[0] == "event" for call in row["calls"])


@pytest.mark.parametrize("scenario,analytics,ads", [
    ("analytics_only", True, False), ("ads_only", False, True), ("ads_disabled", True, False),
])
def test_analytics_and_advertising_permissions_are_independent(scenario, analytics, ads):
    row, = observe(["/de/plans"], scenario)
    assert row["event"] is analytics
    assert row["conversion"] is ads
    assert ("G-SYNTHETIC" in configurations(row)) is analytics
    assert ("AW-123456789" in configurations(row)) is ads


def test_revoking_consent_prevents_subsequent_events():
    row, = observe(["/plans"], "revoked")
    later = row["calls"][row["beforeRevoke"]:]
    assert not any(call[0] in {"event", "config"} for call in later)
    assert later[-1][2]["ad_storage"] == "denied"
    assert later[-1][2]["analytics_storage"] == "denied"


@pytest.mark.parametrize("configuration", ["cold", "warm"])
@pytest.mark.parametrize("revoked,analytics,advertising", [
    ("analytics", False, True),
    ("advertising", True, False),
    ("both", False, False),
])
def test_revoking_consent_cancels_pending_events(configuration, revoked, analytics, advertising):
    row, = observe(["/plans"], f"pending_{configuration}_{revoked}")
    assert row["event"] is analytics
    assert row["conversion"] is advertising
    events = [call[1] for call in row["calls"] if call[0] == "event"]
    assert events.count("test_event") == int(analytics)
    assert events.count("conversion") == int(advertising)
    assert len(row["requests"]) == 1
    if configuration == "cold" and revoked == "both":
        assert row["scripts"] == []
