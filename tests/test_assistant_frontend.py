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
    assert "answer.path" not in source
    assert "history:history.slice(-6)" in source
    assert "URL.revokeObjectURL(url)" in source
    assert "sessionToken!==token()" in source


def test_assistant_preferences_require_fixed_confirmation_and_server_owned_lesson_target():
    source = (ROOT / "frontend/assistant.js").read_text()
    assert "const languageActions = new Set(['tr','en','de','fr','es','it','pt','ru','ar','zh','ja','ko','hi']" in source
    assert "appendPreferenceConfirmation(node,action)" in source
    assert "apply.addEventListener('click'" in source
    assert "cancel.addEventListener('click'" in source
    assert "localStorage.setItem('lecturesift-ui',code)" in source
    assert "if(!/^[a-zA-Z0-9_-]{1,64}$/.test(jobId)" in source
    assert "encodeURIComponent(lesson.jobId)" in source
    assert "appendAccountSummary(node,details.account_summary)" in source
    assert "appendCharge(replyNode,answer.charged_credits)" in source
    assert "detail.textContent=t('confirmsetting')" in source
    assert "title.textContent=item.title" in source
    assert "innerHTML=details" not in source


def test_credit_checkout_reuses_consent_and_provider_flow():
    source = (ROOT / "frontend/plans.js").read_text()
    assert "buy(pack.code,'one_time')" in source
    assert "button.disabled=!offers.available" in source
    assert "COUPON_PLANS.has(planCode)" in source
