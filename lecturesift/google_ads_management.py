"""Fail-closed, read-only Google Ads API v25 account reporting.

The integration accepts one service-account key, exchanges a source-built JWT
for the fixed ``adwords`` OAuth scope, and submits only fixed GAQL ``search``
queries to Google's fixed v25 origin.  Provider responses are reduced to a
small operational summary; customer IDs, resource names, coupon codes, OAuth
tokens, private keys and raw provider errors never enter our API output.
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import threading
import time
from typing import Any

import httpx
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from . import config


_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API_ROOT = "https://googleads.googleapis.com/v25"
_OAUTH_SCOPE = "https://www.googleapis.com/auth/adwords"
_JWT_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_SERVICE_ACCOUNT_BYTES = 32 * 1024
_MAX_REQUEST_TIMEOUT_SECONDS = 5.0
_CUSTOMER_ID = re.compile(r"^[0-9]{10}$")
_PROJECT_ID = re.compile(r"^[a-z][a-z0-9.-]{4,61}[a-z0-9]$")
_PRIVATE_KEY_ID = re.compile(r"^[A-Fa-f0-9]{16,128}$")
_CLIENT_ID = re.compile(r"^[0-9]{10,40}$")
_ACCESS_TOKEN = re.compile(r"^[\x21-\x7e]{1,4096}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_TIME_ZONE = re.compile(r"^[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*$")
_ACCOUNT_STATUSES = frozenset(
    {"UNKNOWN", "UNSPECIFIED", "ENABLED", "CANCELED", "SUSPENDED", "CLOSED"}
)
_CAMPAIGN_STATUSES = frozenset({"UNKNOWN", "UNSPECIFIED", "ENABLED", "PAUSED", "REMOVED"})
_INCENTIVE_STATES = frozenset(
    {
        "UNKNOWN",
        "UNSPECIFIED",
        "REDEEMED",
        "FULFILLED",
        "REWARD_GRANTED",
        "REWARD_EXHAUSTED",
        "REWARD_EXPIRED",
        "EXPIRED",
        "INVALIDATED",
    }
)
_PERIODS = {
    "today": "TODAY",
    "last_7_days": "LAST_7_DAYS",
    "this_month": "THIS_MONTH",
}
_ACCOUNT_QUERY = (
    "SELECT customer.id, customer.status, customer.currency_code, customer.time_zone "
    "FROM customer LIMIT 1"
)
_METRICS_QUERY = (
    "SELECT customer.id, metrics.cost_micros, metrics.impressions, metrics.clicks, "
    "metrics.conversions FROM customer WHERE segments.date DURING {period} LIMIT 1"
)
_CAMPAIGNS_QUERY = "SELECT customer.id, campaign.status FROM campaign"
_INCENTIVES_QUERY = (
    "SELECT customer.id, applied_incentive.incentive_state, applied_incentive.currency_code, "
    "applied_incentive.reward_amount_micros, applied_incentive.granted_amount_micros, "
    "applied_incentive.required_min_spend_micros, "
    "applied_incentive.current_spend_towards_fulfillment_micros, "
    "applied_incentive.reward_balance_remaining_micros FROM applied_incentive"
)
_ALLOWED_QUERIES = frozenset(
    {
        _ACCOUNT_QUERY,
        _CAMPAIGNS_QUERY,
        _INCENTIVES_QUERY,
        *(_METRICS_QUERY.format(period=period) for period in _PERIODS.values()),
    }
)
_HTTP_CLIENT_FACTORY = httpx.Client


class _GoogleAdsError(RuntimeError):
    """Internal provider failure carrying only a safe, fixed error code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _Settings:
    enabled: bool
    service_account_json: str = field(repr=False)
    customer_id: str
    cache_seconds: int
    timeout_seconds: float

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and 1 <= len(self.service_account_json) <= _MAX_SERVICE_ACCOUNT_BYTES * 2
            and _CUSTOMER_ID.fullmatch(self.customer_id)
        )

    @property
    def fingerprint(self) -> str:
        # A digest invalidates the process cache after key rotation without
        # retaining the raw service-account JSON in the cache key.
        material = "\0".join(
            (
                str(self.enabled),
                self.service_account_json,
                self.customer_id,
                str(self.cache_seconds),
                str(self.timeout_seconds),
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _ServiceAccount:
    client_email: str
    private_key_id: str
    private_key: rsa.RSAPrivateKey


_CACHE_CONDITION = threading.Condition()
_CACHE: tuple[str, float, dict[str, Any]] | None = None
_CACHE_IN_FLIGHT: str | None = None


def _settings() -> _Settings:
    return _Settings(
        enabled=bool(getattr(config, "GOOGLE_ADS_API_ENABLED", False)),
        service_account_json=str(
            getattr(config, "GOOGLE_ADS_API_SERVICE_ACCOUNT_JSON", "") or ""
        ).strip(),
        customer_id=str(getattr(config, "GOOGLE_ADS_API_CUSTOMER_ID", "") or "").strip(),
        cache_seconds=max(
            60, min(3600, int(getattr(config, "GOOGLE_ADS_API_CACHE_SECONDS", 300)))
        ),
        timeout_seconds=max(
            3.0, min(30.0, float(getattr(config, "GOOGLE_ADS_API_TIMEOUT_SECONDS", 10.0)))
        ),
    )


def _incentive_empty(status: str, error_code: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "count": None,
        "states": None,
        "currency_totals": None,
        "error_code": error_code,
    }


def _empty_summary(
    settings: _Settings,
    *,
    error_code: str | None = None,
    configured: bool | None = None,
) -> dict[str, Any]:
    return {
        "enabled": settings.enabled,
        "configured": settings.configured if configured is None else configured,
        "connected": False,
        "status": "unavailable" if error_code else "not_configured",
        "checked_at": None,
        "cached": False,
        "account": None,
        "periods": None,
        "campaigns": None,
        "incentive": _incentive_empty(
            "unavailable" if error_code else "not_configured", error_code
        ),
        "error_code": error_code,
    }


def _checked_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _GoogleAdsError("configuration_invalid")
        result[key] = value
    return result


def _decode_service_account(raw: str) -> dict[str, Any]:
    if not raw or len(raw) > _MAX_SERVICE_ACCOUNT_BYTES * 2:
        raise _GoogleAdsError("configuration_invalid")
    if raw.startswith("{"):
        encoded = raw.encode("utf-8")
    else:
        try:
            encoded = base64.b64decode(raw, validate=True)
        except (ValueError, TypeError) as exc:
            raise _GoogleAdsError("configuration_invalid") from exc
    if not encoded or len(encoded) > _MAX_SERVICE_ACCOUNT_BYTES or b"\x00" in encoded:
        raise _GoogleAdsError("configuration_invalid")
    try:
        value = json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_object)
    except _GoogleAdsError:
        raise
    except (UnicodeError, TypeError, ValueError) as exc:
        raise _GoogleAdsError("configuration_invalid") from exc
    if not isinstance(value, dict):
        raise _GoogleAdsError("configuration_invalid")
    return value


def _service_account(raw: str) -> _ServiceAccount:
    payload = _decode_service_account(raw)
    project_id = payload.get("project_id")
    client_email = payload.get("client_email")
    private_key_id = payload.get("private_key_id")
    client_id = payload.get("client_id")
    private_key_text = payload.get("private_key")
    if (
        payload.get("type") != "service_account"
        or payload.get("token_uri") != _TOKEN_URL
        or not isinstance(project_id, str)
        or _PROJECT_ID.fullmatch(project_id) is None
        or not isinstance(client_email, str)
        or client_email != f"{client_email.partition('@')[0]}@{project_id}.iam.gserviceaccount.com"
        or len(client_email) > 254
        or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,62}", client_email.partition("@")[0])
        or not isinstance(private_key_id, str)
        or _PRIVATE_KEY_ID.fullmatch(private_key_id) is None
        or not isinstance(client_id, str)
        or _CLIENT_ID.fullmatch(client_id) is None
        or not isinstance(private_key_text, str)
        or not (256 <= len(private_key_text) <= 16384)
        or not private_key_text.startswith("-----BEGIN PRIVATE KEY-----\n")
        or not private_key_text.endswith("-----END PRIVATE KEY-----\n")
    ):
        raise _GoogleAdsError("configuration_invalid")
    try:
        private_key = serialization.load_pem_private_key(
            private_key_text.encode("ascii"), password=None
        )
    except (TypeError, ValueError, UnicodeError, UnsupportedAlgorithm) as exc:
        raise _GoogleAdsError("configuration_invalid") from exc
    if (
        not isinstance(private_key, rsa.RSAPrivateKey)
        or private_key.key_size < 2048
        or private_key.key_size > 4096
    ):
        raise _GoogleAdsError("configuration_invalid")
    return _ServiceAccount(
        client_email=client_email,
        private_key_id=private_key_id,
        private_key=private_key,
    )


