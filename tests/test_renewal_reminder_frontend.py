"""Workspace nudge for a paid package that ends within seven days."""

import json
from pathlib import Path
import re

from lecturesift.plan_copy import LANGUAGES


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def _nudge_copy() -> dict[str, list[str]]:
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    block = re.search(r"const RENEWAL_NUDGE = \{\n(.*?)\n\};", script, re.DOTALL)
    assert block, "RENEWAL_NUDGE table is missing"
    rows = re.findall(r'^\s*([a-z]{2}): (\[.*\]),$', block.group(1), re.MULTILINE)
    return {language: json.loads(payload) for language, payload in rows}


def test_nudge_copy_covers_every_interface_language():
    copy = _nudge_copy()
    assert tuple(copy) == LANGUAGES
    for language, values in copy.items():
        assert len(values) == 3, language
        assert all(value.strip() for value in values), language
        assert "{days}" in values[0] and "{days}" not in values[1], language
    assert copy["tr"] == ["Paketin {days} gün sonra bitiyor", "Paketin bugün bitiyor", "Yenile"]
    assert len({values[0] for values in copy.values()}) == 13


def test_nudge_reads_the_account_subscription_and_links_to_the_same_package():
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "renderRenewalNudge(loggedIn);" in script
    assert script.index("renderRenewalNudge(loggedIn);") < script.index("if (!loggedIn) return;\n  if ($(\"accountEmail\"))")
    nudge = script[script.index("function renewalDaysLeft"):script.index("async function billingRequest")]
    assert '["monthly", "annual"].includes(subscription.interval)' in nudge
    assert "subscription.ends_at" in nudge
    assert "days === null || days > 7" in nudge
    assert 'node.id = "renewalNudge"; node.className = "renewal-nudge";' in nudge
    assert 'host.prepend(node)' in nudge and 'const host = $("app");' in nudge
    assert 'new URLSearchParams({plan: billingAccount.plan?.code || "", interval: billingAccount.subscription.interval})' in nudge
    assert 'localizedPath?.(currentLanguage, "/plans.html") || "/plans.html"}?${query}' in nudge
    assert "textContent" in nudge and "innerHTML" not in nudge


def test_workspace_loads_the_versions_that_carry_the_nudge():
    page = (FRONTEND / "workspace.html").read_text(encoding="utf-8")
    style = (FRONTEND / "library.css").read_text(encoding="utf-8")
    assert 'src="./app.js?v=47"' in page
    assert 'href="/library.css?v=2"' in page
    assert ".renewal-nudge{" in style and ".renewal-nudge[hidden]{display:none}" in style
    assert 'id="renewalNudge"' not in page
