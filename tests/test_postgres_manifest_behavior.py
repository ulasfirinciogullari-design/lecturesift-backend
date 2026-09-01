from __future__ import annotations

import importlib.util
import hashlib
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "deploy" / "rehearsal_manifest.sql"
CONTRACT = ROOT / "deploy" / "schema_contract_payment_provider_sessions_v1.txt"
VERIFIER_PATH = ROOT / "deploy" / "verify_schema_transition.py"

SPEC = importlib.util.spec_from_file_location("schema_transition_verifier", VERIFIER_PATH)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def _contract_lines() -> list[str]:
    return [
        line
        for line in CONTRACT.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]


def _manifest_text(objects: list[str], *, legacy: bool = False) -> str:
    objects = sorted(objects)
    object_payload = [
        line.removeprefix(verifier.PREFIX) for line in objects
    ]
    schema_digest = hashlib.md5(
        "\n".join(object_payload).encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    tables = sorted(
        verifier.EXPECTED_TABLES
        - ({"billing_payment_provider_sessions"} if legacy else set())
    )
    anomalies = sorted(
        verifier.BASE_ANOMALIES
        | (frozenset() if legacy else verifier.PROVIDER_ANOMALIES)
    )
    lines = [
        "DATABASE|18|UTF8|en_US.UTF-8|en_US.UTF-8|c|2.36|UTC",
        "DATABASE_SIZE|1",
        f"SCHEMA|{len(objects)}|{schema_digest}",
        *objects,
        *(f"TABLE|{table}|0|0|0" for table in tables),
        *(f"ANOMALY|{name}|0" for name in anomalies),
    ]
    if legacy:
        lines.append(verifier.LEGACY_MARKER)
    counts = {
        family: sum(line.startswith(f"{family}|") for line in lines)
        for family in verifier.MANIFEST_FAMILIES
    }
    lines.append(
        "MANIFEST_COMPLETE|v2|"
        + "|".join(
            f"{family}|{counts[family]}" for family in verifier.MANIFEST_FAMILIES
        )
    )
    return "\n".join(lines) + "\n"


def test_manifest_generator_quiets_psql_before_configuring_record_output():
    lines = MANIFEST.read_text(encoding="utf-8").splitlines()

    quiet = lines.index(r"\set QUIET on")
    tuples_only = lines.index(r"\pset tuples_only on")
    unaligned = lines.index(r"\pset format unaligned")

    assert quiet < tuples_only < unaligned


def test_schema_transition_fixture_rejects_unreviewed_catalog_deltas(tmp_path: Path):
    contract = _contract_lines()
    baseline = [
        "SCHEMA_OBJECT|C|public.billing_users|1|id|character varying(36)|t|||",
        "SCHEMA_OBJECT|K|billing_users|billing_users_pkey|t|PRIMARY KEY (id)",
    ]
    before = tmp_path / "before.txt"
    after = tmp_path / "after.txt"
    before.write_text(_manifest_text(baseline, legacy=True), encoding="utf-8")
    after.write_text(_manifest_text(baseline + contract), encoding="utf-8")

    transition, _ = verifier.verify_transition(before, after, CONTRACT)
    assert transition == "legacy_to_current"
    assert verifier.verify_current(after, CONTRACT)
    assert verifier.verify_legacy(before, CONTRACT)[0] == "legacy_missing_provider_sessions"
    assert verifier.verify_legacy(after, CONTRACT)[0] == "current"

    extra_column = tmp_path / "extra-column.txt"
    extra_column.write_text(
        _manifest_text(
            baseline
            + contract
            + [
                "SCHEMA_OBJECT|C|public.billing_payment_provider_sessions|5|unexpected|text|f|||"
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(verifier.ContractError):
        verifier.verify_current(extra_column, CONTRACT)

    missing_index = tmp_path / "missing-index.txt"
    missing_index.write_text(
        _manifest_text([line for line in baseline + contract if "|I|" not in line]),
        encoding="utf-8",
    )
    with pytest.raises(verifier.ContractError):
        verifier.verify_current(missing_index, CONTRACT)

    missing_constraint = tmp_path / "missing-constraint.txt"
    missing_constraint.write_text(
        _manifest_text(
            [
                line
                for line in baseline + contract
                if "billing_payment_provider_sessions_order_reference_fkey" not in line
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(verifier.ContractError):
        verifier.verify_current(missing_constraint, CONTRACT)

    unrelated_change = tmp_path / "unrelated-change.txt"
    unrelated_change.write_text(
        _manifest_text(
            [
                "SCHEMA_OBJECT|C|public.billing_users|1|id|text|t|||",
                baseline[1],
                *contract,
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(verifier.ContractError):
        verifier.verify_transition(before, unrelated_change, CONTRACT)


def test_manifest_completion_rejects_truncation_old_versions_and_missing_checks(
    tmp_path: Path,
):
    objects = [
        "SCHEMA_OBJECT|C|public.billing_users|1|id|character varying(36)|t|||",
        "SCHEMA_OBJECT|K|billing_users|billing_users_pkey|t|PRIMARY KEY (id)",
        *_contract_lines(),
    ]
    valid_lines = _manifest_text(objects).splitlines()

    no_sentinel = tmp_path / "no-sentinel.txt"
    no_sentinel.write_text("\n".join(valid_lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(verifier.ContractError, match="terminal"):
        verifier.verify_current(no_sentinel, CONTRACT)

    missing_middle = tmp_path / "missing-middle.txt"
    removed_table = next(
        index
        for index, line in enumerate(valid_lines)
        if line.startswith("TABLE|billing_users|")
    )
    missing_middle.write_text(
        "\n".join(valid_lines[:removed_table] + valid_lines[removed_table + 1 :])
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(verifier.ContractError, match="counts"):
        verifier.verify_current(missing_middle, CONTRACT)

    old_version = tmp_path / "old-version.txt"
    old_lines = valid_lines.copy()
    old_lines[-1] = old_lines[-1].replace(
        "MANIFEST_COMPLETE|v2|", "MANIFEST_COMPLETE|v1|"
    )
    old_version.write_text("\n".join(old_lines) + "\n", encoding="utf-8")
    with pytest.raises(verifier.ContractError, match="completion contract"):
        verifier.verify_current(old_version, CONTRACT)

    missing_anomaly = [
        line
        for line in valid_lines[:-1]
        if not line.startswith("ANOMALY|negative_user_credit|")
    ]
    counts = {
        family: sum(line.startswith(f"{family}|") for line in missing_anomaly)
        for family in verifier.MANIFEST_FAMILIES
    }
    missing_anomaly.append(
        "MANIFEST_COMPLETE|v2|"
        + "|".join(
            f"{family}|{counts[family]}" for family in verifier.MANIFEST_FAMILIES
        )
    )
    missing_check = tmp_path / "missing-check.txt"
    missing_check.write_text("\n".join(missing_anomaly) + "\n", encoding="utf-8")
    with pytest.raises(verifier.ContractError, match="exact anomaly checks"):
        verifier.verify_current(missing_check, CONTRACT)


@pytest.mark.parametrize(
    "chatter",
    ("Tuples only is on.", "Output format is unaligned."),
)
def test_manifest_rejects_psql_informational_chatter(tmp_path: Path, chatter: str):
    objects = [
        "SCHEMA_OBJECT|C|public.billing_users|1|id|character varying(36)|t|||",
        "SCHEMA_OBJECT|K|billing_users|billing_users_pkey|t|PRIMARY KEY (id)",
        *_contract_lines(),
    ]
    contaminated = tmp_path / "psql-chatter.txt"
    contaminated.write_text(
        chatter + "\n" + _manifest_text(objects),
        encoding="utf-8",
    )

    with pytest.raises(verifier.ContractError, match="unknown manifest record family"):
        verifier.verify_current(contaminated, CONTRACT)


def test_manifest_rejects_malformed_record_shapes_and_noncanonical_values(
    tmp_path: Path,
):
    objects = [
        "SCHEMA_OBJECT|C|public.billing_users|1|id|character varying(36)|t|||",
        "SCHEMA_OBJECT|K|billing_users|billing_users_pkey|t|PRIMARY KEY (id)",
        *_contract_lines(),
    ]
    valid_lines = _manifest_text(objects).splitlines()

    database_index = next(
        index for index, line in enumerate(valid_lines) if line.startswith("DATABASE|")
    )
    schema_index = next(
        index for index, line in enumerate(valid_lines) if line.startswith("SCHEMA|")
    )
    database_size_index = next(
        index
        for index, line in enumerate(valid_lines)
        if line.startswith("DATABASE_SIZE|")
    )
    table_index = next(
        index for index, line in enumerate(valid_lines) if line.startswith("TABLE|")
    )
    anomaly_index = next(
        index for index, line in enumerate(valid_lines) if line.startswith("ANOMALY|")
    )

    malformed: dict[str, list[str]] = {}
    for label, index, replacement in (
        ("database-arity", database_index, "DATABASE|18|UTF8"),
        (
            "database-version",
            database_index,
            valid_lines[database_index].replace("DATABASE|18|", "DATABASE|018|", 1),
        ),
        (
            "database-empty-encoding",
            database_index,
            valid_lines[database_index].replace("|UTF8|", "||", 1),
        ),
        (
            "database-extra-field",
            database_index,
            valid_lines[database_index] + "|unexpected",
        ),
        ("database-size-arity", database_size_index, "DATABASE_SIZE|1|extra"),
        ("database-size-zero", database_size_index, "DATABASE_SIZE|0"),
        ("database-size-format", database_size_index, "DATABASE_SIZE|+1"),
        ("schema-arity", schema_index, "SCHEMA|1"),
        (
            "schema-count-format",
            schema_index,
            valid_lines[schema_index].replace("SCHEMA|", "SCHEMA|0", 1),
        ),
        (
            "schema-digest",
            schema_index,
            valid_lines[schema_index].rsplit("|", 1)[0] + "|" + "A" * 32,
        ),
        (
            "table-arity",
            table_index,
            "|".join(valid_lines[table_index].split("|")[:2]),
        ),
        (
            "table-count",
            table_index,
            valid_lines[table_index].replace("|0|0|0", "|+0|0|0"),
        ),
        (
            "table-hash",
            table_index,
            valid_lines[table_index].rsplit("|", 1)[0] + "|+0",
        ),
        (
            "table-extra-field",
            table_index,
            valid_lines[table_index] + "|unexpected",
        ),
        (
            "anomaly-arity",
            anomaly_index,
            "|".join(valid_lines[anomaly_index].split("|")[:2]),
        ),
        (
            "anomaly-count",
            anomaly_index,
            valid_lines[anomaly_index].rsplit("|", 1)[0] + "|+0",
        ),
        (
            "anomaly-extra-field",
            anomaly_index,
            valid_lines[anomaly_index] + "|unexpected",
        ),
    ):
        candidate = valid_lines.copy()
        candidate[index] = replacement
        malformed[label] = candidate

    invalid_status = valid_lines[:-1] + [
        "STATUS|unknown_family|active|1",
        valid_lines[-1].replace("|STATUS|0|", "|STATUS|1|"),
    ]
    malformed["status-family"] = invalid_status
    invalid_status_count = valid_lines[:-1] + [
        "STATUS|subscription|active|01",
        valid_lines[-1].replace("|STATUS|0|", "|STATUS|1|"),
    ]
    malformed["status-count"] = invalid_status_count
    invalid_status_value = valid_lines[:-1] + [
        "STATUS|subscription|not canonical|1",
        valid_lines[-1].replace("|STATUS|0|", "|STATUS|1|"),
    ]
    malformed["status-value"] = invalid_status_value
    invalid_status_arity = valid_lines[:-1] + [
        "STATUS|subscription|active",
        valid_lines[-1].replace("|STATUS|0|", "|STATUS|1|"),
    ]
    malformed["status-arity"] = invalid_status_arity
    duplicate_status = valid_lines[:-1] + [
        "STATUS|subscription|active|1",
        "STATUS|subscription|active|2",
        valid_lines[-1].replace("|STATUS|0|", "|STATUS|2|"),
    ]
    malformed["status-duplicate"] = duplicate_status

    noncanonical_sentinel = valid_lines.copy()
    noncanonical_sentinel[-1] = noncanonical_sentinel[-1].replace(
        "|DATABASE|1|", "|DATABASE|01|", 1
    )
    malformed["sentinel-count"] = noncanonical_sentinel
    wrong_version_sentinel = valid_lines.copy()
    wrong_version_sentinel[-1] = wrong_version_sentinel[-1].replace(
        "MANIFEST_COMPLETE|v2|", "MANIFEST_COMPLETE|v02|", 1
    )
    malformed["sentinel-version"] = wrong_version_sentinel
    extra_field_sentinel = valid_lines.copy()
    extra_field_sentinel[-1] += "|unexpected"
    malformed["sentinel-extra-field"] = extra_field_sentinel

    for label, lines in malformed.items():
        path = tmp_path / f"{label}.txt"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with pytest.raises(verifier.ContractError):
            verifier.verify_current(path, CONTRACT)


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        timeout=kwargs.pop("timeout", 180),
        **kwargs,
    )


def _docker() -> str:
    executable = shutil.which("docker")
    if not executable:
        pytest.skip("Docker is unavailable; PostgreSQL behavior test requires Docker")
    info = _run([executable, "info", "--format", "{{.ServerVersion}}"], timeout=20)
    if info.returncode != 0:
        pytest.skip("Docker daemon is unavailable; PostgreSQL behavior test requires Docker")
    return executable


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _assert_clean_manifest(output: str, *, legacy: bool = False) -> None:
    assert not any(
        line.startswith(("TABLE_DIFF|", "UNVALIDATED_FK|"))
        for line in output.splitlines()
    )
    assert not any(
        line.startswith("ANOMALY|") and line.rsplit("|", 1)[-1] != "0"
        for line in output.splitlines()
    )
    compat = [line for line in output.splitlines() if line.startswith("SCHEMA_COMPAT|")]
    assert compat == ([verifier.LEGACY_MARKER] if legacy else [])


def test_manifest_legacy_strict_current_and_schema_contract_on_postgres_18(tmp_path: Path):
    docker = _docker()
    container = f"lecturesift-manifest-test-{uuid.uuid4().hex[:12]}"
    database = "lecturesift_rehearsal_20260831063027"
    password = "manifest-test-only-password"
    port = _free_port()
    run = _run(
        [
            docker,
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "--env",
            f"POSTGRES_PASSWORD={password}",
            "--env",
            f"POSTGRES_DB={database}",
            "--publish",
            f"127.0.0.1:{port}:5432",
            "postgres:18-bookworm",
        ],
        timeout=300,
    )
    if run.returncode != 0:
        pytest.fail(f"PostgreSQL 18 container failed to start: {run.stderr[-1000:]}")

    def docker_exec(*args: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
        return _run([docker, "exec", container, *args], timeout=timeout)

    def sql(command: str) -> None:
        result = docker_exec(
            "psql",
            "--no-psqlrc",
            "--username",
            "postgres",
            "--dbname",
            database,
            "--set=ON_ERROR_STOP=1",
            "--command",
            command,
        )
        assert result.returncode == 0, result.stderr

    def manifest(*, legacy: bool = False) -> str:
        command = [
            "psql",
            "--no-psqlrc",
            "--username",
            "postgres",
            "--dbname",
            database,
            "--set=ON_ERROR_STOP=1",
        ]
        if legacy:
            command.extend(["--set=LECTURESIFT_ALLOW_LEGACY_PROVIDER_SESSIONS=on"])
        command.extend(["--file", "/tmp/rehearsal_manifest.sql"])
        result = docker_exec(*command)
        assert result.returncode == 0, result.stderr
        output = result.stdout.replace("\r\n", "\n")
        assert {
            "Tuples only is on.",
            "Output format is unaligned.",
        }.isdisjoint(output.splitlines())
        return output

    database_url = (
        f"postgresql+psycopg://postgres:{password}@127.0.0.1:{port}/{database}"
    )
    app_env = os.environ.copy()
    app_env.update(
        {
            "DATABASE_URL": database_url,
            "LECTURESIFT_REQUIRE_POSTGRES": "true",
            "PYTHONPATH": str(ROOT),
            "LECTURESIFT_WORK_DIR": str(tmp_path / "work"),
        }
    )

    def migrate() -> None:
        result = _run(
            [
                sys.executable,
                "-c",
                (
                    "from lecturesift.rollout_service import init_rollout_database; "
                    "from lecturesift.costs import init_cost_database; "
                    "init_rollout_database(); init_cost_database()"
                ),
            ],
            cwd=ROOT,
            env=app_env,
        )
        assert result.returncode == 0, result.stderr

    def verify_cli(command: str, manifest_path: Path, before: Path | None = None):
        arguments = [sys.executable, str(VERIFIER_PATH), command]
        if command == "current":
            arguments.extend(["--manifest", str(manifest_path)])
        else:
            assert before is not None
            arguments.extend(["--before", str(before), "--after", str(manifest_path)])
        arguments.extend(["--contract", str(CONTRACT)])
        return _run(arguments, cwd=ROOT)

    try:
        ready = False
        for _ in range(60):
            probe = docker_exec("pg_isready", "--username", "postgres", "--dbname", database)
            if probe.returncode == 0:
                ready = True
                break
            time.sleep(0.5)
        assert ready, "PostgreSQL 18 did not become ready"
        copied = _run([docker, "cp", str(MANIFEST), f"{container}:/tmp/rehearsal_manifest.sql"])
        assert copied.returncode == 0, copied.stderr

        migrate()
        sql("CREATE TABLE billing_email_verifications (id varchar(36) PRIMARY KEY)")
        current = manifest()
        _assert_clean_manifest(current)
        current_path = tmp_path / "current.txt"
        current_path.write_text(current, encoding="utf-8")
        verified = verify_cli("current", current_path)
        assert verified.returncode == 0, verified.stderr

        expected = set(_contract_lines())
        actual = {
            line
            for line in current.splitlines()
            if any(line.startswith(prefix) for prefix in verifier.TARGET_PREFIXES)
        }
        assert actual == expected

        sql("DROP TABLE billing_payment_provider_sessions")
        legacy = manifest(legacy=True)
        _assert_clean_manifest(legacy, legacy=True)
        assert "TABLE_DIFF|missing|billing_payment_provider_sessions" not in legacy
        legacy_path = tmp_path / "legacy.txt"
        legacy_path.write_text(legacy, encoding="utf-8")

        strict_missing = manifest()
        assert "TABLE_DIFF|missing|billing_payment_provider_sessions" in strict_missing
        assert "ANOMALY|required_payment_provider_sessions_table_missing|1" in strict_missing

        migrate()
        migrated = manifest()
        _assert_clean_manifest(migrated)
        migrated_path = tmp_path / "migrated.txt"
        migrated_path.write_text(migrated, encoding="utf-8")
        transition = verify_cli("transition", migrated_path, legacy_path)
        assert transition.returncode == 0, transition.stderr
        assert "schema_transition=legacy_to_current" in transition.stdout

        sql("ALTER TABLE billing_payment_provider_sessions ADD COLUMN unexpected text")
        extra_column_path = tmp_path / "extra-column-real.txt"
        extra_column_path.write_text(manifest(), encoding="utf-8")
        assert verify_cli("current", extra_column_path).returncode != 0
        sql("ALTER TABLE billing_payment_provider_sessions DROP COLUMN unexpected")

        sql("CREATE INDEX unexpected_provider_idx ON billing_payment_provider_sessions(provider)")
        extra_index_path = tmp_path / "extra-index-real.txt"
        extra_index_path.write_text(manifest(), encoding="utf-8")
        assert verify_cli("current", extra_index_path).returncode != 0
        sql("DROP INDEX unexpected_provider_idx")

        sql(
            "ALTER TABLE billing_payment_provider_sessions DROP CONSTRAINT "
            "billing_payment_provider_sessions_order_reference_fkey"
        )
        missing_constraint_path = tmp_path / "missing-constraint-real.txt"
        missing_constraint_path.write_text(manifest(), encoding="utf-8")
        assert verify_cli("current", missing_constraint_path).returncode != 0
    finally:
        _run([docker, "rm", "--force", container], timeout=60)
