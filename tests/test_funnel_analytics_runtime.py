"""Run the real funnel helper in analytics.js without contacting Google or the API."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required for measurement runtime checks")
ORIGIN = "https://lecturesift.com"

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const options = JSON.parse(process.argv[1]);
const source = fs.readFileSync(process.argv[2], 'utf8');
(async () => {
  let consent = {analytics: options.analytics, advertising: options.advertising};
  const scripts = [], requests = [], listeners = new Map(), queued = [];
  const context = {
    URL,
    location: {pathname: options.path, origin: options.origin,
      href: `${options.origin}${options.path}?job=private-job-id#study`,
      search: '?job=private-job-id', hash: '#study'},
    document: {
      referrer: '', documentElement: {lang: 'en'}, readyState: 'complete',
      createElement: () => ({}), querySelector: () => null,
      head: {append: element => scripts.push(element.src)},
      addEventListener: (name, callback) => listeners.set(name, callback),
    },
    LectureSiftConsent: options.consentLate ? undefined : {get: () => consent},
    __lecturesiftAnalyticsQueue: (options.queued || []).map(([name, parameters]) => (
      {type: 'event', name, parameters, resolve: value => queued.push(value)})),
    fetch: async url => {
      requests.push(url);
      return {ok: true, json: async () => ({
        enabled: true, measurement_id: 'G-SYNTHETIC',
        google_ads: {enabled: true, id: 'AW-123456789', signup_label: 'signup-test', purchase_label: 'purchase-test'},
      })};
    },
  };
  context.window = context;
  vm.runInNewContext(source, context);
  const pending = (options.calls || []).map(([name, parameters]) => context.lectureSiftTrack(name, parameters));
  let early = null;
  if (options.consentLate) {
    // consent.js finishes loading after analytics.js and the page's first calls.
    await new Promise(setImmediate);
    early = {queued: [...queued], events: (context.dataLayer || []).filter(args => args[0] === 'event').length,
      requests: requests.length};
    context.LectureSiftConsent = {get: () => consent};
    listeners.get('lecturesift:consent-ready')();
  }
  const results = [];
  for (const promise of pending) results.push(await promise);
  const beforeRevoke = (context.dataLayer || []).length;
  if (options.afterRevoke) {
    consent = {analytics: false, advertising: false};
    listeners.get('lecturesift:consent')();
    results.push(await context.lectureSiftTrack(...options.afterRevoke));
  }
  await new Promise(setImmediate);
  console.log(JSON.stringify({results, queued, scripts, requests, beforeRevoke, early,
    calls: (context.dataLayer || []).map(args => Array.from(args))}));
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
"""


def observe(path, *, analytics=True, advertising=True, origin=ORIGIN, calls=(), queued=(), after_revoke=None,
            consent_late=False):
    options = {"path": path, "analytics": analytics, "advertising": advertising, "origin": origin,
               "calls": list(calls), "queued": list(queued), "afterRevoke": after_revoke, "consentLate": consent_late}
    result = subprocess.run(
        [NODE, "-e", HARNESS, json.dumps(options), str(ROOT / "frontend/analytics.js")],
        check=True, capture_output=True, text=True, timeout=8,
    )
    return json.loads(result.stdout)


def events(row):
    return [call[1:] for call in row["calls"] if call[0] == "event"]


def configurations(row):
    return {call[1]: call[2] for call in row["calls"] if call[0] == "config"}


TRIAL = [["trial_start", {"source_type": "document"}]]


@pytest.mark.parametrize("path", ["/workspace.html", "/en/workspace", "/login.html", "/plans", "/account.html", "/register"])
@pytest.mark.parametrize("advertising", [False, True])
def test_helper_sends_nothing_without_analytics_consent(path, advertising):
    row = observe(path, analytics=False, advertising=advertising, calls=TRIAL + [["login", {"method": "email"}]])
    assert row["results"] == [False, False]
    assert events(row) == []
    assert "G-SYNTHETIC" not in configurations(row)
    if not advertising or "workspace" in path:
        assert row["requests"] == [] and row["scripts"] == [] and row["calls"] == []


def test_helper_fires_on_the_workspace_with_consent_but_loads_no_ads_tag_or_page_view():
    row = observe("/en/workspace.html", calls=TRIAL)
    assert row["results"] == [True]
    assert row["scripts"] == ["https://www.googletagmanager.com/gtag/js?id=G-SYNTHETIC"]
    assert list(configurations(row)) == ["G-SYNTHETIC"]
    assert configurations(row)["G-SYNTHETIC"]["send_page_view"] is False
    assert events(row) == [["trial_start", {
        "source_type": "document", "page_location": f"{ORIGIN}/en/workspace.html",
        "page_referrer": "", "send_to": "G-SYNTHETIC",
    }]]
    assert "private-job-id" not in json.dumps(row["calls"])


