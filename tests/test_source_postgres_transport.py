from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "deploy" / "source_postgres_transport.py"
SOURCE_SCRIPTS = (
    "deploy/migrate_postgres.sh",
    "deploy/rehearsal_restore.sh",
    "deploy/rollback_postgres_to_render.sh",
    "deploy/finalize_provider_cutover.sh",
    "deploy/seed_first_cutover_backup.sh",
)


def _module():
    spec = importlib.util.spec_from_file_location("source_postgres_transport", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_source(path: Path, *, sslmode: str = "verify-full", query: str | None = None) -> Path:
    suffix = query if query is not None else f"sslmode={sslmode}"
    path.write_text(
        "SOURCE_DATABASE_URL='postgresql://owner:p%40ssword@db.example.render.com:5432/lecturesift"
        f"?{suffix}'\n"
        "SOURCE_HEALTH_URL=https://lecturesift-backend.onrender.com/health\n"
        "SOURCE_REDIS_URL='rediss://default:redis-secret@cache.example.render.com:6379/0'\n"
        "SOURCE_CELERY_BROKER_URL="
        "'rediss://default:redis-secret@cache.example.render.com:6379/0'\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _source_values(*, query: str = "sslmode=verify-full") -> dict[str, str]:
    return {
        "SOURCE_DATABASE_URL": (
            "postgresql://owner:p%40ssword@db.example.render.com:5432/lecturesift"
            f"?{query}"
        ),
        "SOURCE_HEALTH_URL": "https://lecturesift-backend.onrender.com/health",
        "SOURCE_REDIS_URL": (
            "rediss://default:redis-secret@cache.example.render.com:6379/0"
        ),
        "SOURCE_CELERY_BROKER_URL": (
            "rediss://default:redis-secret@cache.example.render.com:6379/0"
        ),
    }


def test_source_transport_canonicalizes_to_secret_free_libpq_argv_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    helper = _module()
    monkeypatch.setenv("SOURCE_DATABASE_URL", "postgresql://stale:secret@bad/old")
    monkeypatch.setenv("PGSERVICE", "unsafe-service")
    monkeypatch.setenv("PGHOSTADDR", "127.0.0.1")
    monkeypatch.setenv("PGOPTIONS", "-c search_path=attacker")
    monkeypatch.setenv("PGAPPNAME", "unsafe-app")

    configuration = helper.configuration_from_values(_source_values())
    environment = helper.libpq_environment(configuration)

    assert environment["PGHOST"] == "db.example.render.com"
    assert environment["PGPORT"] == "5432"
    assert environment["PGDATABASE"] == "lecturesift"
    assert environment["PGUSER"] == "owner"
    assert "PGPASSWORD" not in environment
    assert "PGPASSFILE" not in environment
    assert environment["PGSSLMODE"] == "verify-full"
    assert environment["PGSSLROOTCERT"] == "system"
    assert environment["PGCONNECT_TIMEOUT"] == "15"
    assert "SOURCE_DATABASE_URL" not in environment
    assert "PGSERVICE" not in environment
    assert "PGHOSTADDR" not in environment
    assert "PGOPTIONS" not in environment
    assert "PGAPPNAME" not in environment


def test_source_child_scopes_disclose_only_the_endpoint_family_they_need():
    helper = _module()
    configuration = helper.configuration_from_values(_source_values())

    fingerprint = helper.source_environment(configuration, "fingerprint")
    health = helper.source_environment(configuration, "health")
    redis = helper.source_environment(configuration, "redis")

    assert helper.SOURCE_KEYS.intersection(fingerprint) == {
        "SOURCE_DATABASE_URL",
        "SOURCE_HEALTH_URL",
        "SOURCE_REDIS_URL",
        "SOURCE_CELERY_BROKER_URL",
    }
    assert helper.SOURCE_KEYS.intersection(health) == {"SOURCE_HEALTH_URL"}
    assert helper.SOURCE_KEYS.intersection(redis) == {
        "SOURCE_REDIS_URL",
        "SOURCE_CELERY_BROKER_URL",
    }
    assert "SOURCE_DATABASE_URL" not in health
    assert "SOURCE_DATABASE_URL" not in redis


@pytest.mark.parametrize(
    "query",
    (
        "",
        "sslmode=require",
        "sslmode=verify-ca",
        "sslmode=verify-full&sslmode=require",
        "sslmode=verify-full&application_name=leak",
    ),
)
def test_source_transport_rejects_every_non_exact_tls_mode(tmp_path: Path, query: str):
    helper = _module()
    with pytest.raises(helper.TransportError, match="sslmode=verify-full"):
        helper.configuration_from_values(_source_values(query=query))


def test_source_dotenv_is_data_and_never_shell_executed(tmp_path: Path):
    helper = _module()
    marker = tmp_path / "must-not-exist"
    lines = (
        f"SOURCE_DATABASE_URL=$(touch {marker})\n"
        "SOURCE_HEALTH_URL=https://lecturesift-backend.onrender.com/health\n"
        "SOURCE_REDIS_URL=rediss://default:secret@cache.example.render.com:6379/0\n"
        "SOURCE_CELERY_BROKER_URL=rediss://default:secret@cache.example.render.com:6379/0\n"
    ).splitlines()

    with pytest.raises(helper.TransportError):
        helper._parse_dotenv_lines(lines)
    assert not marker.exists()


def test_source_dotenv_rejects_a_hardlinked_secret_file(tmp_path: Path):
    helper = _module()
    source = _write_source(tmp_path / "source.env")
    os.link(source, tmp_path / "second-name.env")

    with pytest.raises(helper.TransportError, match="single-link"):
        helper.load_configuration(source.resolve())


def test_cli_keeps_the_secret_file_option_outside_the_child_argv(
    monkeypatch: pytest.MonkeyPatch,
):
    helper = _module()
    source = Path("/root/.lecturesift-render-source.env")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "source_postgres_transport.py",
            "exec-libpq-docker",
            "--source-env",
            str(source),
            "--",
            "docker",
            "run",
            "postgres:18-bookworm",
            "psql",
        ],
    )

    arguments = helper._arguments()
    assert arguments.source_env == source
    assert arguments.command == [
        "--",
        "docker",
        "run",
        "postgres:18-bookworm",
        "psql",
    ]


