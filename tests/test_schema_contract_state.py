"""Exact live schema-contract detection and the routes every cutover tool uses."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
SPEC = importlib.util.spec_from_file_location(
    "lecturesift_schema_contract_state", DEPLOY / "schema_contract_state.py"
)
assert SPEC and SPEC.loader
state = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = state
SPEC.loader.exec_module(state)


def _inventory(path: Path, tables, *, count: int | None = None) -> Path:
    names = sorted(tables)
    lines = [f"PUBLIC_TABLE|{name}" for name in names]
    lines.append(f"INVENTORY_COMPLETE|v1|{len(names) if count is None else count}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_routes_match_backup_product_schema_versions_one_and_two():
    backup = _read("deploy/backup.sh")
    routing = backup[backup.index('case "${LECTURESIFT_PRODUCT_SCHEMA_VERSION:-0}" in'):]
    routing = routing[: routing.index("esac")]
    for version in (4, 5):
        route = state.ROUTES[version]
        recovery = route.recovery_manifest_version
        assert (
            f"{route.product_schema_version}) RECOVERY_MANIFEST_VERSION={recovery}; "
            f'RECOVERY_MANIFEST="$ROOT_DIR/deploy/{route.recovery_manifest}" ;;'
        ) in routing
        assert route.application_schema_compatibility == f"lecturesift-schema-v{recovery}"
        assert route.verifier == f"verify_schema_transition_v{version}.py"
        assert route.manifest == f"rehearsal_manifest_v{version}.sql"
        # The recovery manifest is the strict form of the same verifier family.
        recovery_sql = _read(f"deploy/{route.recovery_manifest}")
        assert f"'MANIFEST_COMPLETE|v{version}'" in recovery_sql
        assert f"'MANIFEST_COMPLETE|v{version}'" in _read(f"deploy/{route.manifest}")
    # Product schema version 0 is the historical core-only recovery family.
    assert "0) ;;" in routing
    assert state.ROUTES[3].product_schema_version == 0
    assert state.ROUTES[3].recovery_manifest == "recovery_manifest_v2.sql"
    assert state.PROVIDER_SOURCE_VERSIONS == frozenset({4, 5})
    for script in ("restore.sh", "restic_restore_rehearsal.sh"):
        source = _read(f"deploy/{script}")
        for version in (4, 5):
            route = state.ROUTES[version]
            assert (
                f"  {route.recovery_manifest_version})\n"
                f'    RECOVERY_MANIFEST="$ROOT_DIR/deploy/{route.recovery_manifest}"\n'
                f'    expected_schema_compatibility="{route.application_schema_compatibility}"\n'
            ) in source
        # Recovery v3/v4 metadata is verified by the v4/v5 verifier family.
        assert "  schema_verifier=4\n" in source
        assert '[[ "$metadata_manifest_version" != "4" ]] || schema_verifier=5' in source


@pytest.mark.parametrize("version", [3, 4, 5])
def test_each_exact_family_is_detected(tmp_path: Path, version: int):
    inventory = _inventory(tmp_path / "inventory.txt", state.TABLE_SETS[version])
    assert state.detect(inventory, state.ALL_VERSIONS) == version
    assert state.main(["detect", "--inventory", str(inventory), "--accept", "3,4,5"]) == 0


def test_provider_sources_refuse_the_core_v3_family(tmp_path: Path, capsys):
    inventory = _inventory(tmp_path / "inventory.txt", state.TABLE_SETS[3])
    with pytest.raises(state.SchemaStateError, match="v3 is not accepted"):
        state.detect(inventory, state.PROVIDER_SOURCE_VERSIONS)
    assert state.main(["detect", "--inventory", str(inventory), "--accept", "4,5"]) == 1
    assert capsys.readouterr().out == ""
    for version in (4, 5):
        inventory = _inventory(tmp_path / f"v{version}.txt", state.TABLE_SETS[version])
        assert state.main(["detect", "--inventory", str(inventory), "--accept", "4,5"]) == 0
        assert capsys.readouterr().out == f"{version}\n"


@pytest.mark.parametrize(
    "tables",
    [
        # A product release that stopped half way.
        state.TABLE_SETS[4] - {"billing_referral_rewards"},
        state.TABLE_SETS[3] | {"assistant_credit_grants_v1"},
        # A workspace release with one table missing or one table early.
        state.TABLE_SETS[5] - {"workspace_items_v1"},
        state.TABLE_SETS[4] | {"admin_ad_free_grants_v1"},
        # Unknown extra or missing core tables.
        state.TABLE_SETS[5] | {"unreviewed_table"},
        state.TABLE_SETS[4] - {"billing_purchase_terms"},
        frozenset(),
    ],
)
def test_partial_or_unknown_table_sets_are_rejected(tmp_path: Path, tables):
    inventory = _inventory(tmp_path / "inventory.txt", tables)
    with pytest.raises(state.SchemaStateError, match="partial or matches no exact"):
        state.detect(inventory, state.ALL_VERSIONS)


def test_inventory_records_are_exact(tmp_path: Path):
    path = tmp_path / "inventory.txt"
    tables = sorted(state.TABLE_SETS[4])
    cases = {
        "no completion": "\n".join(f"PUBLIC_TABLE|{name}" for name in tables) + "\n",
        "wrong count": "\n".join(
            [*(f"PUBLIC_TABLE|{name}" for name in tables), f"INVENTORY_COMPLETE|v1|{len(tables) + 1}"]
        ),
        "duplicate": "\n".join(
            [*(f"PUBLIC_TABLE|{name}" for name in [*tables, tables[0]]),
             f"INVENTORY_COMPLETE|v1|{len(tables) + 1}"]
        ),
        "unknown family": "\n".join(
            ["TABLE|billing_users|0|0|0", *(f"PUBLIC_TABLE|{name}" for name in tables),
             f"INVENTORY_COMPLETE|v1|{len(tables)}"]
        ),
        "wrong version": "\n".join(
            [*(f"PUBLIC_TABLE|{name}" for name in tables), f"INVENTORY_COMPLETE|v2|{len(tables)}"]
        ),
        "trailing record": "\n".join(
            [*(f"PUBLIC_TABLE|{name}" for name in tables[1:]),
             f"INVENTORY_COMPLETE|v1|{len(tables)}", f"PUBLIC_TABLE|{tables[0]}"]
        ),
        "unsafe name": "PUBLIC_TABLE|Billing Users\nINVENTORY_COMPLETE|v1|1\n",
    }
    for label, text in cases.items():
        path.write_text(text, encoding="utf-8")
        with pytest.raises(state.SchemaStateError):
            state.parse_inventory(path)
    # psql blank separator lines are tolerated; nothing else is.
    path.write_text(
        "\n".join(f"PUBLIC_TABLE|{name}" for name in tables)
        + f"\n\nINVENTORY_COMPLETE|v1|{len(tables)}\n",
        encoding="utf-8",
    )
    assert state.parse_inventory(path) == state.TABLE_SETS[4]


def test_manifest_completion_selects_only_accepted_contracts(tmp_path: Path):
    manifest = tmp_path / "manifest.txt"
    for version in (3, 4, 5):
        manifest.write_text(f"DATABASE|18\nMANIFEST_COMPLETE|v{version}|DATABASE|1\n", encoding="utf-8")
        assert state.manifest_contract_version(manifest, state.ALL_VERSIONS) == version
    manifest.write_text("MANIFEST_COMPLETE|v3|DATABASE|1\n", encoding="utf-8")
    with pytest.raises(state.SchemaStateError, match="unaccepted"):
        state.manifest_contract_version(manifest, state.PROVIDER_SOURCE_VERSIONS)
    for text in (
        "MANIFEST_COMPLETE|v2|DATABASE|1\n",
        "DATABASE|18\n",
        "MANIFEST_COMPLETE|v4|DATABASE|1\nMANIFEST_COMPLETE|v5|DATABASE|1\n",
    ):
        manifest.write_text(text, encoding="utf-8")
        with pytest.raises(state.SchemaStateError):
            state.manifest_contract_version(manifest, state.ALL_VERSIONS)


def test_inventory_query_is_read_only_and_privilege_independent():
    sql = _read("deploy/schema_contract_inventory.sql")
    statements = re.sub(r"--[^\n]*", "", sql).upper()
    for forbidden in ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "GRANT", "TRUNCATE"):
        assert forbidden not in statements
    # information_schema hides tables the connected role cannot use.
    assert "information_schema" not in sql
    assert "pg_catalog.pg_class" in sql and "relkind IN ('r', 'p')" in sql
    assert "'INVENTORY_COMPLETE|v1|'" in sql


# Real PostgreSQL 18 proof on the allocated synthetic CI service only.
from test_product_schema_release import client, database, manifest  # noqa: E402,F401
from test_workspace_schema_release import current, migrate  # noqa: E402,F401


def _live_inventory(name: str, path: Path) -> Path:
    path.write_text(
        client(
            name,
            ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", "/probe/deploy/schema_contract_inventory.sql"],
        ).decode(),
        encoding="utf-8",
    )
    return path


def _strict_manifest(name: str, family: str) -> str:
    return client(
        name, ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", f"/probe/deploy/{family}"]
    ).decode()


def _verify(version: int, path: Path) -> None:
    verifier = getattr(state, f"contract_v{version}")
    verifier.verify_current(
        path,
        DEPLOY / "schema_contract_payment_provider_sessions_v1.txt",
        DEPLOY / "schema_contract_billing_email_verifications_v1.txt",
    )


def test_real_core_database_is_v3_and_never_a_provider_source(database, tmp_path):
    from sqlalchemy import text

    engine, name, _ = database
    inventory = _live_inventory(name, tmp_path / "core.txt")
    assert state.detect(inventory, state.ALL_VERSIONS) == 3
    with pytest.raises(state.SchemaStateError, match="not accepted"):
        state.detect(inventory, state.PROVIDER_SOURCE_VERSIONS)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE assistant_daily_budget_v1 (day text PRIMARY KEY)"))
    with pytest.raises(state.SchemaStateError, match="partial"):
        state.detect(_live_inventory(name, tmp_path / "partial.txt"), state.ALL_VERSIONS)


def test_real_product_and_workspace_databases_select_their_own_strict_family(current, tmp_path):
    from sqlalchemy import text

    engine, name, _ = current
    assert state.detect(_live_inventory(name, tmp_path / "v4.txt"), state.PROVIDER_SOURCE_VERSIONS) == 4
    product = tmp_path / "product-v4.txt"
    product.write_text(_strict_manifest(name, "rehearsal_manifest_v4.sql"), encoding="utf-8")
    _verify(4, product)
    # The historical core manifest reports every product table as unexpected.
    core = tmp_path / "product-v3.txt"
    core.write_text(_strict_manifest(name, "rehearsal_manifest_v3.sql"), encoding="utf-8")
    unexpected = {
        line.removeprefix("TABLE_DIFF|unexpected|")
        for line in core.read_text(encoding="utf-8").splitlines()
        if line.startswith("TABLE_DIFF|unexpected|")
    }
    assert unexpected == state.contract_v4.PRODUCT_TABLES
    with pytest.raises(state.contract_v3.ContractError):
        _verify(3, core)

    assert migrate(name, tmp_path / "workspace") == 4
    assert state.detect(_live_inventory(name, tmp_path / "v5.txt"), state.PROVIDER_SOURCE_VERSIONS) == 5
    workspace = tmp_path / "workspace-v5.txt"
    workspace.write_text(_strict_manifest(name, "rehearsal_manifest_v5.sql"), encoding="utf-8")
    _verify(5, workspace)
    recovery = tmp_path / "workspace-recovery-v4.txt"
    recovery.write_text(_strict_manifest(name, "recovery_manifest_v4.sql"), encoding="utf-8")
    _verify(5, recovery)
    stale = tmp_path / "workspace-v4.txt"
    stale.write_text(_strict_manifest(name, "rehearsal_manifest_v4.sql"), encoding="utf-8")
    with pytest.raises(state.contract_v4.ContractError):
        _verify(4, stale)

    with engine.begin() as connection:
        connection.execute(text("DROP TABLE workspace_items_v1"))
    with pytest.raises(state.SchemaStateError, match="partial"):
        state.detect(_live_inventory(name, tmp_path / "partial.txt"), state.ALL_VERSIONS)
    broken = tmp_path / "partial-v5.txt"
    broken.write_text(_strict_manifest(name, "rehearsal_manifest_v5.sql"), encoding="utf-8")
    with pytest.raises(state.contract_v5.ContractError):
        _verify(5, broken)
