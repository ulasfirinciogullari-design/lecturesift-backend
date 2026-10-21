"""Localized plan display names for backend emails, synced from the site copy.

The site shows plan names through the ``plan.<code>`` rows of
``frontend/i18n.js``. The API image ships without the frontend, so the same
rows are kept next to this module in ``plan_copy.json`` and read at send
time; no email template names a plan itself. ``python -m lecturesift.plan_copy
--check`` fails when the snapshot and the site copy differ, and ``--sync``
rewrites the snapshot from the site copy.
"""

from __future__ import annotations

import argparse
from functools import lru_cache
import json
from pathlib import Path
import re
import sys


LANGUAGES = ("tr", "en", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko", "hi")
SNAPSHOT_PATH = Path(__file__).with_name("plan_copy.json")
FRONTEND_I18N_PATH = Path(__file__).resolve().parents[1] / "frontend" / "i18n.js"
_ROW = re.compile(r'^\s*"plan\.([a-z][a-z0-9_]{0,31})":(\[.*?\]),?$', re.MULTILINE)


def frontend_plan_names(path: Path = FRONTEND_I18N_PATH) -> dict[str, list[str]]:
    """Read the ``plan.<code>`` name rows of the site's runtime copy."""
    names: dict[str, list[str]] = {}
    for code, payload in _ROW.findall(path.read_text(encoding="utf-8")):
        values = json.loads(payload)
        if len(values) != len(LANGUAGES) or not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError(f"plan.{code} must have one non-empty name per language")
        names[code] = values
    if not names:
        raise ValueError("no plan name rows found")
    return dict(sorted(names.items()))


def snapshot_payload(names: dict[str, list[str]]) -> str:
    return json.dumps({"languages": list(LANGUAGES), "plans": names}, ensure_ascii=False, indent=2) + "\n"


@lru_cache(maxsize=1)
def _snapshot() -> dict[str, list[str]]:
    try:
        payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        plans = payload["plans"] if payload.get("languages") == list(LANGUAGES) else {}
    except (OSError, ValueError, TypeError, AttributeError):
        plans = {}
    return {
        str(code): [str(value) for value in values]
        for code, values in (plans.items() if isinstance(plans, dict) else ())
        if isinstance(values, list) and len(values) == len(LANGUAGES)
    }


def plan_name(plan_code: str, language: str) -> str:
    """Return the plan's display name in ``language``, Turkish when unknown.

    A plan without a row keeps the catalog code, title-cased, as the order
    confirmation email does, so a renamed or new plan never blocks an email.
    """
    code = str(plan_code or "").strip()
    values = _snapshot().get(code)
    if not values:
        return code.replace("_", " ").title()
    language_code = str(language or "").strip().lower().replace("_", "-").split("-", 1)[0]
    index = LANGUAGES.index(language_code) if language_code in LANGUAGES else 0
    return values[index] or values[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Keep plan_copy.json equal to the plan names in frontend/i18n.js.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Fail when the snapshot differs from frontend/i18n.js.")
    group.add_argument("--sync", action="store_true", help="Rewrite the snapshot from frontend/i18n.js.")
    args = parser.parse_args(argv)
    expected = snapshot_payload(frontend_plan_names())
    current = SNAPSHOT_PATH.read_text(encoding="utf-8") if SNAPSHOT_PATH.exists() else ""
    if args.sync:
        if current != expected:
            SNAPSHOT_PATH.write_text(expected, encoding="utf-8")
        print(f"plan_copy.json holds {len(frontend_plan_names())} plan names")
        return 0
    if current != expected:
        print("lecturesift/plan_copy.json differs from the plan names in frontend/i18n.js; "
              "run: python -m lecturesift.plan_copy --sync", file=sys.stderr)
        return 1
    print("plan_copy.json matches frontend/i18n.js")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
