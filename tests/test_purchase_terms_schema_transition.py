from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
CONTRACT = DEPLOY / "schema_contract_payment_provider_sessions_v1.txt"
PRESERVED = DEPLOY / "schema_contract_billing_email_verifications_v1.txt"
TERMS = DEPLOY / "schema_contract_billing_purchase_terms_v1.txt"
SPEC = importlib.util.spec_from_file_location("schema_v3", DEPLOY / "verify_schema_transition_v3.py")
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def _records(path):
    return [line for line in path.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")]


def _manifest(*, missing=(), changed_terms=False, nonempty=(), extra=(), markers=None):
    missing = set(missing)
    objects = _records(PRESERVED)
    for table, path in (("billing_payment_provider_sessions", CONTRACT),
                        ("billing_purchase_terms", TERMS)):
        if table not in missing:
            objects += _records(path)
    if changed_terms:
        objects = [line.replace("|plan_json|text|", "|plan_json|character varying(64)|")
                   for line in objects]
    objects = sorted([*objects, *extra])
    digest = hashlib.md5(
        "\n".join(line.removeprefix(verifier.PREFIX) for line in objects).encode(),
        usedforsecurity=False,
    ).hexdigest()
    lines = [
        "DATABASE|18|UTF8|en_US.UTF8|en_US.UTF8|c|2.36|UTC",
        "DATABASE_SIZE|1",
        f"SCHEMA|{len(objects)}|{digest}",
        *objects,
        *(f"TABLE|{name}|" + ("1|123|123" if name in nonempty else "0|0|0")
          for name in sorted(verifier.EXPECTED_TABLES - missing)),
        *(f"ANOMALY|{name}|0" for name in sorted(
            verifier.BASE_ANOMALIES | (
                frozenset() if "billing_payment_provider_sessions" in missing
                else verifier.PROVIDER_ANOMALIES))),
        *(markers if markers is not None else
          [verifier.LEGACY_MARKERS[name] for name in sorted(missing)]),
    ]
    counts = {family: sum(line.startswith(f"{family}|") for line in lines)
              for family in verifier.MANIFEST_FAMILIES}
    lines.append("MANIFEST_COMPLETE|v3|" + "|".join(
        f"{family}|{counts[family]}" for family in verifier.MANIFEST_FAMILIES))
    return "\n".join(lines) + "\n"


def _write(tmp_path, name, **kwargs):
    path = tmp_path / name
    path.write_text(_manifest(**kwargs), encoding="utf-8")
    return path


@pytest.mark.parametrize("missing", [
    (), ("billing_purchase_terms",), ("billing_payment_provider_sessions",),
    ("billing_purchase_terms", "billing_payment_provider_sessions"),
])
def test_v3_accepts_only_explicit_reviewed_additions(tmp_path, missing):
    before = _write(tmp_path, "before.txt", missing=missing)
    after = _write(tmp_path, "after.txt")
    state, _ = verifier.verify_legacy(before, CONTRACT, PRESERVED)
    assert state == ("legacy_missing_release_tables" if missing else "current")
    transition, _ = verifier.verify_transition(before, after, CONTRACT, PRESERVED)
    assert transition == ("legacy_to_current" if missing else "current_to_current")
    verifier.verify_current(after, CONTRACT, PRESERVED)
    if missing:
        with pytest.raises(verifier.ContractError):
            verifier.verify_current(before, CONTRACT, PRESERVED)


@pytest.mark.parametrize("missing,markers", [
    (("billing_purchase_terms",), []),
    ((), [verifier.PURCHASE_TERMS_MARKER]),
    (("billing_purchase_terms",), [verifier.PURCHASE_TERMS_MARKER] * 2),
    (("billing_users",), ["SCHEMA_COMPAT|legacy_missing_table|billing_users|forged"]),
])
def test_v3_rejects_missing_forged_or_duplicate_legacy_permission(tmp_path, missing, markers):
    path = _write(tmp_path, "invalid.txt", missing=missing, markers=markers)
    with pytest.raises(verifier.ContractError):
        verifier.verify_legacy(path, CONTRACT, PRESERVED)


def test_v3_new_terms_must_be_empty_and_existing_data_preserved(tmp_path):
    before = _write(tmp_path, "before.txt", missing=("billing_purchase_terms",))
    after = _write(tmp_path, "after.txt", nonempty=("billing_purchase_terms",))
    with pytest.raises(verifier.ContractError, match="must be empty"):
        verifier.verify_transition(before, after, CONTRACT, PRESERVED)
    after = _write(tmp_path, "after.txt", nonempty=("billing_users",))
    with pytest.raises(verifier.ContractError, match="pre-existing source data"):
        verifier.verify_transition(before, after, CONTRACT, PRESERVED)
    current = _write(tmp_path, "current.txt", nonempty=("billing_purchase_terms",))
    assert verifier.verify_transition(current, current, CONTRACT, PRESERVED)[0] == "current_to_current"


def test_v3_rejects_changed_terms_schema_and_unrelated_schema_migration(tmp_path):
    bad = _write(tmp_path, "changed.txt", changed_terms=True)
    with pytest.raises(verifier.ContractError):
        verifier.verify_current(bad, CONTRACT, PRESERVED)
    before = _write(tmp_path, "before.txt")
    after = _write(tmp_path, "after.txt", extra=(
        "SCHEMA_OBJECT|C|public.billing_users|99|unreviewed|text|f|||",
    ))
    with pytest.raises(verifier.ContractError, match="outside the permitted tables"):
        verifier.verify_transition(before, after, CONTRACT, PRESERVED)


@pytest.mark.parametrize("replacement", [
    ("MANIFEST_COMPLETE|v3|", "MANIFEST_COMPLETE|v2|"),
    ("|TABLE|25|", "|TABLE|24|"),
    ("TABLE|billing_purchase_terms|0|0|0", ""),
])
def test_v3_rejects_truncated_or_wrong_version_evidence(tmp_path, replacement):
    path = tmp_path / "bad.txt"
    path.write_text(_manifest().replace(*replacement), encoding="utf-8")
    with pytest.raises(verifier.ContractError):
        verifier.verify_current(path, CONTRACT, PRESERVED)


def test_current_recovery_and_cutover_routing_preserves_historical_files():
    current_sql = (DEPLOY / "rehearsal_manifest_v3.sql").read_text(encoding="utf-8")
    assert "MANIFEST_COMPLETE|v3" in current_sql
    assert r"\set LECTURESIFT_ALLOW_LEGACY_PURCHASE_TERMS off" in current_sql
    assert current_sql.count("('billing_purchase_terms')") == 2
    assert "('billing_purchase_terms')" in (DEPLOY / "recovery_manifest_v2.sql").read_text(encoding="utf-8")
    for name in ("migrate_postgres.sh", "rehearsal_restore.sh", "rollback_postgres_to_render.sh",
                 "finalize_provider_cutover.sh", "verify_provider_first_start.sh",
                 "run_exact_rehearsal.sh", "seed_first_cutover_backup.sh"):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        assert "rehearsal_manifest_v3.sql" in text
        assert "verify_schema_transition_v3.py" in text
    for name in ("backup.sh", "seed_first_cutover_backup.sh"):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        assert "RECOVERY_MANIFEST_VERSION=2" in text
        assert "application_schema_compatibility=lecturesift-schema-v2" in text
    for name in ("restore.sh", "restic_restore_rehearsal.sh"):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        for version in (1, 2):
            assert f"recovery_manifest_v{version}.sql" in text
            assert f'expected_schema_compatibility="lecturesift-schema-v{version}"' in text
        assert '"$metadata_schema_compatibility"' in text
        assert '"$expected_schema_compatibility"' in text
