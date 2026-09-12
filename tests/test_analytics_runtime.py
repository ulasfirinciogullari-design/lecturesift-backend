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
  const scripts = [], requests = [], listeners = new Map();
  const context = {
    location: {pathname, origin: 'https://lecturesift.com', search: '?token=private-value', hash: '#private-fragment'},
    document: {
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
      return {ok: true, json: async () => ({
        enabled: true, measurement_id: 'G-SYNTHETIC',
        google_ads: {enabled: scenario !== 'ads_disabled', id: 'AW-123456789', signup_label: 'signup-test', purchase_label: 'purchase-test'},
      })};
    },
  };
  context.window = context;
  vm.runInNewContext(source, context);
  await context.LectureSiftAnalytics.refresh();
  const event = await context.LectureSiftAnalytics.track('test_event', {value: 1});
  const conversion = await context.LectureSiftAnalytics.trackConversion('purchase', {transaction_id: 'synthetic-order', value: 59.90, currency: 'TRY'});
  await context.LectureSiftAnalytics.refresh();
  const beforeRevoke = context.dataLayer?.length || 0;
  if (scenario === 'revoked') {
    consent = {analytics: false, advertising: false};
    listeners.get('lecturesift:consent')();
    await context.LectureSiftAnalytics.refresh();
    await context.LectureSiftAnalytics.track('after_revoke');
    await context.LectureSiftAnalytics.trackConversion('signup');
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


def observe(paths, scenario="allowed"):
    result = subprocess.run(
        [NODE, "-e", HARNESS, json.dumps(paths), scenario, str(ROOT / "frontend/analytics.js")],
        check=True, capture_output=True, text=True, timeout=8,
    )
    return json.loads(result.stdout)


def configurations(row):
    return {call[1]: call[2] for call in row["calls"] if call[0] == "config"}


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
