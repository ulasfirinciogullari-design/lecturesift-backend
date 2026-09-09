import json
from html.parser import HTMLParser
import re
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
LANGUAGES = ("tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi")


def _read(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


def _translation_row(script: str, key: str) -> list[str]:
    match = re.search(rf'^\s*"{re.escape(key)}":(\[.*\]),?$', script, re.MULTILINE)
    assert match, f"missing referral translation row: {key}"
    return json.loads(match.group(1))


def test_referral_copy_covers_every_supported_language() -> None:
    script = _read("referral-i18n.js")
    assert f'const languages = {json.dumps(list(LANGUAGES), separators=(",", ":"))};' in script
    keys = {
        "nav", "intro", "rules", "hold", "historyLimited", "disabled", "unavailable",
        "earned", "pending", "minutesChoice", "couponChoice", "registerLabel",
        "registerHelp", "registerDetected", "registerInvalid", "checkoutCouponLabel",
        "registerAccepted", "registerDisabled", "registerRejected", "registerUnconfirmed",
        "checkoutCouponHelp", "checkoutCouponInvalid",
    }
    for key in keys:
        row = _translation_row(script, key)
        assert len(row) == len(LANGUAGES)
        assert all(isinstance(value, str) and value.strip() for value in row)


def test_registration_accepts_only_the_server_referral_format() -> None:
    html = _read("register.html")
    script = _read("auth.js")
    assert 'pattern="LSR-[A-Fa-f0-9]{24}"' in html
    assert 'minlength="28" maxlength="28"' in html
    assert html.index("referral-i18n.js?v=2") < html.index("auth.js?v=15")
    assert '/^LSR-[A-F0-9]{24}$/' in script
    assert 'new URLSearchParams(location.search).get("ref")' in script
    assert '...(normalizedReferral ? {referral_code: normalizedReferral} : {})' in script
    assert "lecturesift-referral" not in script


def test_account_referrals_are_authenticated_and_fail_closed() -> None:
    html = _read("account.html")
    script = _read("auth.js")
    assert 'data-account-view="referrals"' in html
    assert html.index("referral-i18n.js?v=2") < html.index("auth.js?v=15")
    assert 'request("/billing/referrals", {}, token)' in script
    assert 'request("/billing/referrals/code", {method:"POST"}, token)' in script
    assert '/billing/referrals/rewards/${encodeURIComponent(reward.dataset.referralReward)}/choice' in script
    assert 'if (!value.enabled) return {enabled:false};' in script
    assert 'value.referral_url !== `https://lecturesift.com/register.html?ref=${code}`' in script
    assert 'value.history_limit !== 50' in script
    assert 'summary.rewards.filter(reward => reward.role === "inviter" && reward.status === "pending")' in script
    assert 'id="referralEarned"' in html


def test_referral_promises_are_inside_the_initially_hidden_content() -> None:
    class ReferralMarkup(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.ancestors = {}
            self.content_hidden = False

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if attrs.get("id") == "referralContent":
                self.content_hidden = "hidden" in attrs
            key = attrs.get("data-referral-i18n")
            if key in {"intro", "rules", "hold", "create"}:
                self.ancestors[key] = [item[1] for item in self.stack]
            if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
                self.stack.append((tag, attrs.get("id")))

        def handle_endtag(self, tag):
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break

    markup = ReferralMarkup()
    markup.feed(_read("account.html"))
    assert markup.content_hidden
    assert set(markup.ancestors) == {"intro", "rules", "hold", "create"}
    assert all("referralContent" in ancestors for ancestors in markup.ancestors.values())


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required")
def test_disabled_referral_response_hides_previous_enabled_content() -> None:
    script = r'''
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync('frontend/auth.js', 'utf8');
const start = source.indexOf('  const disableReferralUi =');
const end = source.indexOf('  const referralSummaryFromResponse =', start);
if (start < 0 || end < start) throw Error('referral renderer missing');
const nodes = Object.fromEntries(['referralLoading','referralContent','referralUnavailable'].map(id => [id,{hidden:false,textContent:''}]));
nodes.referralUnavailable.hidden = true;
vm.runInNewContext(source.slice(start,end) + ';renderReferralSummary({enabled:false});', {
  $:id=>nodes[id], rt:key=>key,
});
console.log(JSON.stringify(nodes));
'''
    result = subprocess.run(["node", "-e", script], cwd=ROOT, check=True,
                            capture_output=True, text=True, timeout=5)
    nodes = json.loads(result.stdout)
    assert nodes["referralContent"]["hidden"]
    assert nodes["referralLoading"]["hidden"]
    assert not nodes["referralUnavailable"]["hidden"]
    assert nodes["referralUnavailable"]["textContent"] == "disabled"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required")
def test_registration_reports_actual_referral_acceptance_without_blocking_signup() -> None:
    script = r'''
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync('frontend/auth.js', 'utf8');
const start = source.indexOf('async function initRegister()');
const end = source.indexOf('async function initLogin()', start);
if (start < 0 || end < start) throw Error('registration handler missing');
(async () => {
  const results = [];
  for (const [status, withCode] of [['accepted',true],['disabled',true],['invalid',true],['unavailable',true],['not_provided',true],[undefined,true],['unknown',true],['accepted',false]]) {
    const nodes = {}, handlers = {};
    const get = id => nodes[id] ||= {value:'',checked:true,hidden:false,textContent:'',classList:{add(){}},addEventListener:(event,handler)=>handlers[`${id}:${event}`]=handler};
    for (const id of ['password','passwordConfirm']) get(id).value = 'Synthetic123';
    get('referralCode').value = withCode ? 'LSR-' + 'A'.repeat(24) : '';
    get('successBox').hidden = true;
    get('registerReferralStatus').hidden = true;
    const context = {
      $:get, populateCountrySelect(){}, selectedCountry:()=> 'TR',
      location:{search:''}, URLSearchParams, encodeURIComponent,
      referralCode:value=>/^LSR-[A-F0-9]{24}$/.test(value) ? value : '',
      rt:key=>key, t:(_key,fallback)=>fallback, setBusy(){},
      showNotice:message=>{throw Error(message)}, recordAnalytics(){},
      localStorage:{setItem(){}}, LOCALE_DATA:{currencyForCountry:{}},
      request:async()=>({user:{email:'synthetic@example.invalid',country_code:'TR'},referral_status:status}),
    };
    await vm.runInNewContext(source.slice(start,end) + ';initRegister();', context);
    await handlers['registerForm:submit']({preventDefault(){}});
    results.push({status:status ?? 'missing',withCode,formHidden:get('registerForm').hidden,successHidden:get('successBox').hidden,notice:get('registerReferralStatus')});
  }
  console.log(JSON.stringify(results));
})().catch(error => {console.error(error);process.exitCode=1});
'''
    result = subprocess.run(["node", "-e", script], cwd=ROOT, check=True,
                            capture_output=True, text=True, timeout=5)
    rows = json.loads(result.stdout)
    expected = {"accepted": "registerAccepted", "disabled": "registerDisabled", "invalid": "registerRejected"}
    for row in rows:
        assert row["formHidden"] and not row["successHidden"]
        assert row["notice"]["hidden"] is not row["withCode"]
        assert row["notice"]["textContent"] == (
            expected.get(row["status"], "registerUnconfirmed") if row["withCode"] else ""
        )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required")
def test_account_tabs_follow_visual_arrow_direction_including_referrals() -> None:
    script = r'''
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync('frontend/auth.js','utf8');
const start = source.indexOf('  const activateAccountView =');
const finish = '  setupAccountNavigation();';
const end = source.indexOf(finish,start);
if (start < 0 || end < start) throw Error('account navigation missing');
const cases = [];
for (const dir of ['ltr','rtl']) {
  const views = JSON.parse(source.match(/const accountViews = (\[[^;]+\]);/)[1]);
  const element = dataset => ({dataset,attrs:{},events:{},setAttribute(key,value){this.attrs[key]=value},addEventListener(key,value){this.events[key]=value},focus(){this.focused=true},scrollIntoView(){}});
  const buttons = views.map(view => element({accountViewButton:view}));
  const panels = views.map(view => element({accountView:view}));
  const document = {
    documentElement:{dir},
    querySelectorAll:selector=>selector === '[data-account-view]' ? panels : buttons,
    querySelector:selector=>panels.find(panel=>selector === `[data-account-view="${panel.dataset.accountView}"]`),
  };
  vm.runInNewContext(source.slice(start,end+finish.length),{
    document,accountViews:views,accountViewKey:'test-account-view',
    history:{replaceState(){}},sessionStorage:{setItem(){},getItem:()=>null},
    location:{pathname:'/account.html',search:'',hash:''},URLSearchParams,
    window:{addEventListener(){}},
  });
  for (const [index,key] of [[views.indexOf('referrals'),'ArrowRight'],[views.length-1,'ArrowLeft'],[0,'ArrowLeft'],[views.length-1,'ArrowRight'],[3,'Home'],[3,'End']]) {
    buttons[index].events.click();
    let prevented=false;
    buttons[index].events.keydown({key,preventDefault(){prevented=true}});
    const active=buttons.findIndex(button=>button.attrs['aria-selected']==='true');
    cases.push({count:views.length,dir,index,key,active,prevented,focused:buttons[active].focused,panelVisible:!panels[active].hidden});
  }
}
console.log(JSON.stringify(cases));
'''
    result = subprocess.run(["node", "-e", script], cwd=ROOT, check=True,
                            capture_output=True, text=True, timeout=5)
    for case in json.loads(result.stdout):
        if case["key"] == "Home":
            expected = 0
        elif case["key"] == "End":
            expected = case["count"] - 1
        else:
            step = 1 if case["key"] == "ArrowRight" else -1
            expected = (case["index"] + step * (-1 if case["dir"] == "rtl" else 1)) % case["count"]
        assert case["active"] == expected
        assert case["prevented"] and case["focused"] and case["panelVisible"]


def test_checkout_passes_coupon_to_server_without_client_side_discount_math() -> None:
    html = _read("plans.html")
    script = _read("plans.js")
    assert 'id="checkoutCouponRow" hidden' in html
    assert 'pattern="LSC-[A-Fa-f0-9]{24}"' in html
    assert html.index("referral-i18n.js?v=2") < html.index("plans.js?v=23")
    assert 'const COUPON_PLANS = new Set(["lite", "plus", "pro", "max"]);' in script
    assert 'LOCALE_DATA.currencies.includes(currency) && interval === "monthly" && COUPON_PLANS.has(planCode)' in script
    assert script.count("coupon_code: couponCode") == 2
    assert '/^LSC-[A-F0-9]{24}$/' in script
    assert "coupon.percent" not in script
    assert "coupon.max_discount_minor" not in script
    assert "Kupon, verildiği para biriminde aylık Lite, Plus, Pro veya Max planında kullanılabilir" in html
    assert "Nihai indirim sipariş oluşturulurken hesaplanır" in html
