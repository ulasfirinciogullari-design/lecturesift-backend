"""Print the AdSense fields that the daily status email compares, as one JSON line.

The host's ``deploy/adsense_status_alert.py`` runs this inside the existing
api container::

    docker compose exec -T api python -m lecturesift.adsense_status_snapshot

The read-only AdSense OAuth credentials stay in the api role's environment;
the host never reads them. The output holds only enum states, alert types and
severities, the policy issue count and fixed error codes. It carries no
credential, account or client id, provider message or raw API payload.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .adsense_management import adsense_management_readiness


SCHEMA = "lecturesift-adsense-status-v1"


def _failure(part: str, code: Any) -> dict[str, Any]:
    # The host accepts only its own list of codes and turns anything else
    # into "unknown", so a provider-derived string never reaches an email.
    return {
        "schema": SCHEMA,
        "ok": False,
        "part": part,
        "error": code if isinstance(code, str) and code else "unknown",
    }


def status_snapshot(readiness: Any) -> dict[str, Any]:
    """Reduce the admin readiness summary to what the status email compares.

    A check that could not read every compared field is a failure, so a
    missing collection is never mistaken for a status change.
    """

    if not isinstance(readiness, dict):
        return _failure("account", "unknown")
    if readiness.get("status") == "not_configured":
        return _failure("account", "not_configured")
    if readiness.get("status") != "connected" or readiness.get("connected") is not True:
        return _failure("account", readiness.get("error_code"))
    account = readiness.get("account")
    site = readiness.get("site")
    if not isinstance(account, dict) or not isinstance(site, dict):
        return _failure("account", "invalid_response")
    alerts = readiness.get("alerts")
    if readiness.get("alerts_error_code") or not isinstance(alerts, dict):
        return _failure("alerts", readiness.get("alerts_error_code") or "invalid_response")
    policy = readiness.get("policy_issues")
    if readiness.get("policy_error_code") or not isinstance(policy, dict):
        return _failure("policy", readiness.get("policy_error_code") or "invalid_response")
    return {
        "schema": SCHEMA,
        "ok": True,
        "account_state": account.get("state"),
        "site_state": site.get("state"),
        "auto_ads": site.get("auto_ads_enabled"),
        "alerts": alerts.get("severities"),
        "policy_issues": policy.get("total"),
    }


def main() -> int:
    try:
        snapshot = status_snapshot(adsense_management_readiness())
    except Exception:  # noqa: BLE001 - the reader is already opaque; stay that way
        snapshot = _failure("account", "unknown")
    sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
