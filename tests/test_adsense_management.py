from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

import lecturesift.adsense_management as adsense_management
from lecturesift import config
from main import app


ACCOUNT = "accounts/pub-7608481350058806"
REPORT_URL = f"https://adsense.googleapis.com/v2/{ACCOUNT}/reports:generate"


def _report_payload(values: list[str | None] | None = None) -> dict:
    payload = {
        "headers": [
            {"name": "ESTIMATED_EARNINGS", "type": "METRIC_CURRENCY", "currencyCode": "TRY"},
            *({"name": name, "type": "METRIC_TALLY"} for name in ("PAGE_VIEWS", "IMPRESSIONS", "CLICKS")),
        ],
        "startDate": {"year": 2026, "month": 10, "day": 1},
        "endDate": {"year": 2026, "month": 10, "day": 9},
    }
    if values is not None:
        payload.update(totalMatchedRows="1", rows=[{"cells": [{"value": value} for value in values]}])
    return payload


def _expected_empty_report() -> dict:
    return {
        "status": "empty", "currency_code": "TRY",
        "start_date": "2026-10-01", "end_date": "2026-10-09",
        "estimated_earnings_micros": None, "page_views": None,
        "impressions": None, "clicks": None, "error_code": None,
    }


def _response(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(status, json=payload)


class FakeGoogleClient:
    created: list["FakeGoogleClient"] = []
    token_status = 200
    token_payload: dict = {
        "access_token": "short-lived-access",
        "token_type": "Bearer",
        "scope": adsense_management._READONLY_SCOPE,
    }
    api_statuses: dict[str, int] = {}
    payload_overrides: dict[str, dict] = {}

    def __init__(self, **kwargs):
        self.options = kwargs
        self.posts: list[tuple[str, dict, dict, float]] = []
        self.gets: list[tuple[str, dict | None, dict, float]] = []
        type(self).created.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def post(self, url, *, data, headers, timeout):
        self.posts.append((url, data, headers, timeout))
        return _response(type(self).token_status, type(self).token_payload)

    def get(self, url, *, params, headers, timeout):
        self.gets.append((url, params, headers, timeout))
        status = type(self).api_statuses.get(url, 200)
        payloads = {
            REPORT_URL: _report_payload(),
            f"https://adsense.googleapis.com/v2/{ACCOUNT}": {
                "name": ACCOUNT,
                "displayName": "Must not leave the backend",
                "state": "READY",
                "pendingTasks": [],
            },
            f"https://adsense.googleapis.com/v2/{ACCOUNT}/sites": {
                "sites": [
                    {
                        "name": f"{ACCOUNT}/sites/private-resource-id",
                        "domain": "lecturesift.com",
                        "state": "GETTING_READY",
                        "autoAdsEnabled": True,
                    }
                ]
            },
            f"https://adsense.googleapis.com/v2/{ACCOUNT}/alerts": {
                "alerts": [
                    {
                        "name": f"{ACCOUNT}/alerts/private-id",
                        "severity": "WARNING",
                        "message": "<a href='https://private.example'>provider detail</a>",
                        "type": "payment-hold",
                    },
                    {"severity": "SEVERE", "message": "secret", "type": "policy-warning"},
                    {"severity": "INFO", "type": "<script>not-returned</script>"},
                ]
            },
            f"https://adsense.googleapis.com/v2/{ACCOUNT}/policyIssues": {
                "policyIssues": [
                    {
                        "name": f"{ACCOUNT}/policyIssues/private-id",
                        "site": "private.example",
                        "uri": "private.example/a-page",
                        "action": "WARNED",
                    },
                    {"action": "AD_SERVING_DISABLED"},
                    {"action": "AD_PERSONALIZATION_RESTRICTED"},
                ]
            },
        }
        payloads.update(type(self).payload_overrides)
        return _response(status, payloads.get(url, {"error": {"message": "provider secret"}}))


@pytest.fixture(autouse=True)
def isolated_adsense_cache(monkeypatch):
    adsense_management._reset_cache_for_tests()
    FakeGoogleClient.created = []
    FakeGoogleClient.token_status = 200
    FakeGoogleClient.token_payload = {
        "access_token": "short-lived-access",
        "token_type": "Bearer",
        "scope": adsense_management._READONLY_SCOPE,
    }
    FakeGoogleClient.api_statuses = {}
    FakeGoogleClient.payload_overrides = {}
    values = {
        "ADSENSE_API_ENABLED": True,
        "ADSENSE_API_CLIENT_ID": "client.apps.googleusercontent.com",
        "ADSENSE_API_CLIENT_SECRET": "client-secret-must-not-leak",
        "ADSENSE_API_REFRESH_TOKEN": "refresh-token-must-not-leak",
        "ADSENSE_API_ACCOUNT_NAME": ACCOUNT,
        "ADSENSE_PUBLISHER_ID": "ca-pub-7608481350058806",
        "ADSENSE_API_SITE_DOMAIN": "lecturesift.com",
        "ADSENSE_API_CACHE_SECONDS": 300,
        "ADSENSE_API_TIMEOUT_SECONDS": 4.0,
    }
    for name, value in values.items():
        monkeypatch.setattr(config, name, value, raising=False)
    monkeypatch.setattr(adsense_management, "_HTTP_CLIENT_FACTORY", FakeGoogleClient)
    yield
    adsense_management._reset_cache_for_tests()


def test_readonly_status_is_reduced_to_safe_summary_and_cached():
    first = adsense_management.adsense_management_readiness()
    second = adsense_management.adsense_management_readiness()

    assert first == {
        "enabled": True,
        "configured": True,
        "connected": True,
        "status": "connected",
        "checked_at": first["checked_at"],
        "cached": False,
        "account": {"state": "READY", "pending_task_count": 0},
        "site": {
            "domain": "lecturesift.com",
            "state": "GETTING_READY",
            "auto_ads_enabled": True,
        },
        "alerts": {
            "total": 3,
            "info": 1,
            "warning": 1,
            "severe": 1,
            "types": ["payment-hold", "policy-warning"],
        },
        "policy_issues": {
            "total": 3,
            "warned": 1,
            "ad_serving_restricted": 0,
            "ad_serving_disabled": 1,
            "ad_personalization_restricted": 1,
        },
        "reports": {period: _expected_empty_report() for period in ("today", "last_7_days", "this_month")},
        "error_code": None,
    }
    assert second == {**first, "cached": True}
    assert len(FakeGoogleClient.created) == 1

    fake = FakeGoogleClient.created[0]
    assert fake.options["follow_redirects"] is False
    assert fake.posts[0][0] == "https://oauth2.googleapis.com/token"
    assert fake.posts[0][1] == {
        "client_id": "client.apps.googleusercontent.com",
        "client_secret": "client-secret-must-not-leak",
        "refresh_token": "refresh-token-must-not-leak",
        "grant_type": "refresh_token",
    }
    assert fake.posts[0][3] <= adsense_management._MAX_REQUEST_TIMEOUT_SECONDS
    assert fake.gets[0][0] == f"https://adsense.googleapis.com/v2/{ACCOUNT}"
    assert all(
        call[3] <= adsense_management._MAX_REQUEST_TIMEOUT_SECONDS
        for call in fake.gets if not call[0].endswith("/policyIssues")
    )
    policy_call = next(call for call in fake.gets if call[0].endswith("/policyIssues"))
    assert policy_call[3] <= adsense_management._MAX_POLICY_TIMEOUT_SECONDS
    assert policy_call[1] == {"pageSize": 10000, "fields": "policyIssues(action),nextPageToken"}
    assert policy_call[2]["x-goog-request-params"] == "parent=accounts%2Fpub-7608481350058806"
    assert {call[0] for call in fake.gets[1:]} == {
        f"https://adsense.googleapis.com/v2/{ACCOUNT}/sites",
        f"https://adsense.googleapis.com/v2/{ACCOUNT}/alerts",
        f"https://adsense.googleapis.com/v2/{ACCOUNT}/policyIssues",
        REPORT_URL,
    }
    report_calls = [call for call in fake.gets if call[0] == REPORT_URL]
    assert len(report_calls) == 3
    assert {call[1]["dateRange"] for call in report_calls} == {"TODAY", "LAST_7_DAYS", "MONTH_TO_DATE"}
    for call in report_calls:
        assert call[1] == {
            "metrics": ["ESTIMATED_EARNINGS", "PAGE_VIEWS", "IMPRESSIONS", "CLICKS"],
            "dateRange": call[1]["dateRange"], "filters": "DOMAIN_NAME==lecturesift.com",
            "reportingTimeZone": "ACCOUNT_TIME_ZONE", "languageCode": "en", "limit": 1,
        }
    serialized = json.dumps(first)
    for private_value in (
        "client-secret-must-not-leak",
        "refresh-token-must-not-leak",
        "short-lived-access",
        "Must not leave the backend",
        "private-resource-id",
        "private.example",
        "provider detail",
    ):
        assert private_value not in serialized


def test_disabled_or_invalid_configuration_never_contacts_google(monkeypatch):
    monkeypatch.setattr(config, "ADSENSE_API_ENABLED", False, raising=False)
    disabled = adsense_management.adsense_management_readiness()
    assert disabled["status"] == "not_configured"
    assert disabled["error_code"] is None
    assert FakeGoogleClient.created == []

    monkeypatch.setattr(config, "ADSENSE_API_ENABLED", True, raising=False)
    monkeypatch.setattr(
        config, "ADSENSE_API_ACCOUNT_NAME", "accounts/pub-1/../../reports", raising=False
    )
    invalid = adsense_management.adsense_management_readiness()
    assert invalid["configured"] is False
    assert invalid["status"] == "unavailable"
    assert invalid["error_code"] == "configuration_invalid"
    assert FakeGoogleClient.created == []


def test_missing_oauth_secret_has_only_fixed_internal_diagnostics(monkeypatch):
    monkeypatch.setattr(config, "ADSENSE_API_CLIENT_SECRET", "")
    result = adsense_management.adsense_management_readiness()
    assert result["error_code"] == "configuration_invalid"
    assert result["diagnostics"] == {"stage": "internal", "failure_type": "unknown", "elapsed_ms": 0}
    assert FakeGoogleClient.created == []
    assert "refresh-token-must-not-leak" not in json.dumps(result)


@pytest.mark.parametrize("stage", ["oauth", "account", "sites", "alerts", "policy"])
@pytest.mark.parametrize("failure_type", ["timeout", "network", "http", "response"])
def test_failure_diagnostics_identify_the_operation_without_provider_details(monkeypatch, stage, failure_type):
    original_post = FakeGoogleClient.post
    original_get = FakeGoogleClient.get
    private_detail = "client-secret-must-not-leak refresh-token-must-not-leak short-lived-access private-provider-detail"
    target = {
        "oauth": "https://oauth2.googleapis.com/token",
        "account": f"https://adsense.googleapis.com/v2/{ACCOUNT}",
        "sites": f"https://adsense.googleapis.com/v2/{ACCOUNT}/sites",
        "alerts": f"https://adsense.googleapis.com/v2/{ACCOUNT}/alerts",
        "policy": f"https://adsense.googleapis.com/v2/{ACCOUNT}/policyIssues",
    }[stage]

    def fail():
        if failure_type == "timeout":
            raise httpx.ReadTimeout(private_detail)
        if failure_type == "network":
            raise httpx.ConnectError(private_detail)
        if failure_type == "http":
            return _response(503, {"error": {"message": private_detail}})
        return httpx.Response(200, text=private_detail, headers={"Content-Type": "text/html"})

    def post(self, url, **kwargs):
        return fail() if url == target else original_post(self, url, **kwargs)

    def get(self, url, **kwargs):
        return fail() if url == target else original_get(self, url, **kwargs)

    monkeypatch.setattr(FakeGoogleClient, "post", post)
    monkeypatch.setattr(FakeGoogleClient, "get", get)
    result = adsense_management.adsense_management_readiness()
    key = f"{stage}_diagnostics" if stage in {"policy", "alerts"} else "diagnostics"
    diagnostic = result[key]
    assert set(diagnostic) == {"stage", "failure_type", "elapsed_ms"}
    assert diagnostic["stage"] == stage
    assert diagnostic["failure_type"] == failure_type
    assert type(diagnostic["elapsed_ms"]) is int and diagnostic["elapsed_ms"] >= 0
    expected_error = "invalid_response" if failure_type == "response" else "provider_unavailable"
    if stage in {"policy", "alerts"}:
        assert result["connected"] is True
        assert result["site"]["state"] == "GETTING_READY"
        assert result[f"{stage}_error_code"] == expected_error
        assert result["error_code"] is None
    else:
        assert result["connected"] is False
        assert result["error_code"] == expected_error
    serialized = json.dumps(result)
    for secret in private_detail.split():
        assert secret not in serialized
    assert target not in serialized and ACCOUNT not in serialized


def test_stage_elapsed_time_and_cached_diagnostics_are_not_recomputed_or_shared(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(adsense_management.time, "monotonic", lambda: clock["now"])

    def timed_post(self, url, **kwargs):
        clock["now"] += 1.25
        raise httpx.ReadTimeout("private-request-details")

    monkeypatch.setattr(FakeGoogleClient, "post", timed_post)
    first = adsense_management.adsense_management_readiness()
    expected = {"stage": "oauth", "failure_type": "timeout", "elapsed_ms": 1250}
    assert first["diagnostics"] == expected
    first["diagnostics"]["stage"] = "caller-mutation"
    clock["now"] = 120.0
    cached = adsense_management.adsense_management_readiness()
    assert cached["cached"] is True and cached["diagnostics"] == expected
    assert len(FakeGoogleClient.created) == 1


def test_cache_wait_timeout_does_not_replace_the_check_already_in_flight(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(adsense_management.time, "monotonic", lambda: clock["now"])
    fingerprint = adsense_management._settings().fingerprint
    monkeypatch.setattr(adsense_management, "_CACHE_IN_FLIGHT", fingerprint)

    def finish_wait(*, timeout):
        clock["now"] += timeout

    monkeypatch.setattr(adsense_management._CACHE_CONDITION, "wait", finish_wait)
    result = adsense_management.adsense_management_readiness()
    assert result["error_code"] == "provider_unavailable"
    assert result["diagnostics"] == {
        "stage": "cache_wait", "failure_type": "timeout",
        "elapsed_ms": round(adsense_management._CHECK_TIMEOUT_SECONDS * 1000),
    }
    assert adsense_management._CACHE_IN_FLIGHT == fingerprint
    assert adsense_management._CACHE is None
    assert FakeGoogleClient.created == []


def test_unexpected_internal_failure_remains_opaque(monkeypatch):
    def broken_factory(**kwargs):
        raise RuntimeError("private-internal-secret")

    monkeypatch.setattr(adsense_management, "_HTTP_CLIENT_FACTORY", broken_factory)
    result = adsense_management.adsense_management_readiness()
    assert result["error_code"] == "provider_unavailable"
    assert result["diagnostics"]["stage"] == "internal"
    assert result["diagnostics"]["failure_type"] == "unknown"
    assert "private-internal-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    ("token_status", "api_status", "expected"),
    [
        (400, None, "authentication_failed"),
        (200, 403, "permission_denied"),
        (200, 429, "rate_limited"),
        (200, 503, "provider_unavailable"),
    ],
)
def test_provider_failures_are_opaque_and_fail_closed(token_status, api_status, expected):
    FakeGoogleClient.token_status = token_status
    FakeGoogleClient.token_payload = {"error": "invalid_grant", "secret": "provider-detail"}
    if api_status is not None:
        FakeGoogleClient.token_payload = {
            "access_token": "short-lived-access",
            "token_type": "Bearer",
            "scope": adsense_management._READONLY_SCOPE,
        }
        FakeGoogleClient.api_statuses[
            f"https://adsense.googleapis.com/v2/{ACCOUNT}"
        ] = api_status

    result = adsense_management.adsense_management_readiness()

    assert result["connected"] is False
    assert result["status"] == "unavailable"
    assert result["checked_at"].endswith("Z")
    assert result["account"] is result["site"] is None
    assert result["alerts"] is result["policy_issues"] is None
    assert result["error_code"] == expected
    assert "provider-detail" not in json.dumps(result)


@pytest.mark.parametrize(
    ("setting", "value", "expected"),
    [
        ("ADSENSE_API_ACCOUNT_NAME", "accounts/pub-999999", "account_not_found"),
        ("ADSENSE_API_SITE_DOMAIN", "missing.example", "site_not_found"),
    ],
)
def test_configured_account_and_site_must_match_provider_resources(
    monkeypatch, setting, value, expected
):
    monkeypatch.setattr(config, setting, value, raising=False)
    if expected == "account_not_found":
        monkeypatch.setattr(config, "ADSENSE_PUBLISHER_ID", "ca-pub-999999", raising=False)
        FakeGoogleClient.api_statuses[f"https://adsense.googleapis.com/v2/{value}"] = 404

    result = adsense_management.adsense_management_readiness()

    assert result["connected"] is False
    assert result["error_code"] == expected
    assert result["account"] is result["site"] is None


def test_management_account_must_match_ad_serving_publisher(monkeypatch):
    monkeypatch.setattr(config, "ADSENSE_PUBLISHER_ID", "ca-pub-999999", raising=False)

    result = adsense_management.adsense_management_readiness()

    assert result["configured"] is False
    assert result["connected"] is False
    assert result["error_code"] == "configuration_invalid"
    assert FakeGoogleClient.created == []


@pytest.mark.parametrize(
    "scope",
    [
        None,
        "https://www.googleapis.com/auth/adsense",
        (
            "https://www.googleapis.com/auth/adsense.readonly "
            "https://www.googleapis.com/auth/userinfo.email"
        ),
    ],
)
def test_refresh_token_scope_must_be_exactly_readonly(scope):
    FakeGoogleClient.token_payload = {
        "access_token": "short-lived-access",
        "token_type": "Bearer",
    }
    if scope is not None:
        FakeGoogleClient.token_payload["scope"] = scope

    result = adsense_management.adsense_management_readiness()

    assert result["configured"] is True
    assert result["connected"] is False
    assert result["error_code"] == "scope_mismatch"
    assert len(FakeGoogleClient.created) == 1
    assert FakeGoogleClient.created[0].gets == []
    assert scope is None or scope not in json.dumps(result)


@pytest.mark.parametrize("token_type", [None, "MAC"])
def test_refresh_token_type_must_be_explicit_bearer(token_type):
    FakeGoogleClient.token_payload = {
        "access_token": "short-lived-access",
        "scope": adsense_management._READONLY_SCOPE,
    }
    if token_type is not None:
        FakeGoogleClient.token_payload["token_type"] = token_type

    result = adsense_management.adsense_management_readiness()

    assert result["connected"] is False
    assert result["error_code"] == "invalid_response"
    assert len(FakeGoogleClient.created) == 1
    assert FakeGoogleClient.created[0].gets == []


def test_explicit_bearer_token_type_is_case_insensitive():
    FakeGoogleClient.token_payload["token_type"] = "bearer"

    result = adsense_management.adsense_management_readiness()

    assert result["connected"] is True
    assert result["error_code"] is None


def test_absent_auto_ads_field_stays_unknown_instead_of_becoming_false():
    FakeGoogleClient.payload_overrides[
        f"https://adsense.googleapis.com/v2/{ACCOUNT}/sites"
    ] = {
        "sites": [
            {
                "domain": "lecturesift.com",
                "state": "READY",
            }
        ]
    }

    result = adsense_management.adsense_management_readiness()

    assert result["connected"] is True
    assert result["site"]["auto_ads_enabled"] is None


def test_paginated_provider_summary_is_not_misreported_as_complete():
    FakeGoogleClient.payload_overrides[
        f"https://adsense.googleapis.com/v2/{ACCOUNT}/policyIssues"
    ] = {"policyIssues": [], "nextPageToken": "private-next-page-token"}

    result = adsense_management.adsense_management_readiness()

    assert result["connected"] is True
    assert result["site"]["state"] == "GETTING_READY"
    assert result["policy_issues"] is None
    assert result["policy_error_code"] == "invalid_response"
    assert result["error_code"] is None
    assert "private-next-page-token" not in json.dumps(result)


@pytest.mark.parametrize("status", [403, 429, 503])
def test_policy_collection_failure_preserves_verified_site_status_and_retries_soon(monkeypatch, status):
    clock = {"now": 100.0}
    monkeypatch.setattr(adsense_management.time, "monotonic", lambda: clock["now"])
    FakeGoogleClient.api_statuses[f"https://adsense.googleapis.com/v2/{ACCOUNT}/policyIssues"] = status
    first = adsense_management.adsense_management_readiness()
    assert first["connected"] is True
    assert first["account"]["state"] == "READY"
    assert first["site"]["state"] == "GETTING_READY"
    assert first["alerts"]["total"] == 3
    assert first["policy_issues"] is None
    assert first["policy_error_code"] in {"permission_denied", "rate_limited", "provider_unavailable"}
    clock["now"] = 161.0
    FakeGoogleClient.api_statuses = {}
    recovered = adsense_management.adsense_management_readiness()
    assert recovered["cached"] is False
    assert recovered["policy_issues"]["total"] == 3
    assert "policy_error_code" not in recovered


def test_failed_checks_use_short_cache_and_deadline_is_enforced(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(adsense_management.time, "monotonic", lambda: clock["now"])
    FakeGoogleClient.token_status = 503

    first = adsense_management.adsense_management_readiness()
    clock["now"] = 159.0
    cached = adsense_management.adsense_management_readiness()
    clock["now"] = 161.0
    retried = adsense_management.adsense_management_readiness()

    assert first["error_code"] == cached["error_code"] == retried["error_code"]
    assert cached["cached"] is True
    assert retried["cached"] is False
    assert len(FakeGoogleClient.created) == 2
    with pytest.raises(adsense_management._AdSenseError, match="provider_unavailable"):
        adsense_management._request_timeout(clock["now"] - 1)


def test_policy_timeout_does_not_hide_account_and_site(monkeypatch):
    original = FakeGoogleClient.get

    def timed_get(self, url, **kwargs):
        if url.endswith("/policyIssues"):
            raise httpx.ReadTimeout("synthetic private provider details")
        return original(self, url, **kwargs)

    monkeypatch.setattr(FakeGoogleClient, "get", timed_get)
    result = adsense_management.adsense_management_readiness()
    assert result["connected"] is True
    assert result["site"]["state"] == "GETTING_READY"
    assert result["policy_issues"] is None
    assert result["policy_error_code"] == "provider_unavailable"
    assert "synthetic private" not in json.dumps(result)


def test_slow_policy_center_gets_its_own_budget_and_empty_list_is_not_site_approval(monkeypatch):
    original = FakeGoogleClient.get

    def slow_policy_get(self, url, **kwargs):
        if url.endswith("/policyIssues"):
            # Reproduce the provider's 17-second response without slowing CI.
            if kwargs["timeout"] < 17:
                raise httpx.ReadTimeout("policy service still processing")
            return _response(200, {})
        return original(self, url, **kwargs)

    monkeypatch.setattr(FakeGoogleClient, "get", slow_policy_get)
    FakeGoogleClient.payload_overrides[f"https://adsense.googleapis.com/v2/{ACCOUNT}/sites"] = {
        "sites": [{"domain": "lecturesift.com", "state": "NEEDS_ATTENTION"}]
    }
    result = adsense_management.adsense_management_readiness()

    assert result["connected"] is True
    assert result["site"]["state"] == "NEEDS_ATTENTION"
    assert result["policy_issues"]["total"] == 0
    assert "policy_error_code" not in result
    assert all(call[3] <= 5 for call in FakeGoogleClient.created[0].gets)
    assert adsense_management.adsense_management_readiness()["cached"] is True


def test_concurrent_cold_checks_share_one_provider_call(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []
    live_result = {
        "enabled": True,
        "configured": True,
        "connected": True,
        "status": "connected",
        "checked_at": "2026-09-12T18:25:00Z",
        "cached": False,
        "account": {"state": "READY", "pending_task_count": 0},
        "site": {
            "domain": "lecturesift.com",
            "state": "READY",
            "auto_ads_enabled": None,
        },
        "alerts": {"total": 0, "info": 0, "warning": 0, "severe": 0, "types": []},
        "policy_issues": {
            "total": 0,
            "warned": 0,
            "ad_serving_restricted": 0,
            "ad_serving_disabled": 0,
            "ad_personalization_restricted": 0,
        },
        "error_code": None,
    }

    def slow_live_summary(settings):
        calls.append(settings.fingerprint)
        started.set()
        assert release.wait(timeout=2)
        return deepcopy(live_result)

    monkeypatch.setattr(adsense_management, "_live_summary", slow_live_summary)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(adsense_management.adsense_management_readiness)
        assert started.wait(timeout=2)
        # The provider call must not retain the cache condition lock.
        assert adsense_management._CACHE_CONDITION.acquire(timeout=0.5)
        adsense_management._CACHE_CONDITION.release()
        second_future = pool.submit(adsense_management.adsense_management_readiness)
        release.set()
        results = [first_future.result(timeout=2), second_future.result(timeout=2)]

    assert len(calls) == 1
    assert sorted(result["cached"] for result in results) == [False, True]
    assert all(result["connected"] is True for result in results)


def test_admin_readiness_includes_live_management_summary(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_ADMIN", "admin-secret")
    response = TestClient(app).get(
        "/billing/admin/advertising-readiness",
        headers={"Authorization": "Bearer admin-secret"},
    )

    assert response.status_code == 200
    adsense = response.json()["adsense"]
    assert adsense["google_account_connected"] is True
    assert adsense["management_api"]["site"] == {
        "domain": "lecturesift.com",
        "state": "GETTING_READY",
        "auto_ads_enabled": True,
    }


def test_admin_authentication_happens_before_any_google_request(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_ADMIN", "admin-secret")

    response = TestClient(app).get("/billing/admin/advertising-readiness")

    assert response.status_code == 401
    assert FakeGoogleClient.created == []


def test_admin_reads_independent_providers_concurrently(monkeypatch):
    import lecturesift.rollout_routes as routes

    monkeypatch.setattr(config, "ADMIN_ADMIN", "admin-secret")
    entered = threading.Barrier(2)

    def reader():
        # A sequential route cannot cross this barrier. No timing assertion or
        # real provider call is needed to verify the two reads overlap.
        entered.wait(timeout=2)
        return {"connected": False, "status": "not_configured"}

    monkeypatch.setattr(routes, "adsense_management_readiness", reader)
    monkeypatch.setattr(routes, "google_ads_management_readiness", reader)
    response = TestClient(app).get(
        "/billing/admin/advertising-readiness",
        headers={"Authorization": "Bearer admin-secret"},
    )
    assert response.status_code == 200
    assert response.json()["adsense"]["google_account_connected"] is False
    assert response.json()["google_ads"]["google_account_connected"] is False


def test_reports_share_the_bounded_downstream_pool_and_existing_request_budget(monkeypatch):
    original = FakeGoogleClient.get
    entered = threading.Barrier(6)
    calls = []
    lock = threading.Lock()

    def get(self, url, **kwargs):
        if url != f"https://adsense.googleapis.com/v2/{ACCOUNT}":
            with lock:
                calls.append((url, kwargs["timeout"]))
            entered.wait(timeout=2)
        return original(self, url, **kwargs)

    monkeypatch.setattr(FakeGoogleClient, "get", get)
    result = adsense_management.adsense_management_readiness()
    assert result["connected"] is True
    assert len(calls) == 6
    assert all(report["status"] == "empty" for report in result["reports"].values())
    assert all(timeout <= config.ADSENSE_API_TIMEOUT_SECONDS for url, timeout in calls if not url.endswith("/policyIssues"))


def test_alert_failure_preserves_other_data_and_retries_after_short_cache(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(adsense_management.time, "monotonic", lambda: clock["now"])
    FakeGoogleClient.api_statuses[f"https://adsense.googleapis.com/v2/{ACCOUNT}/alerts"] = 503
    first = adsense_management.adsense_management_readiness()
    assert first["connected"] is True and first["error_code"] is None
    assert first["site"]["state"] == "GETTING_READY"
    assert first["alerts"] is None
    assert first["alerts_error_code"] == "provider_unavailable"
    assert first["alerts_diagnostics"]["stage"] == "alerts"
    assert first["policy_issues"]["total"] == 3
    assert first["reports"]["today"]["status"] == "empty"
    first["alerts_diagnostics"]["stage"] = "caller mutation"
    cached = adsense_management.adsense_management_readiness()
    assert cached["cached"] is True and cached["alerts_diagnostics"]["stage"] == "alerts"
    FakeGoogleClient.api_statuses = {}
    clock["now"] = 161.0
    recovered = adsense_management.adsense_management_readiness()
    assert recovered["cached"] is False and recovered["alerts"]["total"] == 3
    assert "alerts_error_code" not in recovered


@pytest.mark.parametrize("earnings, expected", [("0", 0), ("12.340005", 12340005), ("-0.25", -250000)])
def test_reports_preserve_explicit_zero_decimal_precision_and_source_currency(earnings, expected):
    payload = _report_payload([earnings, "150", "321", "0"])
    payload["headers"][0]["currencyCode"] = "EUR"
    payload["warnings"] = ["private warning https://private.example"]
    FakeGoogleClient.payload_overrides[REPORT_URL] = payload
    result = adsense_management.adsense_management_readiness()
    for report in result["reports"].values():
        assert report == {
            "status": "available", "currency_code": "EUR",
            "start_date": "2026-10-01", "end_date": "2026-10-09",
            "estimated_earnings_micros": expected,
            "page_views": 150, "impressions": 321, "clicks": 0,
            "error_code": None,
        }
    assert "private warning" not in json.dumps(result)
    assert "private.example" not in json.dumps(result)


def test_missing_report_metric_stays_unknown_while_other_metrics_remain_available():
    payload = _report_payload([None, "10", "0", ""])
    FakeGoogleClient.payload_overrides[REPORT_URL] = payload
    report = adsense_management.adsense_management_readiness()["reports"]["today"]
    assert report["status"] == "available"
    assert report["estimated_earnings_micros"] is None
    assert report["page_views"] == 10 and report["impressions"] == 0
    assert report["clicks"] is None


def test_report_headers_are_mapped_by_name_and_omitted_metric_is_not_zero():
    payload = _report_payload()
    payload["headers"] = [{"name": "CLICKS", "type": "METRIC_TALLY"}, payload["headers"][0]]
    payload["totals"] = {"cells": [{"value": "2"}, {"value": "1.25"}]}
    FakeGoogleClient.payload_overrides[REPORT_URL] = payload
    report = adsense_management.adsense_management_readiness()["reports"]["today"]
    assert report["status"] == "available" and report["currency_code"] == "TRY"
    assert report["estimated_earnings_micros"] == 1250000 and report["clicks"] == 2
    assert report["page_views"] is None and report["impressions"] is None


@pytest.mark.parametrize("kind", [
    "currency", "header_type", "duplicate_header", "invalid_date", "reverse_dates",
    "truncated", "row_width", "negative_count", "unsafe_integer", "nonfinite",
    "excess_decimal_precision", "unsafe_earnings", "boolean_value",
])
def test_invalid_report_data_is_optional_and_never_becomes_zero(kind):
    payload = _report_payload(["1.25", "20", "40", "1"])
    if kind == "currency":
        payload["headers"][0]["currencyCode"] = "<script>"
    elif kind == "header_type":
        payload["headers"][1]["type"] = "DIMENSION"
    elif kind == "duplicate_header":
        payload["headers"][2] = payload["headers"][1]
    elif kind == "invalid_date":
        payload["startDate"]["day"] = 32
    elif kind == "reverse_dates":
        payload["startDate"]["month"] = 11
    elif kind == "truncated":
        payload["totalMatchedRows"] = "2"
    elif kind == "row_width":
        payload["rows"][0]["cells"].pop()
    elif kind == "negative_count":
        payload["rows"][0]["cells"][1]["value"] = "-1"
    elif kind == "unsafe_integer":
        payload["rows"][0]["cells"][1]["value"] = "9007199254740992"
    elif kind == "nonfinite":
        payload["rows"][0]["cells"][0]["value"] = "NaN"
    elif kind == "excess_decimal_precision":
        payload["rows"][0]["cells"][0]["value"] = "0.0000001"
    elif kind == "unsafe_earnings":
        payload["rows"][0]["cells"][0]["value"] = "9007199254.740992"
    elif kind == "boolean_value":
        payload["rows"][0]["cells"][0]["value"] = True
    FakeGoogleClient.payload_overrides[REPORT_URL] = payload
    result = adsense_management.adsense_management_readiness()
    assert result["connected"] is True and result["site"]["state"] == "GETTING_READY"
    report = result["reports"]["today"]
    assert report["status"] == "unavailable" and report["error_code"] == "invalid_response"
    assert report["estimated_earnings_micros"] is None and report["page_views"] is None
    assert report["diagnostics"]["stage"] == "reports_today"
    assert report["diagnostics"]["failure_type"] == "response"


@pytest.mark.parametrize("failure", ["timeout", "http", "unknown"])
def test_each_report_failure_is_isolated_and_uses_existing_deadline(monkeypatch, failure):
    original = FakeGoogleClient.get
    clock = {"now": 100.0}
    monkeypatch.setattr(adsense_management.time, "monotonic", lambda: clock["now"])

    def get(self, url, **kwargs):
        if url == REPORT_URL and kwargs["params"]["dateRange"] == "TODAY":
            assert kwargs["timeout"] <= config.ADSENSE_API_TIMEOUT_SECONDS
            if failure == "timeout":
                raise httpx.ReadTimeout("private report details")
            if failure == "unknown":
                raise RuntimeError("private report details")
            return _response(503, {"error": {"message": "private report details"}})
        return original(self, url, **kwargs)

    monkeypatch.setattr(FakeGoogleClient, "get", get)
    first = adsense_management.adsense_management_readiness()
    assert first["connected"] is True and first["error_code"] is None
    assert first["alerts"]["total"] == 3 and first["site"]["state"] == "GETTING_READY"
    report = first["reports"]["today"]
    assert report["status"] == "unavailable" and report["error_code"] == "provider_unavailable"
    assert report["diagnostics"]["failure_type"] == failure
    assert first["reports"]["last_7_days"]["status"] == "empty"
    assert first["reports"]["this_month"]["status"] == "empty"
    assert "private report details" not in json.dumps(first)
    report["diagnostics"]["stage"] = "caller mutation"
    assert adsense_management.adsense_management_readiness()["reports"]["today"]["diagnostics"]["stage"] == "reports_today"
    clock["now"] = 161.0
    monkeypatch.setattr(FakeGoogleClient, "get", original)
    recovered = adsense_management.adsense_management_readiness()
    assert recovered["cached"] is False and recovered["reports"]["today"]["status"] == "empty"
