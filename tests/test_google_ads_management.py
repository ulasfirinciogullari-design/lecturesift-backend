from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import threading

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import httpx
import pytest
from fastapi.testclient import TestClient

import lecturesift.google_ads_management as google_ads
from lecturesift import config
import lecturesift.rollout_routes as rollout_routes
from main import app


CUSTOMER_ID = "1234567890"
PROJECT_ID = "lecturesift-ads-readonly"
CLIENT_EMAIL = f"status-reader@{PROJECT_ID}.iam.gserviceaccount.com"
PRIVATE_KEY_ID = "a" * 40
TEST_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TEST_PRIVATE_KEY_PEM = TEST_PRIVATE_KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode("ascii")
ROOT = Path(__file__).resolve().parents[1]


def _service_account(*, encode: bool = True, **overrides) -> str:
    payload = {
        "type": "service_account",
        "project_id": PROJECT_ID,
        "private_key_id": PRIVATE_KEY_ID,
        "private_key": TEST_PRIVATE_KEY_PEM,
        "client_email": CLIENT_EMAIL,
        "client_id": "123456789012345678901",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": google_ads._TOKEN_URL,
    }
    payload.update(overrides)
    raw = json.dumps(payload, separators=(",", ":"))
    return base64.b64encode(raw.encode()).decode() if encode else raw


