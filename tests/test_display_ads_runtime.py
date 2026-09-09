import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DISPLAY_ADS = ROOT / "frontend" / "display-ads.js"
NODE = shutil.which("node")


NODE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const scenario = process.argv[1];
const source = fs.readFileSync(process.argv[2], "utf8");
const pathname = process.argv[3] || "/";
let consentAllowed = true;
let reloads = 0;
const fetchUrls = [];
const listeners = new Map();

class Element {
  constructor(tagName) {
    this.tagName = String(tagName).toUpperCase();
    this.children = [];
    this.dataset = {};
    this.className = "";
    this.id = "";
    this.src = "";
    this.parent = null;
  }
  append(...nodes) {
    for (const node of nodes) {
      node.parent = this;
      this.children.push(node);
      if (this.tagName === "HEAD" && node.tagName === "SCRIPT" && node.onload) {
        const delay = scenario === "consent_revocation" ? 15 : 0;
        setTimeout(() => node.onload(), delay);
      }
    }
  }
  before(node) {
    node.parent = body;
    body.children.unshift(node);
  }
  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter(candidate => candidate !== this);
    this.parent = null;
  }
  setAttribute(name, value) { this[name] = String(value); }
}

const head = new Element("head");
const body = new Element("body");
const footer = new Element("footer");
footer.parent = body;
body.children.push(footer);

function descendants(root) {
  return root.children.flatMap(child => [child, ...descendants(child)]);
}

const document = {
  readyState: "complete",
  head,
  body,
  createElement: tagName => new Element(tagName),
  querySelector(selector) {
    if (selector === "footer") return footer;
    if (selector === ".display-ad") {
      return descendants(body).find(node => node.className.split(/\s+/).includes("display-ad")) || null;
    }
    if (selector === 'script[data-lecturesift-adsense="true"]') {
      return descendants(head).find(node => node.dataset.lecturesiftAdsense === "true") || null;
    }
    return null;
  },
  addEventListener(type, listener) {
    const registered = listeners.get(type) || [];
    registered.push(listener);
    listeners.set(type, registered);
  },
  dispatchEvent(event) {
    for (const listener of listeners.get(event.type) || []) listener(event);
  },
};

function adsConfig() {
  const common = {
    adsense_auto_ads: {enabled: true, publisher_id: "ca-pub-7608481350058806"},
    house_campaign: {enabled: false},
  };
  if (scenario === "disabled_auto_ads") {
    return {...common, enabled: false, provider: null, banner_unit_path: null};
  }
  if (["adsense_enabled", "adsense_revocation"].includes(scenario)) {
    return {...common, enabled: true, provider: "google_adsense_auto", banner_unit_path: null};
  }
  return {...common, enabled: true, provider: "google_gpt", banner_unit_path: "/123/banner"};
}

const token = ["entitlement_failure", "entitlement_unknown", "consent_revocation", "plus", "legacy_paid"].includes(scenario)
  ? "signed-in-token"
  : "";

async function fetch(url) {
  fetchUrls.push(String(url));
  if (String(url).endsWith("/ads/config")) {
    return {ok: true, json: async () => adsConfig()};
  }
  if (String(url).endsWith("/billing/me")) {
    if (scenario === "entitlement_failure") throw new Error("billing unavailable");
    if (scenario === "entitlement_unknown") {
      return {ok: true, json: async () => ({account: {plan: {entitlements: {}}}})};
    }
    return {
      ok: true,
      json: async () => ({account: {plan: {entitlements: {ad_free: scenario === "legacy_paid", ad_mode: scenario === "plus" ? "limited" : "standard"}}}}),
    };
  }
  throw new Error(`unexpected fetch: ${url}`);
}

const context = {
  console,
  document,
  fetch,
  localStorage: {getItem: key => key === "lecturesift-billing-token" ? token : null},
  location: {pathname, reload: () => { reloads += 1; }},
  LectureSiftConsent: {allows: category => category === "advertising" && consentAllowed},
  setTimeout,
  clearTimeout,
  encodeURIComponent,
};
context.window = context;
vm.runInNewContext(source, context, {filename: "display-ads.js"});