def test_libpq_docker_uses_a_private_pgpass_mount_and_cleans_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    helper = _module()
    configuration = helper.configuration_from_values(_source_values())
    session = tmp_path / "session-test"
    session.mkdir(mode=0o700)
    pgpass = session / "pgpass"
    pgpass.write_text(helper.pgpass_record(configuration), encoding="utf-8")
    pgpass.chmod(0o600)
    observed: dict[str, object] = {}

    monkeypatch.setattr(helper, "_create_pgpass", lambda _config: (session, pgpass))

    def run_child(command: list[str], environment: dict[str, str]) -> int:
        observed["command"] = command
        observed["environment"] = environment
        observed["pgpass"] = pgpass.read_text(encoding="utf-8")
        assert pgpass.is_file()
        return 0

    monkeypatch.setattr(helper, "_run_child", run_child)
    result = helper._run_libpq_docker(
        ["--", "docker", "run", "postgres:18-bookworm", "psql"],
        configuration,
    )

    assert result == 0
    command = observed["command"]
    environment = observed["environment"]
    assert isinstance(command, list) and isinstance(environment, dict)
    assert command[:2] == ["docker", "run"]
    assert helper.CONTAINER_PGPASSFILE in " ".join(command)
    assert configuration.password not in " ".join(command)
    assert configuration.database_url not in " ".join(command)
    assert configuration.password not in environment.values()
    assert "PGPASSWORD" not in environment
    assert observed["pgpass"].endswith(":p@ssword\n")
    assert not pgpass.exists()
    assert not session.exists()


def test_libpq_docker_rejects_credential_arguments_before_creating_a_secret_file(
    monkeypatch: pytest.MonkeyPatch,
):
    helper = _module()
    configuration = helper.configuration_from_values(_source_values())
    created = False

    def should_not_create(_config: object) -> object:
        nonlocal created
        created = True
        raise AssertionError("password file must not be created")

    monkeypatch.setattr(helper, "_create_pgpass", should_not_create)
    with pytest.raises(helper.TransportError, match="forbidden credential"):
        helper._run_libpq_docker(
            ["docker", "run", "--env", "PGPASSWORD", "postgres:18-bookworm"],
            configuration,
        )
    assert created is False


def test_cutover_scripts_never_put_source_url_or_password_in_command_lines():
    for relative in SOURCE_SCRIPTS:
        script = (ROOT / relative).read_text(encoding="utf-8")
        lines = script.splitlines()
        assert lines[:2] == ["#!/usr/bin/env bash", "set +x"]
        assert "source_postgres_transport.py" in script
        assert "SOURCE_PG_DOCKER_ENV" in script
        assert "exec-libpq-docker" in script
        assert "--env PGSSLMODE --env PGSSLROOTCERT" in script
        assert "--env PGPASSWORD" not in script
        assert "SOURCE_DATABASE_URL" not in script
        assert "source /run/secrets/render-source.env" not in script
        assert "render-source.env:ro" not in script
        assert "pg_dump \"$SOURCE_DATABASE_URL\"" not in script
        assert "psql --no-psqlrc \"$SOURCE_DATABASE_URL\"" not in script
        assert "pg_restore --dbname \"$SOURCE_DATABASE_URL\"" not in script


@pytest.mark.skipif(
    not (ROOT / ".local-secrets" / "install_cutover_inputs.py").exists(),
    reason="operator-only ignored cutover installer is not present in repository clones",
)
def test_operator_installer_upgrades_and_publishes_only_verify_full():
    script = (ROOT / ".local-secrets" / "install_cutover_inputs.py").read_text(
        encoding="utf-8"
    )

    assert 'database_pairs = [("sslmode", "verify-full")]' in script
    assert 'database_query == {"sslmode": ["verify-full"]}' in script
    assert 'database_pairs.append(("sslmode", "require"))' not in script
    assert '"password_length"' not in script
    assert "secret_key_length" not in script
