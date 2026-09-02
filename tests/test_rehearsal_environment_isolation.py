from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.parse import urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "generate_rehearsal_envs", ROOT / "deploy" / "generate_rehearsal_envs.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


RUN = "20260831112233"
REVISION = "a" * 40
DATABASE = f"lecturesift_rehearsal_{RUN}"


def _inputs(*, ai: bool = True):
    runtime = {
        "S3_ENDPOINT_URL": "https://production.eu.r2.cloudflarestorage.com",
        "S3_BUCKET": "lecturesift-production",
        "S3_ACCESS_KEY_ID": "prod-r2-access",
        "S3_SECRET_ACCESS_KEY": "prod-r2-secret",
        "LECTURESIFT_MAX_DOCUMENT_BYTES": "52428800",
        "ADMIN_ADMIN": "prod-admin-secret",
    }
    database = {
        "POSTGRES_DB": "lecturesift",
        "POSTGRES_USER": "lecturesift_owner",
        "POSTGRES_PASSWORD": "prod-owner-password",
        "LECTURESIFT_APP_DB_PASSWORD": "prod-api-password",
        "LECTURESIFT_WORKER_DB_PASSWORD": "prod-worker-password",
    }
    api = {
        "DATABASE_URL": "postgresql+psycopg://prod_api:prod-api-password@postgres:5432/lecturesift",
        "IYZICO_SECRET_KEY": "prod-iyzico-secret",
        "RESEND_API_KEY": "prod-resend-key",
    }
    worker = {
        "DATABASE_URL": "postgresql+psycopg://prod_worker:prod-worker-password@postgres:5432/lecturesift",
        "OPENAI_API_KEY": "prod-openai-key",
    }
    rehearsal = {
        "LECTURESIFT_REHEARSAL_S3_ENDPOINT_URL": (
            "https://rehearsal.eu.r2.cloudflarestorage.com"
        ),
        "LECTURESIFT_REHEARSAL_S3_REGION": "auto",
        "LECTURESIFT_REHEARSAL_S3_BUCKET": "lecturesift-rehearsal",
        "LECTURESIFT_REHEARSAL_S3_ACCESS_KEY_ID": "rehearsal-r2-access",
        "LECTURESIFT_REHEARSAL_S3_SECRET_ACCESS_KEY": "rehearsal-r2-secret",
    }
    if ai:
        rehearsal["LECTURESIFT_REHEARSAL_OPENAI_API_KEY"] = "rehearsal-openai-key"
    return runtime, database, api, worker, rehearsal


def _build(*, ai: bool = True, api_password: str = "isolated-api-password"):
    runtime, database, api, worker, rehearsal = _inputs(ai=ai)
    api_url = (
        f"postgresql+psycopg://lecturesift_rehearsal_api_{RUN}:"
        f"{api_password}@postgres:5432/{DATABASE}"
    )
    worker_url = (
        f"postgresql+psycopg://lecturesift_rehearsal_worker_{RUN}:"
        f"isolated-worker-password@postgres:5432/{DATABASE}"
    )
    return MODULE.build_environments(
        runtime,
        database,
        api,
        worker,
        rehearsal,
        api_database_url=api_url,
        worker_database_url=worker_url,
        revision=REVISION,
        run_id=RUN,
    )


def test_generated_roles_are_allowlisted_and_do_not_inherit_production_secrets():
    api, worker, proof = _build()

    assert urlsplit(api["DATABASE_URL"]).username == f"lecturesift_rehearsal_api_{RUN}"
    assert urlsplit(worker["DATABASE_URL"]).username == (
        f"lecturesift_rehearsal_worker_{RUN}"
    )
    assert api["S3_BUCKET"] == worker["S3_BUCKET"] == "lecturesift-rehearsal"
    assert api["LECTURESIFT_MAX_DOCUMENT_BYTES"] == "52428800"
    assert api["EMAIL_PROVIDER"] == "none"
    assert api["IYZICO_BANK_TRANSFER_ENABLED"] == "false"
    assert api["INSTAGRAM_DAILY_AUTOMATION_ENABLED"] == "false"
    assert "OPENAI_API_KEY" not in api
    assert worker["OPENAI_API_KEY"] == "rehearsal-openai-key"
    assert api["HTTPS_PROXY"] == "http://egress-proxy-api:3128"
    assert worker["HTTPS_PROXY"] == "http://egress-proxy-worker:3128"
    for forbidden in {
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "IYZICO_API_KEY",
        "IYZICO_SECRET_KEY",
        "PAYTR_MERCHANT_KEY",
        "PAYTR_MERCHANT_SALT",
        "RESEND_API_KEY",
        "INSTAGRAM_ACCESS_TOKEN",
    }:
        assert forbidden not in api
        assert forbidden not in worker
    assert proof["production_api_worker_env_inherited"] is False
    assert proof["production_database_env_inherited"] is False
    assert proof["production_sensitive_overlap"] is False
    assert proof["api_allowed_egress_hosts"] == [
        "rehearsal.eu.r2.cloudflarestorage.com"
    ]
    assert proof["worker_allowed_egress_hosts"] == [
        "rehearsal.eu.r2.cloudflarestorage.com",
        "api.openai.com",
    ]


