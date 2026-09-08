"""Version 4 PostgreSQL contracts for assistant credits and recurring referrals.

Historical v2 verification remains in verify_schema_transition.py. This version
admits only the two separately reviewed additive tables, requires explicit
legacy absence markers, and preserves all existing schema objects and data.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import sys


PREFIX = "SCHEMA_OBJECT|"
TARGET_PREFIXES = (
    "SCHEMA_OBJECT|C|public.billing_payment_provider_sessions|",
    "SCHEMA_OBJECT|I|billing_payment_provider_sessions|",
    "SCHEMA_OBJECT|K|billing_payment_provider_sessions|",
)
PROVIDER_PREFIXES = TARGET_PREFIXES
PURCHASE_TERMS_PREFIXES = (
    "SCHEMA_OBJECT|C|public.billing_purchase_terms|",
    "SCHEMA_OBJECT|I|billing_purchase_terms|",
    "SCHEMA_OBJECT|K|billing_purchase_terms|",
)
TARGET_PREFIXES = PROVIDER_PREFIXES + PURCHASE_TERMS_PREFIXES
PRESERVED_PREFIXES = (
    "SCHEMA_OBJECT|C|public.billing_email_verifications|",
    "SCHEMA_OBJECT|I|billing_email_verifications|",
    "SCHEMA_OBJECT|K|billing_email_verifications|",
)
LEGACY_MARKER = (
    "SCHEMA_COMPAT|legacy_missing_table|billing_payment_provider_sessions|"
    "integrity_checks_deferred_to_current_schema_migration"
)
PURCHASE_TERMS_MARKER = (
    "SCHEMA_COMPAT|legacy_missing_table|billing_purchase_terms|"
    "integrity_checks_deferred_to_current_schema_migration"
)
LEGACY_MARKERS = {
    "billing_payment_provider_sessions": LEGACY_MARKER,
    "billing_purchase_terms": PURCHASE_TERMS_MARKER,
}
MANIFEST_VERSION = "v4"
_UNSIGNED_INTEGER = re.compile(r"0|[1-9][0-9]*")
_SIGNED_INTEGER = re.compile(r"0|-?[1-9][0-9]*")
_MD5 = re.compile(r"[0-9a-f]{32}")
_ENCODING = re.compile(r"[A-Z][A-Z0-9_]*")
_STATUS_TOKEN = re.compile(r"[a-z][a-z0-9_]*")
_STATUS_FAMILIES = frozenset(
    {
        "subscription",
        "manual_order",
        "payment_order",
        "refund",
        "contact",
        "rewarded_ad",
    }
)
MANIFEST_FAMILIES = (
    "DATABASE",
    "DATABASE_SIZE",
    "SCHEMA",
    "SCHEMA_OBJECT",
    "TABLE",
    "TABLE_DIFF",
    "ANOMALY",
    "STATUS",
    "SCHEMA_COMPAT",
    "UNVALIDATED_FK",
)
EXPECTED_TABLES = frozenset(
    {
        "billing_users",
        "billing_user_profiles",
        "billing_user_preferences",
        "billing_auth_tokens",
        "billing_email_verifications",
        "billing_subscriptions",
        "billing_manual_orders",
        "billing_payment_orders",
        "billing_payment_provider_sessions",
        "billing_purchase_terms",
        "billing_payment_consents",
        "billing_usage_events",
        "lecturesift_guest_trials",
        "lecturesift_instagram_rewards",
        "lecturesift_rewarded_ad_claims",
        "lecturesift_email_change_requests",
        "lecturesift_runtime_metrics",
        "lecturesift_admin_credit_events",
        "lecturesift_admin_account_events",
        "lecturesift_account_activity",
        "lecturesift_refund_requests",
        "lecturesift_contact_messages",
        "lecturesift_contact_replies",
        "lecturesift_cost_events",
        "lecturesift_cost_actuals",
    }
)
PRODUCT_TABLES = frozenset(['assistant_credit_grants_v1', 'assistant_credit_requests_v1', 'assistant_daily_budget_v1', 'billing_referral_codes', 'billing_referral_coupons', 'billing_referral_renewal_rewards', 'billing_referral_reward_preferences', 'billing_referral_rewards'])
PRODUCT_ANOMALIES = {table: "invalid_" + table for table in PRODUCT_TABLES}
EXPECTED_TABLES = EXPECTED_TABLES | PRODUCT_TABLES
LEGACY_MARKERS.update({table: "SCHEMA_COMPAT|legacy_missing_table|" + table + "|integrity_checks_deferred_to_current_schema_migration" for table in PRODUCT_TABLES})
TARGET_PREFIXES += tuple(prefix for table in PRODUCT_TABLES for prefix in (f"SCHEMA_OBJECT|C|public.{table}|", f"SCHEMA_OBJECT|I|{table}|", f"SCHEMA_OBJECT|K|{table}|"))

BASE_ANOMALIES = frozenset(
    {
        "negative_user_credit",
        "case_insensitive_duplicate_email",
        "invalid_subscription_period",
        "multiple_active_subscriptions",
        "negative_usage_minutes",
        "negative_manual_amount",
        "manual_timestamp_reversal",
        "negative_payment_amount",
        "payment_timestamp_reversal",
        "paid_provider_amount_mismatch",
        "paid_card_plan_without_subscription",
        "paid_manual_plan_without_subscription",
        "credit_audit_math_error",
        "negative_cost_event",
        "invalid_cost_period",
    }
)
PROVIDER_ANOMALIES = frozenset(
    {
        "invalid_payment_provider_token_digest",
        "payment_provider_session_mismatch",
    }
)


class ContractError(RuntimeError):
    """Raised when a manifest cannot prove the reviewed schema state."""


def _read_lines(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ContractError(f"cannot read {path}: {exc}") from exc
    return [line.rstrip("\r") for line in text.splitlines()]


def _objects(path: Path) -> frozenset[str]:
    records = [line for line in _read_lines(path) if line.startswith(PREFIX)]
    if not records:
        raise ContractError(f"{path} contains no SCHEMA_OBJECT records")
    if len(records) != len(set(records)):
        raise ContractError(f"{path} contains duplicate SCHEMA_OBJECT records")
    return frozenset(records)


def _contract(
    path: Path, *, prefixes: tuple[str, ...], label: str
) -> frozenset[str]:
    records = [
        line
        for line in _read_lines(path)
        if line and not line.lstrip().startswith("#")
    ]
    if not records or any(not line.startswith(PREFIX) for line in records):
        raise ContractError("schema contract must contain only SCHEMA_OBJECT records")
    if len(records) != len(set(records)):
        raise ContractError("schema contract contains duplicate records")
    if any(not line.startswith(prefixes) for line in records):
        raise ContractError(f"{label} schema contract escaped its permitted table")
    return frozenset(records)


def _target_contracts(contract_path: Path) -> dict[str, frozenset[str]]:
    product = _contract(contract_path.with_name("schema_contract_product_v1.txt"), prefixes=tuple(prefix for prefix in TARGET_PREFIXES if any(table in prefix for table in PRODUCT_TABLES)), label="assistant/referral")
    return {
        **{table: _table_objects(product, table) for table in PRODUCT_TABLES},
        "billing_payment_provider_sessions": _contract(
            contract_path, prefixes=PROVIDER_PREFIXES, label="provider-session"
        ),
        "billing_purchase_terms": _contract(
            contract_path.with_name("schema_contract_billing_purchase_terms_v1.txt"),
            prefixes=PURCHASE_TERMS_PREFIXES,
            label="purchase-terms",
        ),
    }


def _table_objects(objects: frozenset[str], table: str) -> frozenset[str]:
    prefixes = (
        f"SCHEMA_OBJECT|C|public.{table}|",
        f"SCHEMA_OBJECT|I|{table}|",
        f"SCHEMA_OBJECT|K|{table}|",
    )
    return frozenset(record for record in objects if record.startswith(prefixes))


def _is_target(record: str) -> bool:
    return record.startswith(TARGET_PREFIXES)


def _is_preserved(record: str) -> bool:
    return record.startswith(PRESERVED_PREFIXES)


def _assert_exact_records(
    objects: frozenset[str],
    expected: frozenset[str],
    *,
    predicate,
    label: str,
) -> None:
    actual = frozenset(record for record in objects if predicate(record))
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ContractError(
            f"{label} schema contract mismatch "
            f"(missing={len(missing)}, extra={len(extra)})"
        )


def _unsigned_integer(value: str) -> bool:
    return _UNSIGNED_INTEGER.fullmatch(value) is not None


def _signed_integer(value: str) -> bool:
    return _SIGNED_INTEGER.fullmatch(value) is not None


def _plain_field(value: str) -> bool:
    return bool(value) and not any(character in value for character in "\x00\r\n|")


def _assert_manifest_integrity(path: Path, *, allow_legacy: bool) -> None:
    lines = _read_lines(path)
    completion = [
        line for line in lines if line.startswith("MANIFEST_COMPLETE|")
    ]
    if len(completion) != 1 or not lines or lines[-1] != completion[0]:
        raise ContractError(f"{path} lacks one terminal manifest completion record")
    unknown = [
        line
        for line in lines[:-1]
        if line
        and not any(line.startswith(f"{family}|") for family in MANIFEST_FAMILIES)
    ]
    if unknown:
        raise ContractError(f"{path} contains an unknown manifest record family")
    parts = completion[0].split("|")
    if (
        len(parts) != 2 + 2 * len(MANIFEST_FAMILIES)
        or parts[:2] != ["MANIFEST_COMPLETE", MANIFEST_VERSION]
        or parts[2::2] != list(MANIFEST_FAMILIES)
    ):
        raise ContractError(f"{path} has an invalid manifest completion contract")
    if any(not _unsigned_integer(value) for value in parts[3::2]):
        raise ContractError(f"{path} has non-canonical manifest counts")
    declared = {
        family: int(value)
        for family, value in zip(parts[2::2], parts[3::2], strict=True)
    }
    actual = {
        family: sum(line.startswith(f"{family}|") for line in lines[:-1])
        for family in MANIFEST_FAMILIES
    }
    if actual != declared:
        raise ContractError(f"{path} manifest completion counts do not match")
    if (
        actual["DATABASE"] != 1
        or actual["DATABASE_SIZE"] != 1
        or actual["SCHEMA"] != 1
        or actual["SCHEMA_OBJECT"] == 0
    ):
        raise ContractError(f"{path} lacks required manifest record families")
    database_parts = next(
        line for line in lines if line.startswith("DATABASE|")
    ).split("|")
    if (
        len(database_parts) != 8
        or database_parts[1] != "18"
        or _ENCODING.fullmatch(database_parts[2]) is None
        or any(not _plain_field(value) for value in database_parts[3:5])
        or database_parts[5] not in {"b", "c", "i"}
        or (database_parts[6] and not _plain_field(database_parts[6]))
        or not _plain_field(database_parts[7])
    ):
        raise ContractError(f"{path} has an invalid database-identity record")

    size_parts = next(
        line for line in lines if line.startswith("DATABASE_SIZE|")
    ).split("|")
    if (
        len(size_parts) != 2
        or not _unsigned_integer(size_parts[1])
        or int(size_parts[1]) <= 0
    ):
        raise ContractError(f"{path} has an invalid database-size record")

    schema_parts = next(
        line for line in lines if line.startswith("SCHEMA|")
    ).split("|")
    objects = [
        line.removeprefix(PREFIX)
        for line in lines
        if line.startswith(PREFIX)
    ]
    if (
        len(schema_parts) != 3
        or not _unsigned_integer(schema_parts[1])
        or int(schema_parts[1]) <= 0
        or int(schema_parts[1]) != len(objects)
        or _MD5.fullmatch(schema_parts[2]) is None
        or schema_parts[2]
        != hashlib.md5(
            "\n".join(sorted(objects)).encode("utf-8"), usedforsecurity=False
        ).hexdigest()
    ):
        raise ContractError(f"{path} schema-object digest/count does not match")

    table_rows = [
        line.split("|") for line in lines if line.startswith("TABLE|")
    ]
    if any(
        len(row) != 5
        or not _plain_field(row[1])
        or not _unsigned_integer(row[2])
        or not _signed_integer(row[3])
        or not _signed_integer(row[4])
        for row in table_rows
    ):
        raise ContractError(f"{path} contains a malformed table fingerprint")
    table_records = [row[1] for row in table_rows]
    if len(table_records) != len(set(table_records)):
        raise ContractError(f"{path} contains duplicate table fingerprints")
    table_names = frozenset(table_records)
    provider_table = "billing_payment_provider_sessions"
    missing_tables = EXPECTED_TABLES - table_names
    if table_names - EXPECTED_TABLES or (
        missing_tables and (
            not allow_legacy or not missing_tables <= LEGACY_MARKERS.keys()
        )
    ):
        raise ContractError(f"{path} does not fingerprint the exact expected tables")
    legacy_missing = provider_table in missing_tables

    anomaly_rows = [
        line.split("|") for line in lines if line.startswith("ANOMALY|")
    ]
    if any(
        len(row) != 3
        or not _plain_field(row[1])
        or not _unsigned_integer(row[2])
        for row in anomaly_rows
    ):
        raise ContractError(f"{path} contains a malformed anomaly result")
    anomaly_records = [row[1] for row in anomaly_rows]
    if len(anomaly_records) != len(set(anomaly_records)):
        raise ContractError(f"{path} contains duplicate anomaly checks")
    expected_anomalies = (
        BASE_ANOMALIES if legacy_missing else BASE_ANOMALIES | PROVIDER_ANOMALIES
    )
    expected_anomalies |= frozenset(PRODUCT_ANOMALIES[table] for table in PRODUCT_TABLES & table_names)
    if frozenset(anomaly_records) != expected_anomalies:
        raise ContractError(f"{path} does not contain the exact anomaly checks")

    status_rows = [
        line.split("|") for line in lines if line.startswith("STATUS|")
    ]
    if any(
        len(row) != 4
        or row[1] not in _STATUS_FAMILIES
        or _STATUS_TOKEN.fullmatch(row[2]) is None
        or not _unsigned_integer(row[3])
        or int(row[3]) <= 0
        for row in status_rows
    ) or len({(row[1], row[2]) for row in status_rows}) != len(status_rows):
        raise ContractError(f"{path} contains a malformed or duplicate status result")

    disallowed = [
        line
        for line in lines
        if line.startswith(("TABLE_DIFF|", "UNVALIDATED_FK|"))
        or (
            line.startswith("ANOMALY|")
            and line.split("|")[2] != "0"
        )
    ]
    compat = [line for line in lines if line.startswith("SCHEMA_COMPAT|")]
    if disallowed:
        raise ContractError(f"{path} contains a schema/integrity failure")
    expected_compat = sorted(LEGACY_MARKERS[table] for table in missing_tables)
    if sorted(compat) != expected_compat:
        raise ContractError(f"{path} compatibility marker/table state differs")
    if not allow_legacy and compat:
        raise ContractError(f"{path} is not a strict current-schema manifest")


def _digest(records: frozenset[str]) -> str:
    payload = ("\n".join(sorted(records)) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_current(
    manifest: Path,
    contract_path: Path,
    preserved_contract_path: Path,
) -> str:
    _assert_manifest_integrity(manifest, allow_legacy=False)
    objects = _objects(manifest)
    contracts = _target_contracts(contract_path)
    expected = frozenset().union(*contracts.values())
    preserved = _contract(
        preserved_contract_path,
        prefixes=PRESERVED_PREFIXES,
        label="preserved email-verification",
    )
    _assert_exact_records(
        objects, expected, predicate=_is_target, label="provider-session"
    )
    _assert_exact_records(
        objects,
        preserved,
        predicate=_is_preserved,
        label="preserved email-verification",
    )
    return _digest(expected)


def verify_legacy(
    manifest: Path,
    contract_path: Path,
    preserved_contract_path: Path,
) -> tuple[str, str]:
    _assert_manifest_integrity(manifest, allow_legacy=True)
    lines = _read_lines(manifest)
    objects = _objects(manifest)
    contracts = _target_contracts(contract_path)
    expected = frozenset().union(*contracts.values())
    preserved = _contract(
        preserved_contract_path,
        prefixes=PRESERVED_PREFIXES,
        label="preserved email-verification",
    )
    _assert_exact_records(
        objects,
        preserved,
        predicate=_is_preserved,
        label="preserved email-verification",
    )
    absent = []
    for table, table_contract in contracts.items():
        actual = _table_objects(objects, table)
        marker_present = LEGACY_MARKERS[table] in lines
        if actual == table_contract and not marker_present:
            continue
        if not actual and marker_present:
            absent.append(table)
            continue
        raise ContractError(f"legacy {table} is neither absent nor contract-current")
    if not absent:
        return "current", _digest(expected)
    return "legacy_missing_release_tables", _digest(expected)


def verify_transition(
    before: Path,
    after: Path,
    contract_path: Path,
    preserved_contract_path: Path,
) -> tuple[str, str]:
    _assert_manifest_integrity(before, allow_legacy=True)
    _assert_manifest_integrity(after, allow_legacy=False)
    before_lines = _read_lines(before)
    before_objects = _objects(before)
    after_objects = _objects(after)
    contracts = _target_contracts(contract_path)
    expected = frozenset().union(*contracts.values())
    preserved = _contract(
        preserved_contract_path,
        prefixes=PRESERVED_PREFIXES,
        label="preserved email-verification",
    )
    _assert_exact_records(
        before_objects,
        preserved,
        predicate=_is_preserved,
        label="source preserved email-verification",
    )
    _assert_exact_records(
        after_objects,
        preserved,
        predicate=_is_preserved,
        label="migrated preserved email-verification",
    )
    before_target = frozenset(record for record in before_objects if _is_target(record))
    after_target = frozenset(record for record in after_objects if _is_target(record))

    if after_target != expected:
        raise ContractError("migrated release tables do not match their exact contracts")
    if (before_objects - before_target) != (after_objects - after_target):
        raise ContractError("migration changed schema objects outside the permitted tables")

    added_tables = set()
    for table, table_contract in contracts.items():
        actual = _table_objects(before_objects, table)
        marker_present = LEGACY_MARKERS[table] in before_lines
        if actual == table_contract and not marker_present:
            continue
        if not actual and marker_present:
            added_tables.add(table)
            continue
        raise ContractError(f"source {table} is neither legacy-absent nor contract-current")

    after_lines = _read_lines(after)
    for table in added_tables:
        if f"TABLE|{table}|0|0|0" not in after_lines:
            raise ContractError(f"newly migrated {table} must be empty")
    data_prefixes = ("DATABASE|", "TABLE|", "STATUS|")
    before_data = {line for line in before_lines if line.startswith(data_prefixes)}
    after_data = {
        line for line in after_lines
        if line.startswith(data_prefixes)
        and not any(line.startswith(f"TABLE|{table}|") for table in added_tables)
    }
    if before_data != after_data:
        raise ContractError("migration changed pre-existing source data")
    transition = "legacy_to_current" if added_tables else "current_to_current"
    return transition, _digest(expected)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    current = subparsers.add_parser("current")
    current.add_argument("--manifest", type=Path, required=True)
    current.add_argument("--contract", type=Path, required=True)
    current.add_argument("--preserved-contract", type=Path, required=True)
    legacy = subparsers.add_parser("legacy")
    legacy.add_argument("--manifest", type=Path, required=True)
    legacy.add_argument("--contract", type=Path, required=True)
    legacy.add_argument("--preserved-contract", type=Path, required=True)
    transition = subparsers.add_parser("transition")
    transition.add_argument("--before", type=Path, required=True)
    transition.add_argument("--after", type=Path, required=True)
    transition.add_argument("--contract", type=Path, required=True)
    transition.add_argument("--preserved-contract", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "current":
            digest = verify_current(
                args.manifest, args.contract, args.preserved_contract
            )
            print(f"schema_contract_sha256={digest}")
            print("schema_contract_state=current")
        elif args.command == "legacy":
            state, digest = verify_legacy(
                args.manifest, args.contract, args.preserved_contract
            )
            print(f"schema_contract_sha256={digest}")
            print(f"schema_contract_state={state}")
        else:
            transition, digest = verify_transition(
                args.before,
                args.after,
                args.contract,
                args.preserved_contract,
            )
            print(f"schema_contract_sha256={digest}")
            print(f"schema_transition={transition}")
    except ContractError as exc:
        print(f"Schema contract verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
