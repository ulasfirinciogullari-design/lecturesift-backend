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
    assert all(call[3] <= adsense_management._MAX_REQUEST_TIMEOUT_SECONDS for call in fake.gets)
    assert {call[0] for call in fake.gets[1:]} == {
        f"https://adsense.googleapis.com/v2/{ACCOUNT}/sites",
        f"https://adsense.googleapis.com/v2/{ACCOUNT}/alerts",
        f"https://adsense.googleapis.com/v2/{ACCOUNT}/policyIssues",
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

    assert result["connected"] is False
    assert result["policy_issues"] is None
    assert result["error_code"] == "invalid_response"
    assert "private-next-page-token" not in json.dumps(result)


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