def test_workspace_still_never_starts_measurement_on_load():
    row = observe("/workspace.html")
    assert row["requests"] == [] and row["scripts"] == [] and row["calls"] == []


def test_sign_in_page_measures_login_without_a_page_view_or_url_values():
    row = observe("/en/login.html", calls=[["login", {"method": "email"}]])
    assert row["results"] == [True]
    assert configurations(row)["G-SYNTHETIC"]["send_page_view"] is False
    login, = [event for event in events(row) if event[0] == "login"]
    assert login[1]["method"] == "email"
    assert login[1]["page_location"] == f"{ORIGIN}/en/login.html"
    assert "private-job-id" not in json.dumps(row["calls"])


def test_helper_drops_identifying_and_unknown_parameters():
    row = observe("/account.html", calls=[["purchase", {
        "transaction_id": "LS20261019ABCDEF123456", "value": 59.9, "currency": "TRY",
        "items": [{"item_id": "plus", "item_name": "Plus", "quantity": 1, "item_brand": "private"}],
        "email": "student@example.invalid", "file_name": "private-notes.pdf", "user_id": "private-user",
        "method": "student@example.invalid", "source_type": "private-notes.pdf",
        "reason": "https://example.invalid/private?token=secret", "payment_type": "C:\\private",
        "content_type": "x" * 101,
    }], ["result_download", {"content_type": "package", "file_extension": "zip", "value": {"name": "private"}}]])
    assert row["results"] == [True, True]
    (purchase_name, purchase), (download_name, download) = events(row)
    assert purchase_name == "purchase" and download_name == "result_download"
    for parameters in (purchase, download):
        assert parameters.pop("page_referrer") == ""
        assert parameters.pop("page_location") == f"{ORIGIN}/account.html"
        assert parameters.pop("send_to") == "G-SYNTHETIC"
    assert purchase == {"transaction_id": "LS20261019ABCDEF123456", "value": 59.9, "currency": "TRY",
                        "items": [{"item_id": "plus", "item_name": "Plus", "quantity": 1}]}
    assert download == {"content_type": "package", "file_extension": "zip"}
    for private in ("private", "example.invalid", "secret"):
        assert private not in json.dumps(row["calls"])


@pytest.mark.parametrize("name", ["page_view", "content_action", "conversion", "", "trial_start "])
def test_unknown_event_names_are_ignored(name):
    row = observe("/en/workspace.html", calls=[[name, {}]])
    assert row["results"] == [False]
    assert row["requests"] == [] and row["calls"] == []


@pytest.mark.parametrize("origin", ["http://127.0.0.1:4173", "https://deploy-preview-1--clever-horse-22b1a8.netlify.app"])
def test_previews_never_send_funnel_events(origin):
    row = observe("/workspace.html", origin=origin, calls=TRIAL)
    assert row["results"] == [False]
    assert row["requests"] == [] and row["scripts"] == [] and row["calls"] == []


@pytest.mark.parametrize("path", ["/verify.html", "/reset-password", "/admin.html", "/support.html", "/assistant.html"])
def test_private_pages_outside_the_funnel_stay_silent(path):
    row = observe(path, calls=TRIAL + [["purchase", {"value": 1, "currency": "TRY"}]])
    assert row["results"] == [False, False]
    assert row["requests"] == [] and row["calls"] == []


@pytest.mark.parametrize("analytics", [False, True])
def test_calls_queued_before_loading_are_checked_against_consent(analytics):
    row = observe("/workspace.html", analytics=analytics, queued=TRIAL)
    assert row["queued"] == [analytics]
    assert [event[0] for event in events(row)] == (["trial_start"] if analytics else [])


@pytest.mark.parametrize("analytics", [False, True])
def test_events_before_consent_is_ready_wait_for_the_saved_choice(analytics):
    row = observe("/workspace.html", analytics=analytics, consent_late=True, queued=TRIAL,
                  calls=[["trial_limit_reached", {"reason": "trial_used"}]])
    assert row["early"] == {"queued": [], "events": 0, "requests": 0}
    assert row["queued"] == [analytics]
    assert row["results"] == [analytics]
    assert [event[0] for event in events(row)] == (["trial_start", "trial_limit_reached"] if analytics else [])


def test_revoking_consent_stops_later_funnel_events():
    row = observe("/workspace.html", calls=TRIAL, after_revoke=["trial_complete", {"source_type": "document"}])
    assert row["results"] == [True, False]
    later = row["calls"][row["beforeRevoke"]:]
    assert not any(call[0] in {"event", "config"} for call in later)
    assert later[-1][2]["analytics_storage"] == "denied"
