"""Small offline contracts: readable palette and one price/limit catalog."""
import json
from pathlib import Path
import re
import subprocess

import pytest

from lecturesift.billing import PLANS, REGIONAL_PRICES, SUPPORTED_CURRENCIES

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def _contrast(a, b):
    def luminance(color):
        rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        return sum(v * w for v, w in zip(linear, (.2126, .7152, .0722)))
    values = sorted((luminance(a), luminance(b)))
    return (values[1] + .05) / (values[0] + .05)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_product_palette_normal_text_meets_aa(mode):
    css = (FRONTEND / "theme.css").read_text(encoding="utf-8")
    # Select the palette, not an earlier component rule mentioning data-theme.
    body = re.search(r'html\[data-theme="' + mode + r'"\] \{\s*color-scheme:[^}]+', css).group()
    colors = dict(re.findall(r'--ui-([\w-]+):\s*(#[0-9a-f]{6})', body))
    for text in ("ink", "muted", "subtle"):
        for surface in ("page", "surface", "surface-raised"):
            assert _contrast(colors[text], colors[surface]) >= 4.5, (mode, text, surface)
    assert _contrast("#202722", "#d8ef91") >= 4.5
    assert _contrast("#202722", "#e5f4b8") >= 4.5


def _frontend_catalogs():
    script = r'''
const fs = require('fs'); const vm = require('vm');
function block(src, name) { const match = src.match(new RegExp('const '+name+' = \\{[\\s\\S]*?\\n\\};')); if (!match) throw Error(name); return match[0]; }
const plans = fs.readFileSync('frontend/plans.js','utf8');
const app = fs.readFileSync('frontend/app.js','utf8');
const p = vm.runInNewContext('const ALL_SUMMARIES=["detailed"];'+['PLAN_LIMITS','FALLBACK_META','FALLBACK_PRICES'].map(n=>block(plans,n)).join('\n')+';({meta:FALLBACK_META,prices:FALLBACK_PRICES})');
const a = vm.runInNewContext(['PLAN_FALLBACK','FALLBACK_PRICES','PLAN_SOURCE_LIMITS'].map(n=>block(app,n)).join('\n')+';({meta:PLAN_FALLBACK,prices:FALLBACK_PRICES,limits:PLAN_SOURCE_LIMITS})');
console.log(JSON.stringify({p,a}));
'''
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_every_regional_fallback_price_and_paid_limit_matches_backend():
    catalogs = _frontend_catalogs()
    for name, include_test in (("p", True), ("a", False)):
        catalog = catalogs[name]
        plans = [p for p in PLANS if include_test or p.code != "test"]
        for currency in SUPPORTED_CURRENCIES:
            assert catalog["prices"][currency] == [REGIONAL_PRICES.get(p.code, {}).get(currency) for p in plans]
        for plan in plans:
            raw = catalog["meta"][plan.code]
            if name == "p":
                ent = raw["entitlements"]
                assert raw["minutes"] == ent["minutes"] == plan.minutes
                assert ent["quiz_questions"] == plan.quiz_questions
                assert ent["flashcards"] == plan.flashcards
                assert ent["history_days"] == plan.history_days
                limits = ent["limits"]
            else:
                assert raw[1:4] == [plan.minutes, plan.quiz_questions, plan.flashcards]
                limits = catalog["limits"][plan.code]
            assert limits["max_minutes_per_job"] == plan.max_minutes_per_job
            assert limits["max_files_per_job"] == plan.max_files_per_job


def test_compact_plan_details_and_translations_remain_available():
    script = (FRONTEND / "plans.js").read_text(encoding="utf-8")
    assert '<details class="plan-details"><summary>' in script
    assert 'plans.rewardedOption' not in script  # Do not sell unverified ad rewards.
    i18n = (FRONTEND / "i18n.js").read_text(encoding="utf-8")
    for key in ("plans.historyDays", "plans.allLimits", "plans.adsMayAppear", "plans.optionalLearning"):
        row = re.search(r'"' + re.escape(key) + r'":(\[[^\n]+\]),?', i18n).group(1)
        values = json.loads(row)
        assert len(values) == 13 and all(values)
