"""Fail-closed PostgreSQL schema-contract verification for provider cutover.

The migration is intentionally allowed to add exactly one reviewed table.  A
full production-schema golden would incorrectly reject preserved legacy tables
that are not owned by current SQLAlchemy metadata, so this verifier instead
binds the new table to an exact PostgreSQL-18 contract and proves every other
catalog object is unchanged from the frozen source manifest.
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
LEGACY_MARKER = (
    "SCHEMA_COMPAT|legacy_missing_table|billing_payment_provider_sessions|"
    "integrity_checks_deferred_to_current_schema_migration"
)
MANIFEST_VERSION = "v2"
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


def _contract(path: Path) -> frozenset[str]:
    records = [
        line
        for line in _read_lines(path)
        if line and not line.lstrip().startswith("#")
    ]
    if not records or any(not line.startswith(PREFIX) for line in records):
        raise ContractError("schema contract must contain only SCHEMA_OBJECT records")
    if len(records) != len(set(records)):
        raise ContractError("schema contract contains duplicate records")
    if any(not _is_target(line) for line in records):
        raise ContractError("schema contract escaped the permitted provider-session table")
    return frozenset(records)


def _is_target(record: str) -> bool:
    return record.startswith(TARGET_PREFIXES)


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
    if table_names == EXPECTED_TABLES:
        legacy_missing = False
    elif allow_legacy and table_names == EXPECTED_TABLES - {provider_table}:
        legacy_missing = True
    else:
        raise ContractError(f"{path} does not fingerprint the exact expected tables")

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
    if allow_legacy:
        if compat not in ([], [LEGACY_MARKER]):
            raise ContractError(f"{path} contains an unapproved compatibility marker")
        if legacy_missing != (compat == [LEGACY_MARKER]):
            raise ContractError(f"{path} compatibility marker/table state differs")
    elif compat:
        raise ContractError(f"{path} is not a strict current-schema manifest")


def _digest(records: frozenset[str]) -> str:
    payload = ("\n".join(sorted(records)) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_current(manifest: Path, contract_path: Path) -> str:
    _assert_manifest_integrity(manifest, allow_legacy=False)
    objects = _objects(manifest)
    expected = _contract(contract_path)
    actual = frozenset(record for record in objects if _is_target(record))
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ContractError(
            "provider-session schema contract mismatch "
            f"(missing={len(missing)}, extra={len(extra)})"
        )
    return _digest(expected)


def verify_legacy(manifest: Path, contract_path: Path) -> tuple[str, str]:
    _assert_manifest_integrity(manifest, allow_legacy=True)
    lines = _read_lines(manifest)
    objects = _objects(manifest)
    expected = _contract(contract_path)
    target = frozenset(record for record in objects if _is_target(record))
    marker_present = LEGACY_MARKER in lines
    if target == expected and not marker_present:
        return "current", _digest(expected)
    if not target and marker_present:
        return "legacy_missing_provider_sessions", _digest(expected)
    raise ContractError(
        "legacy-compatible manifest is neither exact current nor approved legacy"
    )


def verify_transition(before: Path, after: Path, contract_path: Path) -> tuple[str, str]:
    _assert_manifest_integrity(before, allow_legacy=True)
    _assert_manifest_integrity(after, allow_legacy=False)
    before_lines = _read_lines(before)
    before_objects = _objects(before)
    after_objects = _objects(after)
    expected = _contract(contract_path)
    before_target = frozenset(record for record in before_objects if _is_target(record))
    after_target = frozenset(record for record in after_objects if _is_target(record))

    if after_target != expected:
        raise ContractError("migrated provider-session table does not match its exact contract")
    if before_target not in (frozenset(), expected):
        raise ContractError("source provider-session table is neither legacy-absent nor contract-current")
    if (before_objects - before_target) != (after_objects - after_target):
        raise ContractError("migration changed schema objects outside the permitted table")

    marker_present = LEGACY_MARKER in before_lines
    if not before_target and not marker_present:
        raise ContractError("legacy-absent source lacks its explicit compatibility marker")
    if before_target and marker_present:
        raise ContractError("current source incorrectly claims legacy compatibility")
    transition = "legacy_to_current" if not before_target else "current_to_current"
    return transition, _digest(expected)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    current = subparsers.add_parser("current")
    current.add_argument("--manifest", type=Path, required=True)
    current.add_argument("--contract", type=Path, required=True)
    legacy = subparsers.add_parser("legacy")
    legacy.add_argument("--manifest", type=Path, required=True)
    legacy.add_argument("--contract", type=Path, required=True)
    transition = subparsers.add_parser("transition")
    transition.add_argument("--before", type=Path, required=True)
    transition.add_argument("--after", type=Path, required=True)
    transition.add_argument("--contract", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "current":
            digest = verify_current(args.manifest, args.contract)
            print(f"schema_contract_sha256={digest}")
            print("schema_contract_state=current")
        elif args.command == "legacy":
            state, digest = verify_legacy(args.manifest, args.contract)
            print(f"schema_contract_sha256={digest}")
            print(f"schema_contract_state={state}")
        else:
            transition, digest = verify_transition(args.before, args.after, args.contract)
            print(f"schema_contract_sha256={digest}")
            print(f"schema_transition={transition}")
    except ContractError as exc:
        print(f"Schema contract verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