def _response(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(status, json=payload)


class FakeGoogleAdsClient:
    created: list["FakeGoogleAdsClient"] = []
    token_status = 200
    token_payload: dict = {
        "access_token": "short-lived-access-token",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": google_ads._OAUTH_SCOPE,
    }
    query_statuses: dict[str, int] = {}
    query_payloads: dict[str, dict] = {}

    def __init__(self, **kwargs):
        self.options = kwargs
        self.posts: list[dict] = []
        type(self).created.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def post(self, url, *, data=None, json=None, headers, timeout):
        self.posts.append(
            {"url": url, "data": data, "json": json, "headers": headers, "timeout": timeout}
        )
        if url == google_ads._TOKEN_URL:
            return _response(type(self).token_status, type(self).token_payload)
        query = (json or {}).get("query")
        status = type(self).query_statuses.get(query, 200)
        return _response(status, type(self).query_payloads.get(query, _payload_for(query)))


def _payload_for(query: str) -> dict:
    if query == google_ads._ACCOUNT_QUERY:
        return {
            "results": [
                {
                    "customer": {
                        "id": CUSTOMER_ID,
                        "resourceName": f"customers/{CUSTOMER_ID}",
                        "descriptiveName": "Private account name",
                        "status": "ENABLED",
                        "currencyCode": "TRY",
                        "timeZone": "Europe/Istanbul",
                    }
                }
            ]
        }
    if query == google_ads._CAMPAIGNS_QUERY:
        return {
            "results": [
                {"customer": {"id": CUSTOMER_ID}, "campaign": {"resourceName": "private/one", "status": "ENABLED"}},
                {"customer": {"id": CUSTOMER_ID}, "campaign": {"resourceName": "private/two", "status": "PAUSED"}},
                {"customer": {"id": CUSTOMER_ID}, "campaign": {"resourceName": "private/three", "status": "REMOVED"}},
                {"customer": {"id": CUSTOMER_ID}, "campaign": {"resourceName": "private/four", "status": "UNSPECIFIED"}},
            ]
        }
    if query == google_ads._INCENTIVES_QUERY:
        return {
            "results": [
                {
                    "customer": {"id": CUSTOMER_ID},
                    "appliedIncentive": {
                        "resourceName": f"customers/{CUSTOMER_ID}/appliedIncentives/private",
                        "couponCode": "MUST-NOT-LEAK",
                        "incentiveState": "REDEEMED",
                        "currencyCode": "TRY",
                        "rewardAmountMicros": "8000000000",
                        "requiredMinSpendMicros": "16000000000",
                        "currentSpendTowardsFulfillmentMicros": "1000000000",
                    }
                },
                {
                    "customer": {"id": CUSTOMER_ID},
                    "appliedIncentive": {
                        "couponCode": "ALSO-PRIVATE",
                        "incentiveState": "REWARD_GRANTED",
                        "currencyCode": "TRY",
                        "rewardAmountMicros": "2000000000",
                        "grantedAmountMicros": "2000000000",
                        "rewardBalanceRemainingMicros": "1500000000",
                    }
                },
            ]
        }
    for index, period in enumerate(google_ads._PERIODS.values(), 1):
        if query == google_ads._METRICS_QUERY.format(period=period):
            return {
                "results": [
                    {
                        "customer": {"id": CUSTOMER_ID},
                        "metrics": {
                            "costMicros": str(index * 1_000_000),
                            "impressions": str(index * 100),
                            "clicks": str(index * 10),
                            "conversions": index / 2,
                        },
                    }
                ]
            }
    return {"error": {"message": "provider secret"}}


@pytest.fixture(autouse=True)
def isolated_google_ads(monkeypatch):
    google_ads._reset_cache_for_tests()
    FakeGoogleAdsClient.created = []
    FakeGoogleAdsClient.token_status = 200
    FakeGoogleAdsClient.token_payload = {
        "access_token": "short-lived-access-token",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": google_ads._OAUTH_SCOPE,
    }
    FakeGoogleAdsClient.query_statuses = {}
    FakeGoogleAdsClient.query_payloads = {}
    values = {
        "GOOGLE_ADS_API_ENABLED": True,
        "GOOGLE_ADS_API_SERVICE_ACCOUNT_JSON": _service_account(),
        "GOOGLE_ADS_API_CUSTOMER_ID": CUSTOMER_ID,
        "GOOGLE_ADS_API_CACHE_SECONDS": 300,
        "GOOGLE_ADS_API_TIMEOUT_SECONDS": 4.0,
    }
    for name, value in values.items():
        monkeypatch.setattr(config, name, value, raising=False)
    monkeypatch.setattr(google_ads, "_HTTP_CLIENT_FACTORY", FakeGoogleAdsClient)
    yield
    google_ads._reset_cache_for_tests()


def _decode_part(value: str) -> dict:
    value += "=" * (-len(value) % 4)
    return json.loads(base64.urlsafe_b64decode(value).decode())


def test_status_uses_signed_readonly_jwt_fixed_queries_and_safe_summary():
    first = google_ads.google_ads_management_readiness()
    second = google_ads.google_ads_management_readiness()

    assert first == {
        "enabled": True,
        "configured": True,
        "connected": True,
        "status": "connected",
        "checked_at": first["checked_at"],
        "cached": False,
        "account": {
            "status": "ENABLED",
            "currency_code": "TRY",
            "time_zone": "Europe/Istanbul",
        },
        "periods": {
            "today": {
                "cost_micros": 1_000_000,
                "impressions": 100,
                "clicks": 10,
                "conversions": 0.5,
            },
            "last_7_days": {
                "cost_micros": 2_000_000,
                "impressions": 200,
                "clicks": 20,
                "conversions": 1.0,
            },
            "this_month": {
                "cost_micros": 3_000_000,
                "impressions": 300,
                "clicks": 30,
                "conversions": 1.5,
            },
        },
        "campaigns": {"total": 4, "enabled": 1, "paused": 1, "removed": 1, "other": 1},
        "incentive": {
            "status": "available",
            "count": 2,
            "states": {
                "redeemed": 1,
                "fulfilled": 0,
                "reward_granted": 1,
                "reward_exhausted": 0,
                "reward_expired": 0,
                "expired": 0,
                "invalidated": 0,
                "other": 0,
            },
            "currency_totals": [
                {
                    "currency_code": "TRY",
                    "reward_amount_micros": 10_000_000_000,
                    "granted_amount_micros": 2_000_000_000,
                    "required_min_spend_micros": 16_000_000_000,
                    "current_spend_towards_fulfillment_micros": 1_000_000_000,
                    "reward_balance_remaining_micros": 1_500_000_000,
                }
            ],
            "error_code": None,
        },
        "error_code": None,
    }
    assert second == {**first, "cached": True}
    assert len(FakeGoogleAdsClient.created) == 1

    fake = FakeGoogleAdsClient.created[0]
    assert fake.options["follow_redirects"] is False
    token_call = fake.posts[0]
    assert token_call["url"] == "https://oauth2.googleapis.com/token"
    assert token_call["data"]["grant_type"] == google_ads._JWT_GRANT_TYPE
    assertion = token_call["data"]["assertion"]
    header, claims, signature = assertion.split(".")
    assert _decode_part(header) == {"alg": "RS256", "kid": PRIVATE_KEY_ID, "typ": "JWT"}
    decoded_claims = _decode_part(claims)
    assert decoded_claims["iss"] == CLIENT_EMAIL
    assert decoded_claims["aud"] == google_ads._TOKEN_URL
    assert decoded_claims["scope"] == google_ads._OAUTH_SCOPE
    assert decoded_claims["exp"] - decoded_claims["iat"] == 3600
    signature += "=" * (-len(signature) % 4)
    TEST_PRIVATE_KEY.public_key().verify(
        base64.urlsafe_b64decode(signature),
        f"{header}.{claims}".encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )

    api_calls = fake.posts[1:]
    assert len(api_calls) == 6
    assert {call["url"] for call in api_calls} == {
        f"https://googleads.googleapis.com/v25/customers/{CUSTOMER_ID}/googleAds:search"
    }
    assert {call["json"]["query"] for call in api_calls} == google_ads._ALLOWED_QUERIES
    assert all("developer-token" not in call["headers"] for call in api_calls)
    assert all("login-customer-id" not in call["headers"] for call in api_calls)
    assert all(call["timeout"] <= google_ads._MAX_REQUEST_TIMEOUT_SECONDS for call in fake.posts)

    serialized = json.dumps(first)
    for private_value in (
        CUSTOMER_ID,
        CLIENT_EMAIL,
        PRIVATE_KEY_ID,
        "short-lived-access-token",
        "Private account name",
        "MUST-NOT-LEAK",
        "ALSO-PRIVATE",
        "private/one",
        "appliedIncentives/private",
    ):
        assert private_value not in serialized


def test_only_readonly_search_queries_are_source_controlled():
    assert google_ads._OAUTH_SCOPE == "https://www.googleapis.com/auth/adwords"
    assert all(query.startswith("SELECT ") for query in google_ads._ALLOWED_QUERIES)
    joined = " ".join(google_ads._ALLOWED_QUERIES).casefold()
    assert "coupon_code" not in joined
    assert "resource_name" not in joined
    assert "fetchincentive" not in joined
    assert "applyincentive" not in joined
    assert "mutate" not in joined


@pytest.mark.parametrize("encode", [False, True])
def test_minified_or_base64_service_account_json_is_accepted(monkeypatch, encode):
    monkeypatch.setattr(
        config, "GOOGLE_ADS_API_SERVICE_ACCOUNT_JSON", _service_account(encode=encode)
    )

    assert google_ads.google_ads_management_readiness()["connected"] is True


@pytest.mark.parametrize(
    "secret",
    [
        "not-json-or-base64",
        json.dumps({"type": "service_account"}),
        _service_account(token_uri="https://evil.example/token"),
        _service_account(type="authorized_user"),
    ],
)
def test_invalid_key_material_fails_before_network(monkeypatch, secret):
    monkeypatch.setattr(config, "GOOGLE_ADS_API_SERVICE_ACCOUNT_JSON", secret)

    result = google_ads.google_ads_management_readiness()

    assert result["configured"] is False
    assert result["connected"] is False
    assert result["error_code"] == "configuration_invalid"
    assert FakeGoogleAdsClient.created == []
    assert secret not in json.dumps(result)


def test_settings_representation_never_contains_service_account_secret():
    settings = google_ads._settings()

    assert settings.service_account_json
    assert settings.service_account_json not in repr(settings)


def test_duplicate_service_account_fields_fail_before_network(monkeypatch):
    raw = _service_account(encode=False)
    duplicate = raw[:-1] + ',"token_uri":"https://evil.example/token"}'
    monkeypatch.setattr(config, "GOOGLE_ADS_API_SERVICE_ACCOUNT_JSON", duplicate)

    result = google_ads.google_ads_management_readiness()

    assert result["configured"] is False
    assert result["error_code"] == "configuration_invalid"
    assert FakeGoogleAdsClient.created == []


def test_returned_additional_oauth_scope_fails_before_ads_queries():
    FakeGoogleAdsClient.token_payload["scope"] = (
        f"{google_ads._OAUTH_SCOPE} https://www.googleapis.com/auth/cloud-platform"
    )

    result = google_ads.google_ads_management_readiness()

    assert result["connected"] is False
    assert result["error_code"] == "scope_mismatch"
    assert len(FakeGoogleAdsClient.created) == 1
    assert len(FakeGoogleAdsClient.created[0].posts) == 1


def test_disabled_or_invalid_customer_configuration_never_contacts_google(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_ADS_API_ENABLED", False)
    disabled = google_ads.google_ads_management_readiness()
    assert disabled["status"] == "not_configured"
    assert disabled["error_code"] is None
    assert FakeGoogleAdsClient.created == []

    monkeypatch.setattr(config, "GOOGLE_ADS_API_ENABLED", True)
    monkeypatch.setattr(config, "GOOGLE_ADS_API_CUSTOMER_ID", "123-456-7890")
    invalid = google_ads.google_ads_management_readiness()
    assert invalid["configured"] is False
    assert invalid["error_code"] == "configuration_invalid"
    assert FakeGoogleAdsClient.created == []


def test_account_identity_mismatch_fails_closed_without_leaking_ids():
    FakeGoogleAdsClient.query_payloads[google_ads._ACCOUNT_QUERY] = {
        "results": [
            {
                "customer": {
                    "id": "9999999999",
                    "status": "ENABLED",
                    "currencyCode": "TRY",
                    "timeZone": "Europe/Istanbul",
                }
            }
        ]
    }

    result = google_ads.google_ads_management_readiness()

    assert result["connected"] is False
    assert result["error_code"] == "account_not_found"
    assert CUSTOMER_ID not in json.dumps(result)
    assert "9999999999" not in json.dumps(result)


@pytest.mark.parametrize(
    ("token_status", "query_status", "expected"),
    [
        (400, None, "authentication_failed"),
        (200, 403, "permission_denied"),
        (200, 404, "account_not_found"),
        (200, 429, "rate_limited"),
        (200, 503, "provider_unavailable"),
    ],
)
def test_general_provider_failures_are_opaque(token_status, query_status, expected):
    FakeGoogleAdsClient.token_status = token_status
    FakeGoogleAdsClient.token_payload = {"error": "private-token-error"}
    if query_status is not None:
        FakeGoogleAdsClient.token_payload = {
            "access_token": "short-lived-access-token",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        FakeGoogleAdsClient.query_statuses[google_ads._ACCOUNT_QUERY] = query_status

    result = google_ads.google_ads_management_readiness()

    assert result["connected"] is False
    assert result["error_code"] == expected
    assert result["account"] is result["periods"] is result["campaigns"] is None
    assert "private-token-error" not in json.dumps(result)


@pytest.mark.parametrize("status", [400, 403])
def test_typed_incentive_allowlist_failure_does_not_hide_general_summary(status):
    FakeGoogleAdsClient.query_statuses[google_ads._INCENTIVES_QUERY] = status
    FakeGoogleAdsClient.query_payloads[google_ads._INCENTIVES_QUERY] = {
        "error": {
            "code": status,
            "message": "private provider message",
            "details": [
                {
                    "@type": "type.googleapis.com/google.ads.googleads.v25.errors.GoogleAdsFailure",
                    "errors": [
                        {
                            "errorCode": {
                                "notAllowlistedError": (
                                    "CUSTOMER_NOT_ALLOWLISTED_FOR_THIS_FEATURE"
                                )
                            },
                            "message": "private detail",
                        }
                    ],
                }
            ],
        }
    }

    result = google_ads.google_ads_management_readiness()

    assert result["connected"] is True
    assert result["status"] == "connected"
    assert result["account"]["currency_code"] == "TRY"
    assert result["incentive"]["status"] == "unsupported"
    assert result["incentive"]["count"] is None
    assert result["incentive"]["error_code"] == "not_allowlisted_or_unsupported"
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [(400, "invalid_response"), (403, "permission_denied"), (503, "provider_unavailable")],
)
def test_other_incentive_failures_stay_separate_without_claiming_allowlist_status(
    status, expected_code
):
    FakeGoogleAdsClient.query_statuses[google_ads._INCENTIVES_QUERY] = status
    FakeGoogleAdsClient.query_payloads[google_ads._INCENTIVES_QUERY] = {
        "error": {
            "code": status,
            "message": "CUSTOMER_NOT_ALLOWLISTED_FOR_THIS_FEATURE",
            "details": [
                {
                    "errors": [
                        {"errorCode": {"queryError": "PROHIBITED_RESOURCE_TYPE_IN_SELECT_CLAUSE"}}
                    ]
                }
            ],
        }
    }

    result = google_ads.google_ads_management_readiness()

    assert result["connected"] is True
    assert result["incentive"]["status"] == "unavailable"
    assert result["incentive"]["error_code"] == expected_code
    assert "CUSTOMER_NOT_ALLOWLISTED" not in json.dumps(result)


def test_partial_paginated_reports_are_never_presented_as_complete():
    FakeGoogleAdsClient.query_payloads[google_ads._CAMPAIGNS_QUERY] = {
        "results": [],
        "nextPageToken": "private-page-token",
    }

    result = google_ads.google_ads_management_readiness()

    assert result["connected"] is False
    assert result["error_code"] == "invalid_response"
    assert "private-page-token" not in json.dumps(result)


def test_concurrent_cold_checks_share_one_provider_call(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []
    live = {
        "enabled": True,
        "configured": True,
        "connected": True,
        "status": "connected",
        "checked_at": "2026-09-12T20:00:00Z",
        "cached": False,
        "account": {"status": "ENABLED", "currency_code": "TRY", "time_zone": "Europe/Istanbul"},
        "periods": {},
        "campaigns": {"total": 0, "enabled": 0, "paused": 0, "removed": 0, "other": 0},
        "incentive": google_ads._incentive_empty("unsupported", "not_allowlisted_or_unsupported"),
        "error_code": None,
    }

    def slow(settings):
        calls.append(settings.fingerprint)
        started.set()
        assert release.wait(timeout=2)
        return deepcopy(live)

    monkeypatch.setattr(google_ads, "_live_summary", slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(google_ads.google_ads_management_readiness)
        assert started.wait(timeout=2)
        assert google_ads._CACHE_CONDITION.acquire(timeout=0.5)
        google_ads._CACHE_CONDITION.release()
        second = pool.submit(google_ads.google_ads_management_readiness)
        release.set()
        results = [first.result(timeout=2), second.result(timeout=2)]

    assert len(calls) == 1
    assert sorted(result["cached"] for result in results) == [False, True]


def test_admin_endpoint_includes_summary_and_auth_runs_before_google(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_ADMIN", "admin-secret")
    calls: list[str] = []

    def fake_summary():
        calls.append("called")
        return google_ads.google_ads_management_readiness()

    monkeypatch.setattr(rollout_routes, "google_ads_management_readiness", fake_summary)
    unauthorized = TestClient(app).get("/billing/admin/advertising-readiness")
    assert unauthorized.status_code == 401
    assert calls == []
    assert FakeGoogleAdsClient.created == []

    authorized = TestClient(app).get(
        "/billing/admin/advertising-readiness",
        headers={"Authorization": "Bearer admin-secret"},
    )
    assert authorized.status_code == 200
    management = authorized.json()["google_ads"]["management_api"]
    assert management["connected"] is True
    assert management["account"]["currency_code"] == "TRY"
    assert calls == ["called"]


def test_deployment_contract_is_opt_in_and_keeps_key_in_api_role_only():
    example = (ROOT / "deploy/env.example").read_text(encoding="utf-8")
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    preflight = (ROOT / "deploy/preflight.sh").read_text(encoding="utf-8")
    roles = (ROOT / "deploy/generate_role_envs.py").read_text(encoding="utf-8")
    probe = (ROOT / "deploy/long_media_probe.py").read_text(encoding="utf-8")
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    documentation = (ROOT / "docs/GOOGLE_ADS_READONLY_API.md").read_text(encoding="utf-8")

    assert "LECTURESIFT_GOOGLE_ADS_API_ENABLED=false" in example
    assert "LECTURESIFT_GOOGLE_ADS_API_SERVICE_ACCOUNT_JSON=\n" in example
    assert render.count("- key: LECTURESIFT_GOOGLE_ADS_API_SERVICE_ACCOUNT_JSON") == 1
    assert (
        "- key: LECTURESIFT_GOOGLE_ADS_API_ENABLED\n        sync: false" in render
    )
    assert (
        "- key: LECTURESIFT_GOOGLE_ADS_API_SERVICE_ACCOUNT_JSON\n        sync: false"
        in render
    )
    assert "Google Ads service-account JSON is invalid." in preflight
    assert '"LECTURESIFT_GOOGLE_ADS_API_SERVICE_ACCOUNT_JSON"' in roles
    assert '"LECTURESIFT_GOOGLE_ADS_API_SERVICE_ACCOUNT_JSON"' in probe
    assert "cryptography==50.0.1" in requirements
    assert "CUSTOMER_NOT_ALLOWLISTED_FOR_THIS_FEATURE" in documentation
    assert "developer-token" in documentation
    assert "FetchIncentive" in documentation and "ApplyIncentive" in documentation
