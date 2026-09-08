"""Execute the referral validator and renderer with synthetic API data only."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest
from lecturesift.referral_policy import POLICY_TERMS, REGIONAL_COUPON_CAPS_MINOR, coupon_terms_for_policy


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is required")

HARNESS = r'''
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync('frontend/auth.js', 'utf8');
const validatorStart = source.indexOf('const referralCode =');
const validatorEnd = source.indexOf('function recordAnalytics(', validatorStart);
const rendererStart = source.indexOf('  const adminSafe =');
const rendererEnd = source.indexOf('  const referralSummaryFromResponse =', rendererStart);
if ([validatorStart,validatorEnd,rendererStart,rendererEnd].some(index => index < 0)) throw Error('referral functions missing');
const html = fs.readFileSync('frontend/account.html', 'utf8');
const nodes = Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], {
  hidden:false, textContent:'', innerHTML:'', value:'', classList:{toggle(){}},
}]));
const saved = {};
const context = vm.createContext({
  console, nodes, saved, I18N:{language:'en',locale:'en-US'},currentAccount:{user:{country_code:'TR'}},
  localStorage:{getItem:key=>saved[key] || null},
  window:{LectureSiftI18n:{language:'en'}},
  document:{documentElement:{lang:'en'},querySelectorAll:()=>[]},
  $:id=>{if (!nodes[id]) throw Error(`missing real element: ${id}`); return nodes[id]},
  t:(key,fallback)=>key === 'unit.minuteShort' ? 'minutes' : fallback,
});
vm.runInContext(fs.readFileSync('frontend/referral-i18n.js','utf8'),context);
vm.runInContext(fs.readFileSync('frontend/locale-data.js','utf8'),context);
context.LOCALE_DATA = context.window.LECTURESIFT_LOCALE_DATA;
context.rt = (...args)=>context.window.LectureSiftReferralI18n.t(...args);
context.rf = (...args)=>context.window.LectureSiftReferralI18n.format(...args);
const fixtureSource = `
const V1='referral-2026-09-v1', V2='referral-2026-09-v2', RENEWAL='referral-2026-09-renewal-v1';
const fixture = () => {
const policies=JSON.parse(JSON.stringify(policyData));
const rows = [[V1,'TRY','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'],
  [V2,'EUR','cccccccc-cccc-4ccc-8ccc-cccccccccccc'],[RENEWAL,'JPY','rr-'+'b'.repeat(32)]];
return {ok:true,referrals:{
  enabled:true,referral_code:null,referral_url:null,
  reward_minutes:60,invitee_reward_minutes:30,monthly_invitation_cap:5,
  monthly_reserved_count:3,monthly_remaining_count:2,earned_minutes:0,pending_minutes:60,
  hold_days:14,history_limit:50,has_more_rewards:false,has_more_coupons:false,
  settlement:'admin_reconciliation_after_14_days',month_utc:'2026-09',
  coupon:{percent:10,max_discount_minor:5000,currency:'TRY',valid_days:90,monthly_only:true},
  renewal:{reward_minutes:30,invitee_reward_minutes:0,monthly_per_invitee_cap:1,
    coupon:{percent:5,max_discount_minor:2500,currency:'TRY',valid_days:90,monthly_only:true}},
  coupon_policies:policies,redemption_currencies:Object.keys(policies[V2]),
  rewards:rows.map(([version,currency,id],index)=>({id,policy_version:version,
    kind:version===RENEWAL?'renewal':'first_purchase',role:'inviter',status:'pending',
    reward_choice:index?'coupon':'minutes',inviter_minutes:index===2?30:60,invitee_minutes:index===2?0:30,
    coupon_currency:currency,coupon_currency_selected:true,coupon_percent:policies[version][currency].percent,
    coupon_max_discount_minor:policies[version][currency].max_discount_minor,
    created_at:'2026-09-08T12:00:00Z',pending_until:'2026-09-22T12:00:00Z'})),
  coupons:rows.map(([version,currency],index)=>({...policies[version][currency],
    code:'LSC-'+['C','D','E'][index].repeat(24),status:'ready',expires_at:'2026-12-08T12:00:00Z'})),
}};};
`;
vm.runInContext(source.slice(validatorStart,validatorEnd) + source.slice(rendererStart,rendererEnd) + fixtureSource,context);
'''


def run_js(actions):
    policies = {version: {
        currency: dict(percent=terms.coupon_percent, max_discount_minor=terms.coupon_max_minor,
                       currency=currency, valid_days=90, monthly_only=True)
        for currency in REGIONAL_COUPON_CAPS_MINOR
        if (terms := coupon_terms_for_policy(version, currency)) is not None
    } for version in POLICY_TERMS}
    script = HARNESS + f"\ncontext.policyData={json.dumps(policies)}; vm.runInContext({json.dumps(actions)},context);"
    result = subprocess.run(["node", "-e", script], cwd=ROOT, check=True,
                            capture_output=True, text=True, timeout=5)
    return json.loads(result.stdout)


def test_mixed_first_and_renewal_summary_is_accepted():
    result = run_js("const value=validatedReferralSummary(fixture()); console.log(JSON.stringify(value));")
    assert result["enabled"] is True
    assert [row["kind"] for row in result["rewards"]] == ["first_purchase", "first_purchase", "renewal"]
    assert [row["inviter_minutes"] for row in result["rewards"]] == [60, 60, 30]
    assert [row["currency"] for row in result["coupons"]] == ["TRY", "EUR", "JPY"]
    assert len(result["coupon_policies"]["referral-2026-09-v2"]) == 22


def test_mismatched_reward_coupon_and_identifier_terms_are_rejected():
    rejected = run_js(r'''
const mutations = [
  value=>delete value.renewal,
  value=>value.renewal.reward_minutes=60,
  value=>value.renewal.invitee_reward_minutes=30,
  value=>value.renewal.monthly_per_invitee_cap=2,
  value=>value.renewal.coupon.max_discount_minor=5000,
  value=>value.rewards[0].kind='renewal',
  value=>value.rewards[0].id='rr-'+'a'.repeat(32),
  value=>value.rewards[2].id='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  value=>value.rewards[2].id='rr-'+'x'.repeat(32),
  value=>value.rewards[2].inviter_minutes=60,
  value=>value.rewards[2].invitee_minutes=30,
  value=>value.rewards[2].coupon_percent=10,
  value=>value.rewards[2].coupon_max_discount_minor=5000,
  value=>value.rewards[2].coupon_currency_selected='yes',
  value=>value.rewards[1].policy_version=V1,
  value=>value.coupon_policies[V2].EUR.currency='USD',
  value=>value.coupon_policies[V1].EUR=value.coupon_policies[V2].EUR,
  value=>value.coupon_policies[RENEWAL].JPY.max_discount_minor=-1,
  value=>value.redemption_currencies.push('BTC'), value=>value.redemption_currencies.push('TRY'),
  value=>{value.coupon_policies.unknown={USD:{percent:99,max_discount_minor:999999}};
    Object.assign(value.coupons[0],{currency:'USD',percent:99,max_discount_minor:999999})},
  value=>value.rewards.push({...value.rewards[1]}),
  value=>value.coupons[0].percent=5,
  value=>value.coupons[1].max_discount_minor=5000,
  value=>value.coupons[1].currency='USD',
  value=>value.coupons[1].code=value.coupons[0].code.toLowerCase(),
];
console.log(JSON.stringify(mutations.map(mutate=>{
  const body=fixture(); mutate(body.referrals); return validatedReferralSummary(body) === null;
})));
''')
    assert rejected and all(rejected)


def test_enabled_rendering_separates_choices_history_and_both_coupon_values():
    nodes = run_js("renderReferralSummary(validatedReferralSummary(fixture())); console.log(JSON.stringify(nodes));")
    choices = nodes["referralRewards"]["innerHTML"]
    history = nodes["referralHistory"]["innerHTML"]
    coupons = nodes["referralCoupons"]["innerHTML"]
    assert not nodes["referralContent"]["hidden"]
    assert nodes["referralUnavailable"]["hidden"]
    assert "Choose 60 minutes" in choices and "Choose 30 minutes" in choices
    assert "Choose 10% coupon" in choices and "Choose 5% coupon" in choices
    assert 'data-referral-reward="rr-' in choices
    assert "First subscription purchase" in history and "Later purchase / renewal" in history
    assert history.count("<article ") == 3 and 'data-referral-choice=' not in history
    assert "Pending" in history and "60 minutes" in history and "Coupon: 5%" in history
    assert "Coupon: 10%" in coupons and "50.00" in coupons
    assert "€1.41" in coupons and "Coupon: 5%" in coupons and "¥94" in coupons
    assert nodes["referralEarned"]["textContent"] == "0 minutes"


def test_blocked_and_limit_decisions_never_display_an_earned_benefit():
    rows = run_js(r'''
console.log(JSON.stringify(['blocked','cap_reached','monthly_limit'].map(status=>{
  const body=fixture(), value=body.referrals;
  value.rewards.forEach(reward=>{reward.status=status});
  value.coupons=[]; value.pending_minutes=0; value.monthly_reserved_count=0; value.monthly_remaining_count=5;
  renderReferralSummary(validatedReferralSummary(body));
  return {status,history:nodes.referralHistory.innerHTML,choices:nodes.referralRewards.innerHTML,
    earned:nodes.referralEarned.textContent,pending:nodes.referralPending.textContent};
})));
''')
    for row in rows:
        assert row["earned"] == row["pending"] == "0 minutes"
        assert "<button" not in row["choices"]
        assert row["history"].count("<small>—</small>") == 3
        assert "60 minutes" not in row["history"] and "30 minutes" not in row["history"]
        assert "Coupon:" not in row["history"]


def test_disabled_response_hides_previously_rendered_rewards_and_coupons():
    nodes = run_js(r'''
renderReferralSummary(validatedReferralSummary(fixture()));
renderReferralSummary(validatedReferralSummary({ok:true,referrals:{enabled:false}}));
console.log(JSON.stringify(nodes));
''')
    assert nodes["referralContent"]["hidden"] and nodes["referralLoading"]["hidden"]
    assert not nodes["referralUnavailable"]["hidden"]
    assert nodes["referralUnavailable"]["textContent"]


def test_currency_format_preference_and_provider_availability_are_independent():
    result = run_js(r'''
const formatted=['USD','EUR','JPY','KRW'].map(currency=>{
  const terms=policyData[V2][currency]; return referralCouponValue(terms.percent,terms.max_discount_minor,currency);
});
const body=fixture(), reward=body.referrals.rewards[1];
Object.assign(reward,{coupon_currency:'TRY',coupon_max_discount_minor:5000,coupon_currency_selected:false,reward_choice:null});
const selections=()=>[...nodes.referralRewards.innerHTML.matchAll(/<option value="([^"]+)" selected>/g)].map(match=>match[1]);
saved['lecturesift-currency']='EUR'; renderReferralSummary(validatedReferralSummary(body)); const eur=selections();
delete saved['lecturesift-currency']; renderReferralSummary(validatedReferralSummary(body)); const fallback=selections();
body.referrals.redemption_currencies=['TRY','EUR']; renderReferralSummary(validatedReferralSummary(body));
const renewal=[...nodes.referralRewards.innerHTML.matchAll(/<article[\s\S]*?<\/article>/g)][2][0];
const buttons=[...renewal.matchAll(/<button[^>]+>/g)].map(match=>match[0]);
console.log(JSON.stringify({formatted,eur,fallback,buttons}));
''')
    assert all(expected in value for expected, value in zip(["$1.50", "€1.41", "¥188", "₩1,702"], result["formatted"]))
    assert result["eur"] == ["TRY", "EUR", "JPY"]
    assert result["fallback"] == ["TRY", "TRY", "JPY"]
    assert 'data-referral-choice="minutes"' in result["buttons"][0] and "disabled" not in result["buttons"][0]
    assert 'data-referral-choice="coupon"' in result["buttons"][1] and "disabled" in result["buttons"][1]