if (["consent_revocation", "adsense_revocation"].includes(scenario)) {
  setTimeout(() => {
    consentAllowed = false;
    document.dispatchEvent({type: "lecturesift:consent"});
  }, 5);
}

setTimeout(() => {
  const nodes = [...descendants(head), ...descendants(body)];
  console.log(JSON.stringify({
    scripts: descendants(head).filter(node => node.tagName === "SCRIPT").map(node => node.src),
    displayAds: nodes.filter(node => node.className.split(/\s+/).includes("display-ad")).length,
    queuedGptCommands: context.googletag?.cmd?.length || 0,
    reloads,
    fetchUrls,
  }));
}, 40);
"""


def run_scenario(name: str, pathname: str = "/") -> dict:
    completed = subprocess.run(
        [NODE, "-e", NODE_HARNESS, name, str(DISPLAY_ADS), pathname],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for display-ad runtime tests")


def test_disabled_publisher_ads_cannot_be_enabled_by_nested_adsense_id() -> None:
    result = run_scenario("disabled_auto_ads")
    assert result["scripts"] == []
    assert result["displayAds"] == 0


@pytest.mark.parametrize("scenario", ["entitlement_failure", "entitlement_unknown"])
def test_signed_in_unknown_entitlement_fails_closed(scenario: str) -> None:
    result = run_scenario(scenario)
    assert any(url.endswith("/billing/me") for url in result["fetchUrls"])
    assert result["scripts"] == []
    assert result["displayAds"] == 0


def test_consent_revocation_during_gpt_load_never_queues_an_ad() -> None:
    result = run_scenario("consent_revocation")
    assert result["queuedGptCommands"] == 0
    assert result["displayAds"] == 0


def test_adsense_consent_revocation_forces_clean_reload() -> None:
    result = run_scenario("adsense_revocation")
    assert result["reloads"] == 1


def test_explicit_adsense_provider_loads_only_auto_ads() -> None:
    result = run_scenario("adsense_enabled")
    assert result["scripts"] == [
        "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-7608481350058806"
    ]
    assert result["displayAds"] == 0
    assert result["queuedGptCommands"] == 0


@pytest.mark.parametrize(
    "pathname",
    [
        "/", "/index.html", "/en/", "/ar/index.html",
        "/features", "/features/", "/features.html",
        "/plans", "/plans/", "/plans.html",
        "/about", "/about/", "/about.html",
        "/en/features/", "/ar/plans.html", "/de/about/",
    ],
)
def test_public_canonical_routes_and_html_aliases_allow_publisher_ads(pathname: str) -> None:
    result = run_scenario("gpt_enabled", pathname)
    assert result["scripts"] == ["https://securepubads.g.doubleclick.net/tag/js/gpt.js"]
    assert result["queuedGptCommands"] == 1


@pytest.mark.parametrize(
    "pathname",
    [
        "/workspace", "/workspace.html", "/en/workspace/",
        "/account", "/account.html", "/ar/account.html",
        "/admin", "/admin.html", "/de/admin/",
        "/login", "/en/login.html", "/register", "/en/register/",
        "/support", "/en/support.html", "/forgot-password.html",
        "/reset-password", "/verify", "/thanks.html",
        "/contact", "/privacy.html", "/en/plans/details", "/xx/features",
    ],
)
def test_private_and_non_whitelisted_routes_never_request_ads(pathname: str) -> None:
    result = run_scenario("adsense_enabled", pathname)
    assert result["fetchUrls"] == []
    assert result["scripts"] == []
    assert result["displayAds"] == 0


@pytest.mark.parametrize("pathname", ["/", "/en/", "/ar/index.html"])
def test_plus_only_allows_ads_on_homepage(pathname):
    assert run_scenario("plus", pathname)["scripts"] == ["https://securepubads.g.doubleclick.net/tag/js/gpt.js"]


@pytest.mark.parametrize("pathname", ["/plans", "/en/features/", "/about.html", "/workspace.html", "/assistant.html"])
def test_plus_suppresses_ads_away_from_homepage(pathname):
    result = run_scenario("plus", pathname)
    assert result["scripts"] == []
    assert result["displayAds"] == 0


def test_legacy_paid_ad_free_right_is_preserved_on_homepage():
    assert run_scenario("legacy_paid")["scripts"] == []
