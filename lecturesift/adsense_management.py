"""Fail-closed, read-only AdSense Management API status checks.

Only Google's fixed OAuth and AdSense API origins are contacted. Provider
responses are reduced to a small operational summary so OAuth credentials,
resource identifiers, alert HTML and policy URLs never enter our API output.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
import threading
import time
from typing import Any

import httpx

from . import config


_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API_ROOT = "https://adsense.googleapis.com/v2"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_REQUEST_TIMEOUT_SECONDS = 5.0
_READONLY_SCOPE = "https://www.googleapis.com/auth/adsense.readonly"
_ACCOUNT_NAME = re.compile(r"^accounts/pub-[0-9]+$")
_PUBLISHER_ID = re.compile(r"^ca-pub-[0-9]+$")
_CLIENT_ID = re.compile(r"^[A-Za-z0-9._-]+\.apps\.googleusercontent\.com$")
_OAUTH_SECRET = re.compile(r"^[\x21-\x7e]{1,8192}$")
_ACCESS_TOKEN = re.compile(r"^[\x21-\x7e]{1,4096}$")
_SITE_DOMAIN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
_ALERT_TYPE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_ACCOUNT_STATES = frozenset({"STATE_UNSPECIFIED", "READY", "NEEDS_ATTENTION", "CLOSED"})
_SITE_STATES = frozenset(
    {"STATE_UNSPECIFIED", "REQUIRES_REVIEW", "GETTING_READY", "READY", "NEEDS_ATTENTION"}
)
_ALERT_SEVERITIES = frozenset({"SEVERITY_UNSPECIFIED", "INFO", "WARNING", "SEVERE"})
_POLICY_ACTIONS = frozenset(
    {
        "ENFORCEMENT_ACTION_UNSPECIFIED",
        "WARNED",
        "AD_SERVING_RESTRICTED",
        "AD_SERVING_DISABLED",
        "AD_SERVED_WITH_CLICK_CONFIRMATION",
        "AD_PERSONALIZATION_RESTRICTED",
    }
)
_HTTP_CLIENT_FACTORY = httpx.Client


class _AdSenseError(RuntimeError):
    """Internal provider failure carrying only a safe, fixed error code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _Settings:
    enabled: bool
    client_id: str
    client_secret: str
    refresh_token: str
    account_name: str
    publisher_id: str
    site_domain: str
    cache_seconds: int
    timeout_seconds: float

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and len(self.client_id) <= 512
            and _CLIENT_ID.fullmatch(self.client_id)
            and _OAUTH_SECRET.fullmatch(self.client_secret)
            and _OAUTH_SECRET.fullmatch(self.refresh_token)
            and _ACCOUNT_NAME.fullmatch(self.account_name)
            and _PUBLISHER_ID.fullmatch(self.publisher_id)
            and self.account_name == f"accounts/{self.publisher_id[3:]}"
            and _SITE_DOMAIN.fullmatch(self.site_domain)
        )

    @property
    def fingerprint(self) -> str:
        # The digest invalidates an in-process cache after credential rotation
        # without retaining the raw secrets in the cache key.
        material = "\0".join(
            (
                str(self.enabled),
                self.client_id,
                self.client_secret,
                self.refresh_token,
                self.account_name,
                self.publisher_id,
                self.site_domain,
                str(self.cache_seconds),
                str(self.timeout_seconds),
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


_CACHE_CONDITION = threading.Condition()
_CACHE: tuple[str, float, dict[str, Any]] | None = None
_CACHE_IN_FLIGHT: str | None = None


def _settings() -> _Settings:
    account_name = str(getattr(config, "ADSENSE_API_ACCOUNT_NAME", "") or "").strip()
    site_domain = str(getattr(config, "ADSENSE_API_SITE_DOMAIN", "") or "").strip().lower()
    site_domain = site_domain.rstrip(".")
    return _Settings(
        enabled=bool(getattr(config, "ADSENSE_API_ENABLED", False)),
        client_id=str(getattr(config, "ADSENSE_API_CLIENT_ID", "") or "").strip(),
        client_secret=str(getattr(config, "ADSENSE_API_CLIENT_SECRET", "") or ""),
        refresh_token=str(getattr(config, "ADSENSE_API_REFRESH_TOKEN", "") or ""),
        account_name=account_name,
        publisher_id=str(getattr(config, "ADSENSE_PUBLISHER_ID", "") or "").strip(),
        site_domain=site_domain,
        cache_seconds=max(60, min(3600, int(getattr(config, "ADSENSE_API_CACHE_SECONDS", 300)))),
        timeout_seconds=max(
            3.0, min(30.0, float(getattr(config, "ADSENSE_API_TIMEOUT_SECONDS", 10.0)))
        ),
    )


def _empty_summary(settings: _Settings, *, error_code: str | None = None) -> dict[str, Any]:
    enabled = settings.enabled
    configured = settings.configured
    return {
        "enabled": enabled,
        "configured": configured,
        "connected": False,
        "status": "unavailable" if error_code else "not_configured",
        "checked_at": None,
        "cached": False,
        "account": None,
        "site": None,
        "alerts": None,
        "policy_issues": None,
        "error_code": error_code,
    }


def _checked_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _provider_error(response: httpx.Response, *, token_request: bool = False) -> None:
    if 200 <= response.status_code < 300:
        return
    if response.status_code == 429:
        raise _AdSenseError("rate_limited")
    if response.status_code == 403:
        raise _AdSenseError("permission_denied")
    if response.status_code == 401 or (token_request and response.status_code == 400):
        raise _AdSenseError("authentication_failed")
    if response.status_code >= 500:
        raise _AdSenseError("provider_unavailable")
    raise _AdSenseError("invalid_response")


def _json_object(response: httpx.Response, *, token_request: bool = False) -> dict[str, Any]:
    _provider_error(response, token_request=token_request)
    content_type = response.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type != "application/json" and not content_type.endswith("+json"):
        raise _AdSenseError("invalid_response")
    declared_length = response.headers.get("content-length", "")
    if declared_length.isdigit() and int(declared_length) > _MAX_RESPONSE_BYTES:
        raise _AdSenseError("invalid_response")
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise _AdSenseError("invalid_response")
    try:
        value = response.json()
    except (TypeError, ValueError) as exc:
        raise _AdSenseError("invalid_response") from exc
    if not isinstance(value, dict):
        raise _AdSenseError("invalid_response")
    return value


def _request_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _AdSenseError("provider_unavailable")
    return min(_MAX_REQUEST_TIMEOUT_SECONDS, remaining)


def _refresh_access_token(client: httpx.Client, settings: _Settings, deadline: float) -> str:
    try:
        response = client.post(
            _TOKEN_URL,
            data={
                "client_id": settings.client_id,
                "client_secret": settings.client_secret,
                "refresh_token": settings.refresh_token,
                "grant_type": "refresh_token",
            },
            headers={"Accept": "application/json"},
            timeout=_request_timeout(deadline),
        )
    except (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError) as exc:
        raise _AdSenseError("provider_unavailable") from exc
    payload = _json_object(response, token_request=True)
    token = payload.get("access_token")
    token_type = payload.get("token_type")
    scope = payload.get("scope")
    if not isinstance(scope, str) or scope.split() != [_READONLY_SCOPE]:
        raise _AdSenseError("scope_mismatch")
    if (
        not isinstance(token, str)
        or not token
        or _ACCESS_TOKEN.fullmatch(token) is None
        or not isinstance(token_type, str)
        or token_type.casefold() != "bearer"
    ):
        raise _AdSenseError("invalid_response")
    return token


def _get(
    client: httpx.Client,
    path: str,
    token: str,
    params: dict[str, Any] | None = None,
    *,
    deadline: float,
    not_found_code: str | None = None,
) -> dict[str, Any]:
    # Every caller supplies a source-controlled relative path; redirects stay
    # disabled so credentials cannot follow a provider response to another host.
    if not path.startswith("/") or "//" in path or "?" in path or "#" in path:
        raise _AdSenseError("configuration_invalid")
    try:
        response = client.get(
            f"{_API_ROOT}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=_request_timeout(deadline),
        )
    except (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError) as exc:
        raise _AdSenseError("provider_unavailable") from exc
    if response.status_code == 404 and not_found_code:
        raise _AdSenseError(not_found_code)
    return _json_object(response)


def _objects(payload: dict[str, Any], field: str) -> list[dict[str, Any]]:
    # The requested page size is Google's documented maximum. If a future
    # account exceeds it, report the check as unavailable instead of presenting
    # a partial count as complete.
    if payload.get("nextPageToken"):
        raise _AdSenseError("invalid_response")
    value = payload.get(field, [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise _AdSenseError("invalid_response")
    return value


def _enum(value: Any, allowed: frozenset[str]) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in allowed else next(item for item in allowed if "UNSPECIFIED" in item)


def _account_summary(account: dict[str, Any], settings: _Settings) -> dict[str, Any]:
    if account.get("name") != settings.account_name:
        raise _AdSenseError("account_not_found")
    pending_tasks = account.get("pendingTasks", [])
    if not isinstance(pending_tasks, list):
        raise _AdSenseError("invalid_response")
    return {
        "state": _enum(account.get("state"), _ACCOUNT_STATES),
        "pending_task_count": len(pending_tasks),
    }


def _site_summary(sites: list[dict[str, Any]], settings: _Settings) -> dict[str, Any]:
    selected = next(
        (
            site
            for site in sites
            if str(site.get("domain") or "").strip().lower().rstrip(".") == settings.site_domain
        ),
        None,
    )
    if selected is None:
        raise _AdSenseError("site_not_found")
    return {
        "domain": settings.site_domain,
        "state": _enum(selected.get("state"), _SITE_STATES),
        "auto_ads_enabled": (
            selected.get("autoAdsEnabled")
            if isinstance(selected.get("autoAdsEnabled"), bool)
            else None
        ),
    }


def _alert_summary(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"info": 0, "warning": 0, "severe": 0}
    alert_types: set[str] = set()
    for alert in alerts:
        severity = _enum(alert.get("severity"), _ALERT_SEVERITIES).lower()
        if severity in counts:
            counts[severity] += 1
        alert_type = str(alert.get("type") or "")
        if _ALERT_TYPE.fullmatch(alert_type):
            alert_types.add(alert_type)
    return {
        "total": len(alerts),
        **counts,
        "types": sorted(alert_types)[:25],
    }


def _policy_summary(issues: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {
        "warned": 0,
        "ad_serving_restricted": 0,
        "ad_serving_disabled": 0,
        "ad_personalization_restricted": 0,
    }
    for issue in issues:
        action = _enum(issue.get("action"), _POLICY_ACTIONS).lower()
        if action in counts:
            counts[action] += 1
    return {"total": len(issues), **counts}


def _live_summary(settings: _Settings) -> dict[str, Any]:
    deadline = time.monotonic() + settings.timeout_seconds
    timeout = httpx.Timeout(min(_MAX_REQUEST_TIMEOUT_SECONDS, settings.timeout_seconds))
    with _HTTP_CLIENT_FACTORY(
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": "LectureSift-AdSense-ReadOnly/1"},
    ) as client:
        token = _refresh_access_token(client, settings, deadline)
        resource_path = f"/{settings.account_name}"
        account_payload = _get(
            client,
            resource_path,
            token,
            deadline=deadline,
            not_found_code="account_not_found",
        )
        account = _account_summary(account_payload, settings)
        # These read-only collections are independent. Fetching them together
        # bounds a cold admin check to one downstream timeout window instead of
        # three consecutive windows.
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="adsense-readonly") as pool:
            sites_future = pool.submit(
                _get,
                client,
                f"{resource_path}/sites",
                token,
                {"pageSize": 10000},
                deadline=deadline,
            )
            alerts_future = pool.submit(
                _get,
                client,
                f"{resource_path}/alerts",
                token,
                {"languageCode": "tr"},
                deadline=deadline,
            )
            policy_future = pool.submit(
                _get,
                client,
                f"{resource_path}/policyIssues",
                token,
                {"pageSize": 10000},
                deadline=deadline,
            )
            sites = _objects(sites_future.result(), "sites")
            alerts = _objects(alerts_future.result(), "alerts")
            policy_issues = _objects(policy_future.result(), "policyIssues")

    return {
        "enabled": True,
        "configured": True,
        "connected": True,
        "status": "connected",
        "checked_at": _checked_at(),
        "cached": False,
        "account": account,
        "site": _site_summary(sites, settings),
        "alerts": _alert_summary(alerts),
        "policy_issues": _policy_summary(policy_issues),
        "error_code": None,
    }


def adsense_management_readiness() -> dict[str, Any]:
    """Return a cached, secret-free AdSense status summary for admins."""

    settings = _settings()
    if not settings.enabled:
        return _empty_summary(settings)
    if not settings.configured:
        return _empty_summary(settings, error_code="configuration_invalid")

    fingerprint = settings.fingerprint
    global _CACHE, _CACHE_IN_FLIGHT
    wait_deadline = time.monotonic() + settings.timeout_seconds
    with _CACHE_CONDITION:
        while True:
            now = time.monotonic()
            if _CACHE and _CACHE[0] == fingerprint and now < _CACHE[1]:
                cached = deepcopy(_CACHE[2])
                cached["cached"] = True
                return cached
            if _CACHE_IN_FLIGHT is None:
                _CACHE_IN_FLIGHT = fingerprint
                break
            remaining = wait_deadline - now
            if remaining <= 0:
                result = _empty_summary(settings, error_code="provider_unavailable")
                result["checked_at"] = _checked_at()
                return result
            _CACHE_CONDITION.wait(timeout=remaining)

    # Provider I/O happens outside the condition lock. Other requests for this
    # process wait for the same bounded check and then reuse its cached result.
    try:
        try:
            result = _live_summary(settings)
        except _AdSenseError as exc:
            result = _empty_summary(settings, error_code=exc.code)
            result["checked_at"] = _checked_at()
        except Exception:
            # Unknown library/provider failures are intentionally opaque. Raw
            # exceptions can contain request details or OAuth response bodies.
            result = _empty_summary(settings, error_code="provider_unavailable")
            result["checked_at"] = _checked_at()
    except BaseException:
        with _CACHE_CONDITION:
            if _CACHE_IN_FLIGHT == fingerprint:
                _CACHE_IN_FLIGHT = None
                _CACHE_CONDITION.notify_all()
        raise

    with _CACHE_CONDITION:
        try:
            cache_seconds = settings.cache_seconds if result["connected"] else min(
                60, settings.cache_seconds
            )
            _CACHE = (fingerprint, time.monotonic() + cache_seconds, deepcopy(result))
        finally:
            _CACHE_IN_FLIGHT = None
            _CACHE_CONDITION.notify_all()
    return result


def _reset_cache_for_tests() -> None:
    global _CACHE, _CACHE_IN_FLIGHT
    with _CACHE_CONDITION:
        _CACHE = None
        _CACHE_IN_FLIGHT = None
        _CACHE_CONDITION.notify_all()
