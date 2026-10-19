"""Small offline contracts: readable palette and one price/limit catalog."""
from html.parser import HTMLParser
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
    for surface in ("action", "action-hover"):
        assert _contrast(colors["action-ink"], colors[surface]) >= 4.5, (mode, surface)


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


class _StaticPlanCards(HTMLParser):
    """Collect the plan cards that plans.html shows before any API answers."""

    GRIDS = ("plansGrid", "morePlansGrid")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = {}
        self.order = {grid: [] for grid in self.GRIDS}
        self._grid = None
        self._grid_divs = 0
        self._card = None
        self._field = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and self._grid:
            self._grid_divs += 1
        elif tag == "div" and attrs.get("id") in self.GRIDS:
            self._grid, self._grid_divs = attrs["id"], 1
        if tag == "article" and "data-plan-card" in attrs:
            code = attrs["data-plan-card"]
            assert code not in self.cards, code
            self._card = {
                "classes": (attrs.get("class") or "").split(),
                "price_try_minor": attrs.get("data-price-try-minor"),
                "values": {},
                "keys": set(),
            }
            self.cards[code] = self._card
            assert self._grid, f"{code} card is outside the plan grids"
            self.order[self._grid].append(code)
        elif self._card is not None:
            if attrs.get("data-i18n"):
                self._card["keys"].add(attrs["data-i18n"])
            if tag == "span" and "data-plan-value" in attrs:
                self._field = attrs["data-plan-value"]
                assert self._field not in self._card["values"], self._field
                self._card["values"][self._field] = ""

    def handle_endtag(self, tag):
        if tag == "div" and self._grid:
            self._grid_divs -= 1
            if not self._grid_divs:
                self._grid = None
        elif tag == "article":
            self._card = None
        elif tag == "span":
            self._field = None

    def handle_data(self, data):
        if self._field:
            self._card["values"][self._field] += data


def _turkish_number(value):
    return "∞" if value is None else f"{value:,}".replace(",", ".")


def _turkish_try(amount_minor):
    whole, cents = divmod(amount_minor, 100)
    return f"₺{_turkish_number(whole)}" + (f",{cents:02d}" if cents else "")


def test_static_plan_cards_match_the_backend_try_catalog():
    parser = _StaticPlanCards()
    parser.feed((FRONTEND / "plans.html").read_text(encoding="utf-8"))
    sellable = [plan for plan in PLANS if plan.code != "test"]
    assert set(parser.cards) == {plan.code for plan in sellable}

    popular = [plan.code for plan in sellable if plan.featured and plan.kind == "subscription"]
    assert popular == ["plus"]
    highlighted = ["free", "credit", *popular]
    assert parser.order["plansGrid"] == highlighted
    assert parser.order["morePlansGrid"] == [plan.code for plan in sellable if plan.code not in highlighted]
    assert 'const HIGHLIGHTED_PLANS = ["free", "credit"];' in (FRONTEND / "plans.js").read_text(encoding="utf-8")

    for plan in sellable:
        card = parser.cards[plan.code]
        price = REGIONAL_PRICES.get(plan.code, {}).get("TRY")
        expected = {
            "max_minutes_per_job": _turkish_number(plan.max_minutes_per_job),
            "max_files_per_job": _turkish_number(plan.max_files_per_job),
            "quiz_questions": _turkish_number(plan.quiz_questions),
            "flashcards": _turkish_number(plan.flashcards),
            "history_days": _turkish_number(plan.history_days),
            "max_media_upload_mb": _turkish_number(plan.max_media_upload_mb),
            "max_document_upload_mb": _turkish_number(plan.max_document_upload_mb),
            "max_document_pages": _turkish_number(plan.max_document_pages),
            "max_ocr_pages": _turkish_number(plan.max_ocr_pages),
        }
        if plan.minutes is None:
            expected["team_seats"] = _turkish_number(plan.team_seats)
        else:
            expected["minutes"] = _turkish_number(plan.minutes)
        if price is not None:
            expected["price"] = _turkish_try(price)
            if plan.kind == "subscription":
                # create_payment_order charges ten 30-day prices for a year.
                expected["annual_price"] = _turkish_try(price * 10)
        assert card["values"] == expected, plan.code
        assert card["price_try_minor"] == (None if price is None else str(price)), plan.code
        assert ("featured" in card["classes"]) is plan.featured, plan.code

        ad_keys = {"none": "plans.adFree", "limited": "plans.limitedAds"}
        ad_key = ad_keys.get(plan.ad_mode, "plans.adsMayAppear")
        assert ad_key in card["keys"], plan.code
        assert not ({"plans.adFree", "plans.limitedAds", "plans.adsMayAppear"} - {ad_key}) & card["keys"], plan.code
        price_suffix = {"subscription": "plans.thirtyDayPackageShort", "one_time": "plans.oneTimeShort"}.get(plan.kind)
        if price_suffix:
            assert price_suffix in card["keys"], plan.code
        if plan.kind == "subscription":
            assert {"plans.minutesPer30Days", "rollout.chooseMonthly"} <= card["keys"], plan.code
        assert "plans.perMonth" not in card["keys"], plan.code