def test_missing_dedicated_ai_key_is_an_explicit_provider_skip():
    api, worker, proof = _build(ai=False)

    assert "OPENAI_API_KEY" not in api
    assert "OPENAI_API_KEY" not in worker
    assert api["LECTURESIFT_REHEARSAL_AI_PROVIDER"] == "intentionally_absent"
    assert proof["ai_provider"] == "intentionally_absent"
    assert proof["api_allowed_egress_hosts"] == [
        "rehearsal.eu.r2.cloudflarestorage.com"
    ]
    assert proof["worker_allowed_egress_hosts"] == [
        "rehearsal.eu.r2.cloudflarestorage.com"
    ]


def test_domain_restricted_proxy_denies_arbitrary_public_destinations():
    _, _, proof = _build()
    api_policy = MODULE._render_squid_configuration(
        proof["api_allowed_egress_hosts"]
    )
    worker_policy = MODULE._render_squid_configuration(
        proof["worker_allowed_egress_hosts"]
    )

    assert ".rehearsal.eu.r2.cloudflarestorage.com" in api_policy
    assert ".api.openai.com" not in api_policy
    assert ".rehearsal.eu.r2.cloudflarestorage.com" in worker_policy
    assert ".api.openai.com" in worker_policy
    assert "example.com" not in api_policy
    assert "example.com" not in worker_policy
    assert "http_access allow allowed_rehearsal_domains" in api_policy
    assert api_policy.index("http_access allow allowed_rehearsal_domains") < api_policy.index(
        "http_access deny all"
    )


def test_rehearsal_values_equal_to_any_production_database_secret_fail_closed():
    with pytest.raises(
        MODULE.RehearsalEnvironmentError,
        match="sensitive rehearsal value equals a production value",
    ):
        _build(api_password="prod-owner-password")


def test_unreviewed_rehearsal_key_and_non_r2_endpoint_fail_closed():
    runtime, database, api, worker, rehearsal = _inputs()
    rehearsal["IYZICO_SECRET_KEY"] = "should-not-enter"
    with pytest.raises(MODULE.RehearsalEnvironmentError, match="unreviewed rehearsal"):
        MODULE.build_environments(
            runtime,
            database,
            api,
            worker,
            rehearsal,
            api_database_url=(
                f"postgresql+psycopg://lecturesift_rehearsal_api_{RUN}:isolated-api@"
                f"postgres:5432/{DATABASE}"
            ),
            worker_database_url=(
                f"postgresql+psycopg://lecturesift_rehearsal_worker_{RUN}:isolated-worker@"
                f"postgres:5432/{DATABASE}"
            ),
            revision=REVISION,
            run_id=RUN,
        )

    _, _, _, _, rehearsal = _inputs()
    rehearsal["LECTURESIFT_REHEARSAL_S3_ENDPOINT_URL"] = "https://example.com"
    with pytest.raises(MODULE.RehearsalEnvironmentError, match="storage endpoint"):
        MODULE.build_environments(
            runtime,
            database,
            api,
            worker,
            rehearsal,
            api_database_url=(
                f"postgresql+psycopg://lecturesift_rehearsal_api_{RUN}:isolated-api@"
                f"postgres:5432/{DATABASE}"
            ),
            worker_database_url=(
                f"postgresql+psycopg://lecturesift_rehearsal_worker_{RUN}:isolated-worker@"
                f"postgres:5432/{DATABASE}"
            ),
            revision=REVISION,
            run_id=RUN,
        )


def test_dotenv_is_parsed_as_data_and_shell_syntax_is_never_executed():
    values = MODULE.parse_dotenv_text(
        "SAFE='$(touch /tmp/must-not-exist)'\nQUOTED='literal value'\n",
        label="test dotenv",
    )
    assert values == {"SAFE": "$(touch /tmp/must-not-exist)", "QUOTED": "literal value"}