def _base64url(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _jwt_assertion(account: _ServiceAccount) -> str:
    now = int(time.time())
    header = {"alg": "RS256", "kid": account.private_key_id, "typ": "JWT"}
    claims = {
        "aud": _TOKEN_URL,
        "exp": now + 3600,
        "iat": now,
        "iss": account.client_email,
        "scope": _OAUTH_SCOPE,
    }
    parts = [
        _base64url(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        for value in (header, claims)
    ]
    signing_input = ".".join(parts).encode("ascii")
    signature = account.private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{parts[0]}.{parts[1]}.{_base64url(signature)}"


def _request_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _GoogleAdsError("provider_unavailable")
    return min(_MAX_REQUEST_TIMEOUT_SECONDS, remaining)


def _provider_error(
    response: httpx.Response,
    *,
    token_request: bool = False,
    incentive_request: bool = False,
) -> None:
    if 200 <= response.status_code < 300:
        return
    if incentive_request and _incentive_not_allowlisted(response):
        # AppliedIncentive is officially allowlist-only. Classify only Google's
        # typed allowlist failure here; a generic 400 can instead mean that a
        # source-controlled GAQL field became invalid in a future API version.
        raise _GoogleAdsError("incentive_unsupported")
    if response.status_code == 429:
        raise _GoogleAdsError("rate_limited")
    if response.status_code == 403:
        raise _GoogleAdsError("permission_denied")
    if response.status_code == 404:
        raise _GoogleAdsError("account_not_found")
    if response.status_code == 401 or (token_request and response.status_code == 400):
        raise _GoogleAdsError("authentication_failed")
    if response.status_code >= 500:
        raise _GoogleAdsError("provider_unavailable")
    raise _GoogleAdsError("invalid_response")


def _incentive_not_allowlisted(response: httpx.Response) -> bool:
    """Recognize Google's typed allowlist error without retaining raw details."""

    if response.status_code not in {400, 403, 404}:
        return False
    content_type = response.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type != "application/json" and not content_type.endswith("+json"):
        return False
    declared_length = response.headers.get("content-length", "")
    if declared_length.isdigit() and int(declared_length) > _MAX_RESPONSE_BYTES:
        return False
    if len(response.content) > _MAX_RESPONSE_BYTES:
        return False
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
        return False
    details = payload["error"].get("details")
    if not isinstance(details, list):
        return False
    for detail in details:
        if not isinstance(detail, dict) or not isinstance(detail.get("errors"), list):
            continue
        for error in detail["errors"]:
            if not isinstance(error, dict) or not isinstance(error.get("errorCode"), dict):
                continue
            if (
                error["errorCode"].get("notAllowlistedError")
                == "CUSTOMER_NOT_ALLOWLISTED_FOR_THIS_FEATURE"
            ):
                return True
    return False


def _json_object(
    response: httpx.Response,
    *,
    token_request: bool = False,
    incentive_request: bool = False,
) -> dict[str, Any]:
    _provider_error(
        response, token_request=token_request, incentive_request=incentive_request
    )
    content_type = response.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type != "application/json" and not content_type.endswith("+json"):
        raise _GoogleAdsError("invalid_response")
    declared_length = response.headers.get("content-length", "")
    if declared_length.isdigit() and int(declared_length) > _MAX_RESPONSE_BYTES:
        raise _GoogleAdsError("invalid_response")
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise _GoogleAdsError("invalid_response")
    try:
        value = response.json()
    except (TypeError, ValueError) as exc:
        raise _GoogleAdsError("invalid_response") from exc
    if not isinstance(value, dict):
        raise _GoogleAdsError("invalid_response")
    return value


def _access_token(
    client: httpx.Client,
    account: _ServiceAccount,
    deadline: float,
) -> str:
    try:
        response = client.post(
            _TOKEN_URL,
            data={"grant_type": _JWT_GRANT_TYPE, "assertion": _jwt_assertion(account)},
            headers={"Accept": "application/json"},
            timeout=_request_timeout(deadline),
        )
    except (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError) as exc:
        raise _GoogleAdsError("provider_unavailable") from exc
    payload = _json_object(response, token_request=True)
    token = payload.get("access_token")
    token_type = payload.get("token_type")
    returned_scope = payload.get("scope")
    expires_in = payload.get("expires_in")
    if returned_scope is not None and (
        not isinstance(returned_scope, str) or returned_scope.split() != [_OAUTH_SCOPE]
    ):
        raise _GoogleAdsError("scope_mismatch")
    if (
        not isinstance(token, str)
        or _ACCESS_TOKEN.fullmatch(token) is None
        or not isinstance(token_type, str)
        or token_type.casefold() != "bearer"
        or isinstance(expires_in, bool)
        or not isinstance(expires_in, int)
        or not (60 <= expires_in <= 3600)
    ):
        raise _GoogleAdsError("invalid_response")
    return token


def _search(
    client: httpx.Client,
    settings: _Settings,
    token: str,
    query: str,
    *,
    deadline: float,
    incentive_request: bool = False,
) -> list[dict[str, Any]]:
    if query not in _ALLOWED_QUERIES:
        raise _GoogleAdsError("configuration_invalid")
    url = f"{_API_ROOT}/customers/{settings.customer_id}/googleAds:search"
    try:
        response = client.post(
            url,
            json={"query": query},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=_request_timeout(deadline),
        )
    except (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError) as exc:
        raise _GoogleAdsError("provider_unavailable") from exc
    payload = _json_object(response, incentive_request=incentive_request)
    if payload.get("nextPageToken"):
        # Never present a partial count as a complete report.
        raise _GoogleAdsError("invalid_response")
    results = payload.get("results", [])
    if not isinstance(results, list) or any(not isinstance(row, dict) for row in results):
        raise _GoogleAdsError("invalid_response")
    return results


def _customer_id(value: Any, expected: str) -> None:
    if str(value or "") != expected:
        raise _GoogleAdsError("account_not_found")


def _enum(value: Any, allowed: frozenset[str]) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in allowed else "UNKNOWN"


def _account_summary(rows: list[dict[str, Any]], settings: _Settings) -> dict[str, Any]:
    if len(rows) != 1 or not isinstance(rows[0].get("customer"), dict):
        raise _GoogleAdsError("account_not_found")
    customer = rows[0]["customer"]
    _customer_id(customer.get("id"), settings.customer_id)
    currency = str(customer.get("currencyCode") or "").strip().upper()
    time_zone = str(customer.get("timeZone") or "").strip()
    if (
        _CURRENCY.fullmatch(currency) is None
        or not (1 <= len(time_zone) <= 80)
        or _TIME_ZONE.fullmatch(time_zone) is None
    ):
        raise _GoogleAdsError("invalid_response")
    return {
        "status": _enum(customer.get("status"), _ACCOUNT_STATUSES),
        "currency_code": currency,
        "time_zone": time_zone,
    }


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        raise _GoogleAdsError("invalid_response")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value):
        result = int(value)
    else:
        raise _GoogleAdsError("invalid_response")
    if not (0 <= result <= 9_223_372_036_854_775_807):
        raise _GoogleAdsError("invalid_response")
    return result


def _nonnegative_float(value: Any) -> float:
    if isinstance(value, bool):
        raise _GoogleAdsError("invalid_response")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise _GoogleAdsError("invalid_response") from exc
    if not math.isfinite(result) or result < 0 or result > 1e15:
        raise _GoogleAdsError("invalid_response")
    return result


def _metric_summary(rows: list[dict[str, Any]], settings: _Settings) -> dict[str, Any]:
    if not rows:
        return {"cost_micros": 0, "impressions": 0, "clicks": 0, "conversions": 0.0}
    if len(rows) != 1:
        raise _GoogleAdsError("invalid_response")
    customer = rows[0].get("customer", {})
    metrics = rows[0].get("metrics", {})
    if not isinstance(customer, dict) or not isinstance(metrics, dict):
        raise _GoogleAdsError("invalid_response")
    _customer_id(customer.get("id"), settings.customer_id)
    return {
        "cost_micros": _nonnegative_int(metrics.get("costMicros", 0)),
        "impressions": _nonnegative_int(metrics.get("impressions", 0)),
        "clicks": _nonnegative_int(metrics.get("clicks", 0)),
        "conversions": _nonnegative_float(metrics.get("conversions", 0)),
    }


def _campaign_summary(
    rows: list[dict[str, Any]], settings: _Settings
) -> dict[str, int]:
    counts = {"total": len(rows), "enabled": 0, "paused": 0, "removed": 0, "other": 0}
    for row in rows:
        customer = row.get("customer")
        campaign = row.get("campaign")
        if not isinstance(customer, dict) or not isinstance(campaign, dict):
            raise _GoogleAdsError("invalid_response")
        _customer_id(customer.get("id"), settings.customer_id)
        status = _enum(campaign.get("status"), _CAMPAIGN_STATUSES).lower()
        counts[status if status in {"enabled", "paused", "removed"} else "other"] += 1
    return counts


_INCENTIVE_AMOUNT_FIELDS = {
    "rewardAmountMicros": "reward_amount_micros",
    "grantedAmountMicros": "granted_amount_micros",
    "requiredMinSpendMicros": "required_min_spend_micros",
    "currentSpendTowardsFulfillmentMicros": "current_spend_towards_fulfillment_micros",
    "rewardBalanceRemainingMicros": "reward_balance_remaining_micros",
}


def _incentive_summary(
    rows: list[dict[str, Any]], settings: _Settings
) -> dict[str, Any]:
    states = {
        "redeemed": 0,
        "fulfilled": 0,
        "reward_granted": 0,
        "reward_exhausted": 0,
        "reward_expired": 0,
        "expired": 0,
        "invalidated": 0,
        "other": 0,
    }
    totals: dict[str, dict[str, Any]] = {}
    seen_amount: dict[str, set[str]] = {}
    for row in rows:
        customer = row.get("customer")
        incentive = row.get("appliedIncentive")
        if not isinstance(customer, dict) or not isinstance(incentive, dict):
            raise _GoogleAdsError("invalid_response")
        _customer_id(customer.get("id"), settings.customer_id)
        state = _enum(incentive.get("incentiveState"), _INCENTIVE_STATES).lower()
        states[state if state in states else "other"] += 1
        currency = str(incentive.get("currencyCode") or "").strip().upper()
        if _CURRENCY.fullmatch(currency) is None:
            raise _GoogleAdsError("invalid_response")
        target = totals.setdefault(
            currency,
            {"currency_code": currency, **{name: 0 for name in _INCENTIVE_AMOUNT_FIELDS.values()}},
        )
        seen = seen_amount.setdefault(currency, set())
        for provider_name, output_name in _INCENTIVE_AMOUNT_FIELDS.items():
            if provider_name in incentive:
                target[output_name] += _nonnegative_int(incentive[provider_name])
                seen.add(output_name)
    for currency, target in totals.items():
        for output_name in _INCENTIVE_AMOUNT_FIELDS.values():
            if output_name not in seen_amount[currency]:
                target[output_name] = None
    return {
        "status": "available" if rows else "empty",
        "count": len(rows),
        "states": states,
        "currency_totals": [totals[currency] for currency in sorted(totals)],
        "error_code": None,
    }


def _incentive_failure(error: _GoogleAdsError) -> dict[str, Any]:
    if error.code == "incentive_unsupported":
        return _incentive_empty("unsupported", "not_allowlisted_or_unsupported")
    allowed = {
        "authentication_failed",
        "scope_mismatch",
        "permission_denied",
        "rate_limited",
        "provider_unavailable",
        "invalid_response",
    }
    code = error.code if error.code in allowed else "provider_unavailable"
    return _incentive_empty("unavailable", code)


def _live_summary(settings: _Settings) -> dict[str, Any]:
    service_account = _service_account(settings.service_account_json)
    deadline = time.monotonic() + settings.timeout_seconds
    timeout = httpx.Timeout(min(_MAX_REQUEST_TIMEOUT_SECONDS, settings.timeout_seconds))
    with _HTTP_CLIENT_FACTORY(
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": "LectureSift-GoogleAds-ReadOnly/1"},
    ) as client:
        token = _access_token(client, service_account, deadline)
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="google-ads-readonly") as pool:
            account_future = pool.submit(
                _search, client, settings, token, _ACCOUNT_QUERY, deadline=deadline
            )
            metric_futures = {
                name: pool.submit(
                    _search,
                    client,
                    settings,
                    token,
                    _METRICS_QUERY.format(period=period),
                    deadline=deadline,
                )
                for name, period in _PERIODS.items()
            }
            campaigns_future = pool.submit(
                _search, client, settings, token, _CAMPAIGNS_QUERY, deadline=deadline
            )
            incentive_future = pool.submit(
                _search,
                client,
                settings,
                token,
                _INCENTIVES_QUERY,
                deadline=deadline,
                incentive_request=True,
            )
            account = _account_summary(account_future.result(), settings)
            periods = {
                name: _metric_summary(future.result(), settings)
                for name, future in metric_futures.items()
            }
            campaigns = _campaign_summary(campaigns_future.result(), settings)
            try:
                incentive = _incentive_summary(incentive_future.result(), settings)
            except _GoogleAdsError as exc:
                incentive = _incentive_failure(exc)

    return {
        "enabled": True,
        "configured": True,
        "connected": True,
        "status": "connected",
        "checked_at": _checked_at(),
        "cached": False,
        "account": account,
        "periods": periods,
        "campaigns": campaigns,
        "incentive": incentive,
        "error_code": None,
    }


def google_ads_management_readiness() -> dict[str, Any]:
    """Return a cached, secret-free Google Ads account summary for admins."""

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

    try:
        try:
            result = _live_summary(settings)
        except _GoogleAdsError as exc:
            result = _empty_summary(
                settings,
                error_code=exc.code,
                configured=False if exc.code == "configuration_invalid" else None,
            )
            result["checked_at"] = _checked_at()
        except Exception:
            # Never return or log unknown exceptions; crypto and provider
            # failures can embed credential or request details.
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
