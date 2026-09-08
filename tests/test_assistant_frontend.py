import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_assistant_catalog_covers_all_thirteen_languages():
    text = (ROOT / "frontend/assistant-i18n.js").read_text()
    rows = re.findall(r"^\s+[a-z]+: (\[.*\]),$", text, re.MULTILINE)
    assert len(rows) >= 18
    for row in rows:
        values = json.loads(row)
        assert len(values) == 13
        assert all(value.strip() for value in values)


def test_assistant_uses_literal_text_and_allowlisted_local_actions():
    source = (ROOT / "frontend/assistant.js").read_text()
    assert "node.textContent=text" in source
    assert "link.href=path(safeActions[action])" in source
    assert "location.href=answer" not in source
    assert "history:history.slice(-6)" in source
    assert "URL.revokeObjectURL(url)" in source
    assert "sessionToken!==token()" in source


def test_credit_checkout_reuses_consent_and_provider_flow():
    source = (ROOT / "frontend/plans.js").read_text()
    assert "buy(pack.code,'one_time')" in source
    assert "button.disabled=!offers.available" in source
    assert "COUPON_PLANS.has(planCode)" in source
