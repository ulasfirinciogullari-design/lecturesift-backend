from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "deploy" / "generate_role_envs.py"
SPEC = importlib.util.spec_from_file_location("generate_role_envs", GENERATOR_PATH)
assert SPEC and SPEC.loader
role_envs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(role_envs)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _keys(rendered: str) -> set[str]:
    return {
        line.split("=", 1)[0]
        for line in rendered.splitlines()
        if line and not line.startswith("#")
    }


def _compose_service(text: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n.*?(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        text,
    )
    assert match, f"missing Compose service: {name}"
    return match.group(0)


def _runtime_fixture(tmp_path: Path) -> Path:
    source = tmp_path / "runtime.env"
    assignments = {
        "DATABASE_URL": "postgresql+psycopg://service:secret@postgres:5432/app",
        "LECTURESIFT_WORKER_DATABASE_URL": (
            "postgresql+psycopg://worker:worker-secret@postgres:5432/app"
        ),
        "CELERY_BROKER_URL": "redis://redis:6379/0",
        "REDIS_URL": "redis://redis:6379/0",
        "OPENAI_API_KEY": "ai-secret",
        "S3_ENDPOINT_URL": "https://storage.example.test",
        "S3_REGION": "auto",
        "S3_BUCKET": "application-bucket",
        "S3_ACCESS_KEY_ID": "r2-id",
        "S3_SECRET_ACCESS_KEY": "r2-secret",
        "LECTURESIFT_WORK_DIR": "/var/lib/lecturesift",
        "LECTURESIFT_REQUIRE_POSTGRES": "true",
        "LECTURESIFT_REQUIRE_DURABLE_PROCESSING": "true",
        "LECTURESIFT_TRANSCRIPTION_PARALLELISM": "4",
        "LECTURESIFT_COST_RENDER_MONTHLY_USD": "0",
        "LECTURESIFT_COST_VENDOR_RATE": "0.001",
        "PUBLIC_BASE_URL": "https://api.example.test",
        "INSTAGRAM_ACCESS_TOKEN": "ig-access",
        "INSTAGRAM_ACCOUNT_ID": "ig-account",
        "INSTAGRAM_APP_SECRET": "ig-proof",
        "INSTAGRAM_GRAPH_API_VERSION": "v23.0",
        "INSTAGRAM_DAILY_AUTOMATION_ENABLED": "true",
        "INSTAGRAM_DAILY_MEDIA_TYPE": "REELS",
        "INSTAGRAM_ADMIN_TOKEN": "ig-admin",
        "ADMIN_ADMIN": "root-admin",
        "BILLING_SESSION_SECRET": "session-secret",
        "PAYMENT_TOKEN_BINDING_SECRET": "payment-binding-secret",
        "PAYMENT_TOKEN_BINDING_LEGACY_SECRET": "old-payment-binding-secret",
        "BILLING_PROTECTED_EMAILS": "owner@example.test",
        "IYZICO_API_KEY": "payment-id",
        "IYZICO_SECRET_KEY": "payment-secret",
        "PAYTR_MERCHANT_KEY": "paytr-secret",
        "EMAIL_PROVIDER": "resend",
        "RESEND_API_KEY": "mail-secret",
        "SMTP_PASSWORD": "smtp-secret",
        "LEGAL_TAX_ID": "tax-secret",
        "BILLING_BANK_IBAN": "TR000000000000000000000000",
        "FRONTEND_BASE_URL": "https://example.test",
        # These must remain host-only even if runtime.env is contaminated.
        "RESTIC_REPOSITORY": "s3:https://backup.example.test/repository",
        "RESTIC_PASSWORD": "backup-encryption-secret",
        "RESTIC_AWS_ACCESS_KEY_ID": "backup-id",
        "RESTIC_AWS_SECRET_ACCESS_KEY": "backup-storage-secret",
        "RESTIC_FUTURE_TOKEN": "future-host-only-secret",
    }
    source.write_text(
        "# master\n" + "\n".join(f"{key}={value}" for key, value in assignments.items()) + "\n",
        encoding="utf-8",
    )
    return source


def test_worker_and_instagram_roles_are_exact_least_privilege(tmp_path: Path):
    assignments = role_envs.parse_runtime(_runtime_fixture(tmp_path))
    api = role_envs.render_role(assignments, "api")
    worker = role_envs.render_role(assignments, "worker")
    instagram = role_envs.render_role(assignments, "instagram")

    api_keys = _keys(api)
    worker_keys = _keys(worker)
    instagram_keys = _keys(instagram)
    host_only = {key for key in assignments if role_envs._host_only(key)}
    assert api_keys == set(assignments).difference(host_only)
    assert "DATABASE_URL" in worker_keys
    assert "LECTURESIFT_WORKER_DATABASE_URL" not in worker_keys
    assert (
        "DATABASE_URL=postgresql+psycopg://worker:worker-secret@postgres:5432/app"
        in worker
    )
    assert "LECTURESIFT_TRANSCRIPTION_PARALLELISM" in worker_keys
    assert "LECTURESIFT_COST_RENDER_MONTHLY_USD" in worker_keys
    assert instagram_keys == role_envs.INSTAGRAM_KEYS
    assert api_keys.isdisjoint(host_only)
    assert worker_keys.isdisjoint(host_only)
    assert instagram_keys.isdisjoint(host_only)

    forbidden_worker = {
        "ADMIN_ADMIN",
        "BILLING_SESSION_SECRET",
        "PAYMENT_TOKEN_BINDING_SECRET",
        "PAYMENT_TOKEN_BINDING_LEGACY_SECRET",
        "BILLING_PROTECTED_EMAILS",
        "IYZICO_API_KEY",
        "IYZICO_SECRET_KEY",
        "PAYTR_MERCHANT_KEY",
        "EMAIL_PROVIDER",
        "RESEND_API_KEY",
        "SMTP_PASSWORD",
        "INSTAGRAM_ACCESS_TOKEN",
        "INSTAGRAM_APP_SECRET",
        "INSTAGRAM_ADMIN_TOKEN",
        "LEGAL_TAX_ID",
        "BILLING_BANK_IBAN",
        "PUBLIC_BASE_URL",
        "FRONTEND_BASE_URL",
        "LECTURESIFT_WORKER_DATABASE_URL",
        "LECTURESIFT_COST_VENDOR_RATE",
    }
    assert worker_keys.isdisjoint(forbidden_worker)
    assert instagram_keys.isdisjoint(
        {
            "DATABASE_URL",
            "CELERY_BROKER_URL",
            "REDIS_URL",
            "OPENAI_API_KEY",
            "S3_ACCESS_KEY_ID",
            "S3_SECRET_ACCESS_KEY",
            "ADMIN_ADMIN",
            "INSTAGRAM_ADMIN_TOKEN",
            "BILLING_SESSION_SECRET",
            "PAYMENT_TOKEN_BINDING_SECRET",
            "PAYMENT_TOKEN_BINDING_LEGACY_SECRET",
            "IYZICO_SECRET_KEY",
            "PAYTR_MERCHANT_KEY",
            "RESEND_API_KEY",
            "SMTP_PASSWORD",
        }
    )


def test_disabled_instagram_role_needs_no_account_credentials(tmp_path: Path):
    source = tmp_path / "runtime.env"
    source.write_text(
        "DATABASE_URL=postgresql+psycopg://api:secret@postgres/app\n"
        "LECTURESIFT_WORKER_DATABASE_URL="
        "postgresql+psycopg://worker:secret@postgres/app\n"
        "INSTAGRAM_DAILY_AUTOMATION_ENABLED=false\n",
        encoding="utf-8",
    )
    assignments = role_envs.parse_runtime(source)

    assert _keys(role_envs.render_role(assignments, "instagram")) == {
        "INSTAGRAM_DAILY_AUTOMATION_ENABLED"
    }

    assignments["INSTAGRAM_DAILY_AUTOMATION_ENABLED"] = "true"
    with pytest.raises(role_envs.RoleEnvironmentError, match="missing instagram keys"):
        role_envs.render_role(assignments, "instagram")


def test_role_generation_rejects_ambiguous_or_dangerous_master_files(tmp_path: Path):
    duplicate = tmp_path / "duplicate.env"
    duplicate.write_text("DATABASE_URL=one\nDATABASE_URL=two\n", encoding="utf-8")
    with pytest.raises(role_envs.RoleEnvironmentError, match="duplicate"):
        role_envs.parse_runtime(duplicate)

    command = tmp_path / "command.env"
    command.write_text("SAFE=value\nsource /tmp/not-an-env-file\n", encoding="utf-8")
    with pytest.raises(role_envs.RoleEnvironmentError, match="unsupported"):
        role_envs.parse_runtime(command)

    bypass = tmp_path / "bypass.env"
    bypass.write_text("LECTURESIFT_RECOVERY_BOOTSTRAP_OVERRIDE=YES\n", encoding="utf-8")
    with pytest.raises(role_envs.RoleEnvironmentError, match="must stay outside"):
        role_envs.parse_runtime(bypass)

    unknown_secret = _runtime_fixture(tmp_path)
    with unknown_secret.open("a", encoding="utf-8") as handle:
        handle.write("FUTURE_VENDOR_API_KEY=must-not-enter-api\n")
    assignments = role_envs.parse_runtime(unknown_secret)
    with pytest.raises(role_envs.RoleEnvironmentError, match="unreviewed sensitive"):
        role_envs.render_role(assignments, "api")


def test_compose_uses_role_files_and_never_mounts_master_runtime_env():
    compose = _read("compose.yaml")
    common_anchor = compose.split("services:", 1)[0]

    assert "env_file:" not in common_anchor
    assert "${LECTURESIFT_ENV_FILE" not in compose
    assert "${LECTURESIFT_API_ENV_FILE:-/etc/lecturesift/api.env}" in compose
    assert "${LECTURESIFT_WORKER_ENV_FILE:-/etc/lecturesift/worker.env}" in compose
    assert "${LECTURESIFT_INSTAGRAM_ENV_FILE:-/etc/lecturesift/instagram.env}" in compose
    instagram = _compose_service(compose, "instagram")
    assert "      - backend" not in instagram
    assert "      - egress" in instagram


def test_worker_database_role_is_masked_and_narrowly_writable():
    role_sql = _read("deploy/postgres-app-role.sh")
    wrapper = _read("deploy/provision_database_role.sh")

    assert "PostgreSQL owner, API and worker roles must be distinct" in role_sql
    assert "NOINHERIT NOREPLICATION NOBYPASSRLS" in role_sql
    assert "REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC" in role_sql
    assert "PGCONNECT_TIMEOUT=5" in role_sql
    assert "probe_role_login API" in role_sql
    assert "probe_role_login worker" in role_sql
    assert "GRANT USAGE, CREATE ON SCHEMA public" not in role_sql
    assert "CREATE OR REPLACE VIEW lecturesift_worker.billing_users" in role_sql
    assert "''::varchar(64) AS password_hash" in role_sql
    assert "CREATE OR REPLACE VIEW lecturesift_worker.billing_auth_tokens" not in role_sql
    assert "CREATE OR REPLACE VIEW lecturesift_worker.lecturesift_admin" not in role_sql
    assert "CREATE OR REPLACE VIEW lecturesift_worker.lecturesift_contact" not in role_sql
    assert "WHERE plan_code = 'credit' AND status = 'paid'" in role_sql
    assert "GRANT UPDATE (credit_minutes) ON lecturesift_worker.billing_users" in role_sql
    assert "GRANT SELECT, INSERT ON lecturesift_worker.billing_usage_events" in role_sql
    assert "GRANT SELECT, INSERT ON lecturesift_worker.lecturesift_runtime_metrics" in role_sql
    assert "GRANT UPDATE (job_id, media_minutes, last_seen_at)" in role_sql
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I" in role_sql
    assert "SET LOCAL ROLE :\"worker_user\"" in role_sql
    assert "INSERT INTO billing_usage_events" in role_sql
    assert "INSERT INTO lecturesift_runtime_metrics" in role_sql
    assert "ROLLBACK;" in role_sql

    bootstrap = wrapper.index("LECTURESIFT_PROVISION_PHASE=bootstrap")
    migration = wrapper.index("--profile maintenance run --rm --no-deps")
    runtime = wrapper.index("LECTURESIFT_PROVISION_PHASE=runtime")
    assert bootstrap < migration < runtime


def test_preflight_and_systemd_fail_closed_on_missing_or_stale_role_files():
    preflight = _read("deploy/preflight.sh")
    service = _read("deploy/lecturesift.service")
    instagram_service = _read("deploy/lecturesift-instagram.service")
    backup_service = _read("deploy/lecturesift-backup.service")

    assert 'ROLE_ENV_GENERATOR="$ROOT_DIR/deploy/generate_role_envs.py"' in preflight
    assert 'python3 "$ROLE_ENV_GENERATOR"' in preflight
    assert 'python3 "$ROLE_ENV_GENERATOR" --check' in preflight
    for variable, path in (
        ("LECTURESIFT_API_ENV_FILE", "/etc/lecturesift/api.env"),
        ("LECTURESIFT_WORKER_ENV_FILE", "/etc/lecturesift/worker.env"),
        ("LECTURESIFT_INSTAGRAM_ENV_FILE", "/etc/lecturesift/instagram.env"),
    ):
        local_name = variable.removeprefix("LECTURESIFT_")
        assert f'check_private_env "${local_name}"' in preflight
        assert f"Environment={variable}={path}" in service
        assert f"Environment={variable}={path}" in instagram_service
        assert f"Environment={variable}={path}" in backup_service
    assert "generate_role_envs.py --check" in instagram_service
    assert "generate_role_envs.py --check" in backup_service
    reload_steps = [
        line
        for line in service.splitlines()
        if line.startswith("ExecReload=")
    ]
    assert reload_steps == [
        "ExecReload=/bin/bash /opt/lecturesift/deploy/preflight.sh",
        "ExecReload=/bin/bash /opt/lecturesift/deploy/release.sh build",
        "ExecReload=/usr/bin/docker compose up -d --wait --wait-timeout 300 postgres redis",
        "ExecReload=/bin/bash /opt/lecturesift/deploy/provision_database_role.sh",
        "ExecReload=/usr/bin/docker compose up -d --remove-orphans --wait --wait-timeout 600",
    ]
    assert "unreviewed sensitive runtime key" in _read("deploy/generate_role_envs.py")


def test_role_install_relies_on_fd_permissions_and_docs_validate_before_build():
    generator = _read("deploy/generate_role_envs.py")
    docs = _read("VPS_DEPLOYMENT.md")

    assert "os.fchmod(descriptor, 0o600)" in generator
    assert "os.fchown(descriptor, 0, 0)" in generator
    assert "os.chmod(destination" not in generator
    assert "os.chown(destination" not in generator
    assert "_check_private_file(destination, label=f\"{role} environment\")" in generator
    preflight_step = docs.index("LECTURESIFT_PREFLIGHT_CONTEXT=bootstrap-infrastructure")
    build_step = docs.index("sudo bash /opt/lecturesift/deploy/release.sh build")
    assert preflight_step < build_step
    assert "LECTURESIFT_RECOVERY_BOOTSTRAP_OVERRIDE=YES" in docs
    assert "LECTURESIFT_BOOTSTRAP_INFRASTRUCTURE_ONLY=YES" in docs
    assert "sudo systemctl reload lecturesift" in docs
    assert "Do not use a blind restart" in docs


def test_role_files_are_documented_and_ignored():
    docs = _read("VPS_DEPLOYMENT.md")
    env_example = _read("deploy/env.example")
    gitignore = _read(".gitignore")
    dockerignore = _read(".dockerignore")

    assert "runtime.env` is only a root-side source of truth" in docs
    assert "Do not manually create or edit `api.env`, `worker.env` or `instagram.env`" in docs
    assert "never mount it into a" in env_example
    for name in ("api.env", "worker.env", "instagram.env"):
        assert f"deploy/{name}" in gitignore
        assert f"deploy/{name}" in dockerignore
