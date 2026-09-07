#!/usr/bin/env python3
"""Validate a fail-closed provider rollback reconciliation evidence bundle.

This tool deliberately performs no Redis, R2, payment, DNS, webhook, or
database mutation.  It validates the shape, cross-binding, freshness and
internal consistency of evidence produced by separately reviewed procedures.
It cannot prove that an operator's underlying provider exports are authentic;
those root-private artifacts still require human review.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Final


ALLOWED_BUNDLE_ROOT: Final = Path(
    "/var/lib/lecturesift/provider-rollback-evidence"
)
ALLOWED_POSTGRES_ROOT: Final = Path(
    "/var/backups/lecturesift/postgres-rollback"
)
ALLOWED_CUTOVER_ROOT: Final = Path("/var/lib/lecturesift/provider-cutover")
RUNTIME_ENV: Final = Path("/etc/lecturesift/runtime.env")
EXPECTED_FILES: Final = frozenset(
    {"context.json", "redis.json", "r2.json", "payments.json"}
)
MAX_JSON_BYTES: Final = 128 * 1024
MAX_MARKER_BYTES: Final = 16 * 1024
MAX_RUNTIME_BYTES: Final = 512 * 1024
MAX_PROOF_LIFETIME: Final = timedelta(minutes=30)
MAX_FREEZE_WINDOW: Final = timedelta(hours=24)
HEX32: Final = re.compile(r"[0-9a-f]{32}")
HEX40: Final = re.compile(r"[0-9a-f]{40}")
HEX64: Final = re.compile(r"[0-9a-f]{64}")
UTC_TIMESTAMP: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
ENV_ASSIGNMENT: Final = re.compile(
    r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$"
)
PROVIDER_KEYS: Final = {
    "iyzico": ("IYZICO_API_KEY", "IYZICO_SECRET_KEY"),
    "paytr": ("PAYTR_MERCHANT_ID", "PAYTR_MERCHANT_KEY", "PAYTR_MERCHANT_SALT"),
}

COMMON_FIELDS: Final = frozenset(
    {
        "rollback_id",
        "cutover_id",
        "ovh_release_revision",
        "render_release_revision",
        "reconciled_database_manifest_sha256",
        "ovh_scheduler_stop_evidence_sha256",
        "render_scheduler_stop_evidence_sha256",
        "ovh_instagram_stop_evidence_sha256",
        "render_instagram_stop_evidence_sha256",
        "started_at_utc",
        "completed_at_utc",
    }
)
CONTEXT_FIELDS: Final = frozenset(
    {
        "schema",
        "rollback_id",
        "cutover_id",
        "ovh_release_revision",
        "render_release_revision",
        "freeze_started_at_utc",
        "evidence_closed_at_utc",
        "valid_until_utc",
        "postgres_marker_sha256",
        "redis_evidence_sha256",
        "r2_evidence_sha256",
        "payments_evidence_sha256",
        "source_worker_stop_evidence_sha256",
        "reconciled_database_manifest_sha256",
        "expected_configured_providers",
        "ovh_api_mode",
        "render_api_mode",
        "ovh_worker_stopped",
        "render_worker_stopped",
        "ovh_queue_empty",
        "render_queue_empty",
        "ovh_scheduler_stopped",
        "render_scheduler_stopped",
        "ovh_instagram_publisher_stopped",
        "render_instagram_publisher_stopped",
        "ovh_scheduler_stop_evidence_sha256",
        "render_scheduler_stop_evidence_sha256",
        "ovh_instagram_stop_evidence_sha256",
        "render_instagram_stop_evidence_sha256",
        "new_checkout_creation_blocked",
        "user_traffic_changed",
        "provider_callback_traffic_changed",
    }
)
REDIS_FIELDS: Final = COMMON_FIELDS | frozenset(
    {
        "schema",
        "source_system",
        "target_system",
        "logical_key",
        "strategy",
        "payload_schema_version",
        "source_before_existed",
        "source_after_existed",
        "target_before_existed",
        "target_after_existed",
        "source_before_sha256",
        "source_after_sha256",
        "target_before_sha256",
        "target_after_sha256",
        "target_rollback_copy_sha256",
        "target_non_job_before_sha256",
        "target_non_job_after_sha256",
        "source_job_count",
        "target_job_count",
        "source_active_job_count",
        "target_active_job_count",
        "source_processing_lock_count",
        "target_processing_lock_count",
        "source_worker_stop_evidence_sha256",
        "durability_acknowledged",
        "target_lock_token_checked",
        "unsafe_merge_attempted",
    }
)
R2_FIELDS: Final = COMMON_FIELDS | frozenset(
    {
        "schema",
        "strategy",
        "shared_authoritative_bucket",
        "endpoint_binding_sha256",
        "bucket_binding_sha256",
        "pre_cutover_inventory_sha256",
        "freeze_before_inventory_sha256",
        "freeze_after_inventory_sha256",
        "database_reference_manifest_sha256",
        "reference_verification_sha256",
        "freeze_before_object_count",
        "freeze_after_object_count",
        "freeze_before_total_bytes",
        "freeze_after_total_bytes",
        "database_reference_count",
        "verified_reference_count",
        "added_since_cutover_count",
        "modified_since_cutover_count",
        "deleted_since_cutover_count",
        "retained_added_object_count",
        "retained_modified_object_count",
        "unresolved_database_reference_count",
        "unresolved_deleted_reference_count",
        "objects_copied",
        "objects_deleted",
        "production_objects_mutated",
        "version_retention_proven",
        "credential_probe_write_read_delete_passed",
        "probe_namespace_only",
        "all_database_references_resolve",
    }
)
PAYMENTS_FIELDS: Final = COMMON_FIELDS | frozenset(
    {
        "schema",
        "strategy",
        "configured_providers",
        "provider_results",
        "manual_pending_orders",
        "async_bank_transfers_pending",
        "new_checkout_creation_blocked",
        "provider_callback_switch_complete",
        "callbacks_accepting_system",
        "provider_reconciliation_complete",
    }
)
PROVIDER_FIELDS: Final = frozenset(
    {
        "provider",
        "callback_url_sha256",
        "provider_export_sha256",
        "local_order_manifest_before_sha256",
        "local_order_manifest_after_sha256",
        "callback_audit_sha256",
        "events_examined",
        "events_matched",
        "duplicate_events_replayed",
        "unmatched_provider_events",
        "unmatched_local_orders",
        "pending_provider_payments",
        "pending_local_payments",
        "amount_or_currency_mismatches",
        "unaccepted_signature_failures",
        "positive_signed_callback_passed",
        "invalid_signature_rejected",
        "idempotent_replay_passed",
        "callback_target",
        "reconciled_database_manifest_sha256",
    }
)
PROVIDER_BASELINE_FIELDS: Final = frozenset(
    {
        "schema",
        "cutover_id",
        "release_revision",
        "provider_cutover_proof_sha256",
        "configuration_presence_sha256",
        "configured_providers",
        "recorded_at_utc",
    }
)


class RollbackEvidenceError(RuntimeError):
    """Raised when rollback readiness evidence is unsafe or inconsistent."""


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _plain_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _require_fields(document: dict[str, Any], expected: frozenset[str], label: str) -> None:
    if set(document) != expected:
        missing = sorted(expected - set(document))
        extra = sorted(set(document) - expected)
        raise RollbackEvidenceError(
            f"{label} fields do not match the exact contract "
            f"(missing={missing}, extra={extra})"
        )


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RollbackEvidenceError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return tuple(
        int(getattr(value, field))
        for field in (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_uid",
            "st_gid",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
    )


def _read_file(path: Path, *, maximum: int, enforce_filesystem: bool) -> bytes:
    if not path.is_absolute() and enforce_filesystem:
        raise RollbackEvidenceError(f"evidence path is not absolute: {path}")
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise RollbackEvidenceError(f"evidence file is missing: {path.name}") from exc
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise RollbackEvidenceError(f"evidence file is not a regular file: {path.name}")
    if before.st_size <= 0 or before.st_size > maximum:
        raise RollbackEvidenceError(f"evidence file size is unsafe: {path.name}")
    if enforce_filesystem and (
        before.st_uid != 0
        or before.st_gid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
    ):
        raise RollbackEvidenceError(
            f"evidence file must be root:root 0600 with one link: {path.name}"
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RollbackEvidenceError(f"evidence file could not be opened safely: {path.name}") from exc
    try:
        opened = os.fstat(descriptor)
        if enforce_filesystem and _file_identity(opened) != _file_identity(before):
            raise RollbackEvidenceError(f"evidence file changed while opening: {path.name}")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if enforce_filesystem and _file_identity(after) != _file_identity(opened):
            raise RollbackEvidenceError(f"evidence file changed while reading: {path.name}")
    finally:
        os.close(descriptor)
    if not payload or len(payload) > maximum:
        raise RollbackEvidenceError(f"evidence file size is unsafe: {path.name}")
    return bytes(payload)


def _load_json(path: Path, *, enforce_filesystem: bool) -> tuple[dict[str, Any], bytes]:
    payload = _read_file(path, maximum=MAX_JSON_BYTES, enforce_filesystem=enforce_filesystem)
    try:
        document = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(
                RollbackEvidenceError(f"invalid JSON constant: {value}")
            ),
        )
    except RollbackEvidenceError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RollbackEvidenceError(f"invalid JSON evidence: {path.name}") from exc
    if not isinstance(document, dict):
        raise RollbackEvidenceError(f"JSON evidence must be an object: {path.name}")
    return document, payload


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not UTC_TIMESTAMP.fullmatch(value):
        raise RollbackEvidenceError(f"{label} must be an exact UTC second timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise RollbackEvidenceError(f"{label} is not a valid timestamp") from exc
    return parsed


def _hex(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise RollbackEvidenceError(f"{label} has an invalid digest or identifier")
    return value


def _true(document: dict[str, Any], field: str, label: str) -> None:
    if document.get(field) is not True:
        raise RollbackEvidenceError(f"{label}.{field} must be exactly true")


def _false(document: dict[str, Any], field: str, label: str) -> None:
    if document.get(field) is not False:
        raise RollbackEvidenceError(f"{label}.{field} must be exactly false")


def _runtime_provider_census(payload: bytes) -> tuple[tuple[str, ...], str]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise RollbackEvidenceError("runtime environment is not UTF-8") from exc
    if "\x00" in text:
        raise RollbackEvidenceError("runtime environment contains a NUL byte")
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = ENV_ASSIGNMENT.fullmatch(line)
        if not match:
            raise RollbackEvidenceError(f"unsupported runtime syntax at line {number}")
        key, raw = match.groups()
        if key in values:
            raise RollbackEvidenceError(f"duplicate runtime key: {key}")
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value

    providers: list[str] = []
    presence: dict[str, bool] = {}
    for provider, keys in sorted(PROVIDER_KEYS.items()):
        flags = [bool(values.get(key, "")) for key in keys]
        presence.update({key: present for key, present in zip(keys, flags)})
        if any(flags) and not all(flags):
            raise RollbackEvidenceError(f"current {provider} credential set is partial")
        if all(flags):
            providers.append(provider)
    canonical = json.dumps(presence, sort_keys=True, separators=(",", ":")).encode("ascii")
    return tuple(providers), _digest(canonical)


def _marker_historical_providers(value: str) -> tuple[str, ...]:
    if value == "none":
        return ()
    providers = tuple(value.split(","))
    if (
        not providers
        or tuple(sorted(set(providers))) != providers
        or any(provider not in {"iyzico", "paytr"} for provider in providers)
    ):
        raise RollbackEvidenceError("PostgreSQL historical provider census is invalid")
    return providers


def _zero(document: dict[str, Any], field: str, label: str) -> None:
    if document.get(field) != 0 or type(document.get(field)) is not int:
        raise RollbackEvidenceError(f"{label}.{field} must be exactly zero")


def _common(
    document: dict[str, Any],
    *,
    label: str,
    context: dict[str, Any],
    freeze_started: datetime,
    evidence_closed: datetime,
) -> None:
    for field in (
        "rollback_id",
        "cutover_id",
        "ovh_release_revision",
        "render_release_revision",
        "reconciled_database_manifest_sha256",
        "ovh_scheduler_stop_evidence_sha256",
        "render_scheduler_stop_evidence_sha256",
        "ovh_instagram_stop_evidence_sha256",
        "render_instagram_stop_evidence_sha256",
    ):
        if document.get(field) != context[field]:
            raise RollbackEvidenceError(f"{label}.{field} does not match context")
    started = _timestamp(document.get("started_at_utc"), f"{label}.started_at_utc")
    completed = _timestamp(document.get("completed_at_utc"), f"{label}.completed_at_utc")
    if not (freeze_started <= started <= completed <= evidence_closed):
        raise RollbackEvidenceError(f"{label} falls outside the frozen evidence window")


def _parse_postgres_marker(payload: bytes) -> dict[str, str]:
    try:
        text = payload.decode("ascii", errors="strict")
    except UnicodeError as exc:
        raise RollbackEvidenceError("PostgreSQL marker is not ASCII") from exc
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line or "=" not in line:
            raise RollbackEvidenceError("PostgreSQL marker contains a malformed line")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or key in result or not value:
            raise RollbackEvidenceError("PostgreSQL marker contains an unsafe field")
        result[key] = value
    status_value = result.get("status")
    if status_value not in {
        "postgres-reconciliation-not-required",
        "postgres-rollback-reconciled",
    }:
        raise RollbackEvidenceError("PostgreSQL reconciliation is not verified")
    common_fields = {
        "status",
        "verified_at_utc",
        "rollback_id",
        "cutover_id",
        "ovh_release_revision",
        "render_release_revision",
        "reconciled_database_manifest_sha256",
        "historical_payment_providers",
        "historical_provider_manifest_sha256",
        "source_worker_stop_evidence_sha256",
        "traffic_changed",
    }
    status_fields = (
        {"both_databases_identical"}
        if status_value == "postgres-reconciliation-not-required"
        else {
            "render_matches_ovh",
            "automatic_row_merge",
            "whole_database_replacement",
            "replacement_scope",
            "approved_app_schemas_replaced",
            "target_database_policy_preserved",
            "target_app_acl_policy",
            "redis_reconciliation_complete",
            "r2_reconciliation_complete",
        }
    )
    if set(result) != common_fields | status_fields:
        raise RollbackEvidenceError("PostgreSQL marker fields do not match the exact contract")
    _hex(result.get("rollback_id"), HEX32, "PostgreSQL rollback id")
    _hex(result.get("cutover_id"), HEX32, "PostgreSQL cutover id")
    _hex(result.get("ovh_release_revision"), HEX40, "PostgreSQL OVH revision")
    _hex(result.get("render_release_revision"), HEX40, "PostgreSQL Render revision")
    _hex(
        result.get("reconciled_database_manifest_sha256"),
        HEX64,
        "PostgreSQL reconciled database manifest",
    )
    _hex(
        result.get("historical_provider_manifest_sha256"),
        HEX64,
        "PostgreSQL historical provider manifest",
    )
    _marker_historical_providers(result.get("historical_payment_providers", ""))
    if result.get("traffic_changed") != "false":
        raise RollbackEvidenceError("PostgreSQL marker must prove traffic was unchanged")
    _hex(result.get("source_worker_stop_evidence_sha256"), HEX64, "PostgreSQL worker-stop evidence")
    _timestamp(result.get("verified_at_utc"), "PostgreSQL verified_at_utc")
    if status_value == "postgres-reconciliation-not-required":
        if result.get("both_databases_identical") != "true":
            raise RollbackEvidenceError("PostgreSQL no-op marker does not prove equality")
    else:
        required = {
            "render_matches_ovh": "true",
            "automatic_row_merge": "false",
            "whole_database_replacement": "false",
            "approved_app_schemas_replaced": "true",
            "target_database_policy_preserved": "true",
            "redis_reconciliation_complete": "false",
            "r2_reconciliation_complete": "false",
        }
        for field, expected in required.items():
            if result.get(field) != expected:
                raise RollbackEvidenceError(
                    f"PostgreSQL marker field {field} is not {expected}"
                )
        if result.get("replacement_scope") != "public-and-lecturesift-worker-schemas":
            raise RollbackEvidenceError("PostgreSQL replacement scope is unexpected")
        if result.get("target_app_acl_policy") != "database-owner-only":
            raise RollbackEvidenceError("PostgreSQL target ACL policy is unexpected")
    return result


def _parse_cutover_proof(payload: bytes) -> dict[str, str]:
    try:
        text = payload.decode("ascii", errors="strict")
    except UnicodeError as exc:
        raise RollbackEvidenceError("provider cutover proof is not ASCII") from exc
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line or "=" not in line:
            raise RollbackEvidenceError("provider cutover proof contains a malformed line")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or key in result or not value:
            raise RollbackEvidenceError("provider cutover proof contains an unsafe field")
        result[key] = value
    if (
        result.get("version") != "3"
        or result.get("status") != "provider-cutover-verified"
        or not HEX32.fullmatch(result.get("cutover_id", ""))
        or not HEX40.fullmatch(result.get("release_revision", ""))
    ):
        raise RollbackEvidenceError("provider cutover proof identity is invalid")
    return result


def _validate_provider_baseline(
    baseline: dict[str, Any],
    baseline_payload: bytes,
    cutover_proof_payload: bytes,
    *,
    expected_cutover_id: str,
    expected_ovh_revision: str,
    now: datetime,
) -> tuple[str, ...]:
    _require_fields(baseline, PROVIDER_BASELINE_FIELDS, "provider baseline")
    if baseline.get("schema") != "lecturesift-payment-provider-baseline-v1":
        raise RollbackEvidenceError("provider baseline schema is invalid")
    proof = _parse_cutover_proof(cutover_proof_payload)
    if (
        baseline.get("cutover_id") != expected_cutover_id
        or baseline.get("cutover_id") != proof.get("cutover_id")
        or baseline.get("release_revision") != expected_ovh_revision
        or baseline.get("release_revision") != proof.get("release_revision")
    ):
        raise RollbackEvidenceError("provider baseline is not bound to the original cutover")
    if baseline.get("provider_cutover_proof_sha256") != _digest(cutover_proof_payload):
        raise RollbackEvidenceError("provider baseline does not bind the exact cutover proof")
    _hex(
        baseline.get("configuration_presence_sha256"),
        HEX64,
        "provider baseline configuration presence digest",
    )
    providers = baseline.get("configured_providers")
    if (
        not isinstance(providers, list)
        or not providers
        or providers != sorted(set(providers))
        or any(provider not in {"iyzico", "paytr"} for provider in providers)
    ):
        raise RollbackEvidenceError("provider baseline configured provider set is invalid")
    recorded = _timestamp(baseline.get("recorded_at_utc"), "provider baseline recorded_at_utc")
    if recorded > now + timedelta(minutes=5):
        raise RollbackEvidenceError("provider baseline was recorded in the future")
    if not baseline_payload.endswith(b"\n"):
        raise RollbackEvidenceError("provider baseline is not canonically terminated")
    return tuple(providers)


def _validate_context(
    context: dict[str, Any],
    *,
    expected_rollback_id: str,
    expected_cutover_id: str,
    expected_ovh_revision: str,
    expected_render_revision: str,
    expected_providers: tuple[str, ...],
    now: datetime,
) -> tuple[datetime, datetime, datetime]:
    _require_fields(context, CONTEXT_FIELDS, "context")
    if context.get("schema") != "lecturesift-provider-rollback-context-v1":
        raise RollbackEvidenceError("context schema is unsupported")
    expected = {
        "rollback_id": expected_rollback_id,
        "cutover_id": expected_cutover_id,
        "ovh_release_revision": expected_ovh_revision,
        "render_release_revision": expected_render_revision,
    }
    for field, value in expected.items():
        if context.get(field) != value:
            raise RollbackEvidenceError(f"context.{field} does not match the operator expectation")
    providers = context.get("expected_configured_providers")
    if providers != list(expected_providers):
        raise RollbackEvidenceError(
            "context expected configured providers do not match the trusted provider census"
        )
    freeze_started = _timestamp(context.get("freeze_started_at_utc"), "context.freeze_started_at_utc")
    evidence_closed = _timestamp(context.get("evidence_closed_at_utc"), "context.evidence_closed_at_utc")
    valid_until = _timestamp(context.get("valid_until_utc"), "context.valid_until_utc")
    if not freeze_started <= evidence_closed < valid_until:
        raise RollbackEvidenceError("context evidence timeline is invalid")
    if evidence_closed - freeze_started > MAX_FREEZE_WINDOW:
        raise RollbackEvidenceError("rollback evidence window exceeds 24 hours")
    if valid_until - evidence_closed > MAX_PROOF_LIFETIME:
        raise RollbackEvidenceError("rollback readiness proof lifetime exceeds 30 minutes")
    if now.tzinfo is None:
        raise RollbackEvidenceError("validation time must be timezone-aware")
    now = now.astimezone(timezone.utc)
    if not evidence_closed <= now <= valid_until:
        raise RollbackEvidenceError("rollback readiness evidence is not current")
    for field in (
        "postgres_marker_sha256",
        "redis_evidence_sha256",
        "r2_evidence_sha256",
        "payments_evidence_sha256",
        "source_worker_stop_evidence_sha256",
        "reconciled_database_manifest_sha256",
        "ovh_scheduler_stop_evidence_sha256",
        "render_scheduler_stop_evidence_sha256",
        "ovh_instagram_stop_evidence_sha256",
        "render_instagram_stop_evidence_sha256",
    ):
        _hex(context.get(field), HEX64, f"context.{field}")
    if context.get("ovh_api_mode") != "freeze" or context.get("render_api_mode") != "drain":
        raise RollbackEvidenceError(
            "final readiness requires OVH freeze and Render callback-only drain"
        )
    for field in (
        "ovh_worker_stopped",
        "render_worker_stopped",
        "ovh_queue_empty",
        "render_queue_empty",
        "ovh_scheduler_stopped",
        "render_scheduler_stopped",
        "ovh_instagram_publisher_stopped",
        "render_instagram_publisher_stopped",
        "new_checkout_creation_blocked",
        "provider_callback_traffic_changed",
    ):
        _true(context, field, "context")
    _false(context, "user_traffic_changed", "context")
    return freeze_started, evidence_closed, valid_until


def _validate_redis(
    document: dict[str, Any],
    *,
    context: dict[str, Any],
    freeze_started: datetime,
    evidence_closed: datetime,
) -> None:
    _require_fields(document, REDIS_FIELDS, "redis")
    if document.get("schema") != "lecturesift-provider-rollback-redis-v1":
        raise RollbackEvidenceError("redis schema is unsupported")
    _common(
        document,
        label="redis",
        context=context,
        freeze_started=freeze_started,
        evidence_closed=evidence_closed,
    )
    if (
        document.get("source_system") != "ovh"
        or document.get("target_system") != "render"
        or document.get("logical_key") != "lecturesift:jobs:v2"
        or document.get("strategy") != "replace-single-versioned-json-key"
        or document.get("payload_schema_version") != 2
    ):
        raise RollbackEvidenceError("redis migration identity or strategy is unsafe")
    for field in (
        "source_before_sha256",
        "source_after_sha256",
        "target_before_sha256",
        "target_after_sha256",
        "target_rollback_copy_sha256",
        "target_non_job_before_sha256",
        "target_non_job_after_sha256",
        "source_worker_stop_evidence_sha256",
    ):
        _hex(document.get(field), HEX64, f"redis.{field}")
    if document["source_worker_stop_evidence_sha256"] != context["source_worker_stop_evidence_sha256"]:
        raise RollbackEvidenceError("redis worker-stop evidence does not match context")
    if document.get("source_before_existed") is not document.get("source_after_existed"):
        raise RollbackEvidenceError("redis source key presence changed during reconciliation")
    if document.get("target_after_existed") is not True:
        raise RollbackEvidenceError("redis target logical key was not installed")
    if document["source_before_sha256"] != document["source_after_sha256"]:
        raise RollbackEvidenceError("redis source payload changed during reconciliation")
    if document["source_after_sha256"] != document["target_after_sha256"]:
        raise RollbackEvidenceError("redis target payload does not match frozen source")
    if document["target_before_sha256"] != document["target_rollback_copy_sha256"]:
        raise RollbackEvidenceError("redis target rollback copy is not exact")
    if document["target_non_job_before_sha256"] != document["target_non_job_after_sha256"]:
        raise RollbackEvidenceError("redis non-job state changed")
    for field in ("source_before_existed", "source_after_existed", "target_before_existed"):
        if type(document.get(field)) is not bool:
            raise RollbackEvidenceError(f"redis.{field} must be a boolean")
    for field in ("source_job_count", "target_job_count"):
        if not _plain_int(document.get(field)):
            raise RollbackEvidenceError(f"redis.{field} must be a non-negative integer")
    if document["source_job_count"] != document["target_job_count"]:
        raise RollbackEvidenceError("redis job counts do not match")
    for field in (
        "source_active_job_count",
        "target_active_job_count",
        "source_processing_lock_count",
        "target_processing_lock_count",
    ):
        _zero(document, field, "redis")
    _true(document, "durability_acknowledged", "redis")
    _true(document, "target_lock_token_checked", "redis")
    _false(document, "unsafe_merge_attempted", "redis")


def _validate_r2(
    document: dict[str, Any],
    *,
    context: dict[str, Any],
    freeze_started: datetime,
    evidence_closed: datetime,
) -> None:
    _require_fields(document, R2_FIELDS, "r2")
    if document.get("schema") != "lecturesift-provider-rollback-r2-v1":
        raise RollbackEvidenceError("r2 schema is unsupported")
    _common(
        document,
        label="r2",
        context=context,
        freeze_started=freeze_started,
        evidence_closed=evidence_closed,
    )
    if document.get("strategy") != "shared-authoritative-bucket-retain-no-delete":
        raise RollbackEvidenceError("r2 strategy is unsafe")
    for field in (
        "endpoint_binding_sha256",
        "bucket_binding_sha256",
        "pre_cutover_inventory_sha256",
        "freeze_before_inventory_sha256",
        "freeze_after_inventory_sha256",
        "database_reference_manifest_sha256",
        "reference_verification_sha256",
    ):
        _hex(document.get(field), HEX64, f"r2.{field}")
    if document["freeze_before_inventory_sha256"] != document["freeze_after_inventory_sha256"]:
        raise RollbackEvidenceError("r2 inventory changed while writers were frozen")
    count_fields = (
        "freeze_before_object_count",
        "freeze_after_object_count",
        "freeze_before_total_bytes",
        "freeze_after_total_bytes",
        "database_reference_count",
        "verified_reference_count",
        "added_since_cutover_count",
        "modified_since_cutover_count",
        "deleted_since_cutover_count",
        "retained_added_object_count",
        "retained_modified_object_count",
        "unresolved_database_reference_count",
        "unresolved_deleted_reference_count",
        "objects_copied",
        "objects_deleted",
    )
    for field in count_fields:
        if not _plain_int(document.get(field)):
            raise RollbackEvidenceError(f"r2.{field} must be a non-negative integer")
    if (
        document["freeze_before_object_count"] != document["freeze_after_object_count"]
        or document["freeze_before_total_bytes"] != document["freeze_after_total_bytes"]
    ):
        raise RollbackEvidenceError("r2 counts changed while writers were frozen")
    if document["database_reference_count"] != document["verified_reference_count"]:
        raise RollbackEvidenceError("r2 database references were not all verified")
    if document["added_since_cutover_count"] != document["retained_added_object_count"]:
        raise RollbackEvidenceError("r2 post-cutover objects were not all retained")
    if document["modified_since_cutover_count"] != document["retained_modified_object_count"]:
        raise RollbackEvidenceError("r2 modified objects were not all retained")
    for field in (
        "unresolved_database_reference_count",
        "unresolved_deleted_reference_count",
        "objects_copied",
        "objects_deleted",
    ):
        _zero(document, field, "r2")
    for field in (
        "shared_authoritative_bucket",
        "version_retention_proven",
        "credential_probe_write_read_delete_passed",
        "probe_namespace_only",
        "all_database_references_resolve",
    ):
        _true(document, field, "r2")
    _false(document, "production_objects_mutated", "r2")


def _validate_payments(
    document: dict[str, Any],
    *,
    context: dict[str, Any],
    freeze_started: datetime,
    evidence_closed: datetime,
    expected_providers: tuple[str, ...],
) -> None:
    _require_fields(document, PAYMENTS_FIELDS, "payments")
    if document.get("schema") != "lecturesift-provider-rollback-payments-v1":
        raise RollbackEvidenceError("payments schema is unsupported")
    _common(
        document,
        label="payments",
        context=context,
        freeze_started=freeze_started,
        evidence_closed=evidence_closed,
    )
    if document.get("strategy") != "provider-export-and-signed-callback-drain":
        raise RollbackEvidenceError("payments strategy is unsafe")
    providers = document.get("configured_providers")
    results = document.get("provider_results")
    if (
        not isinstance(providers, list)
        or providers != list(expected_providers)
        or not isinstance(results, list)
        or len(results) != len(providers)
    ):
        raise RollbackEvidenceError("payments configured provider set is invalid")
    seen: list[str] = []
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            raise RollbackEvidenceError("payments provider result must be an object")
        label = f"payments.provider_results[{index}]"
        _require_fields(result, PROVIDER_FIELDS, label)
        provider = result.get("provider")
        if provider not in providers:
            raise RollbackEvidenceError(f"{label}.provider is not configured")
        seen.append(provider)
        if result.get("callback_target") != "render":
            raise RollbackEvidenceError(f"{label} callback target is not Render")
        if result.get("reconciled_database_manifest_sha256") != context.get(
            "reconciled_database_manifest_sha256"
        ):
            raise RollbackEvidenceError(
                f"{label} is not bound to the reconciled database manifest"
            )
        for field in (
            "callback_url_sha256",
            "provider_export_sha256",
            "local_order_manifest_before_sha256",
            "local_order_manifest_after_sha256",
            "callback_audit_sha256",
        ):
            _hex(result.get(field), HEX64, f"{label}.{field}")
        for field in (
            "events_examined",
            "events_matched",
            "duplicate_events_replayed",
            "unmatched_provider_events",
            "unmatched_local_orders",
            "pending_provider_payments",
            "pending_local_payments",
            "amount_or_currency_mismatches",
            "unaccepted_signature_failures",
        ):
            if not _plain_int(result.get(field)):
                raise RollbackEvidenceError(f"{label}.{field} must be a non-negative integer")
        if result["events_examined"] < 1 or result["events_matched"] != result["events_examined"]:
            raise RollbackEvidenceError(f"{label} does not reconcile every provider event")
        if result["duplicate_events_replayed"] < 1:
            raise RollbackEvidenceError(f"{label} lacks an idempotent callback replay")
        for field in (
            "unmatched_provider_events",
            "unmatched_local_orders",
            "pending_provider_payments",
            "pending_local_payments",
            "amount_or_currency_mismatches",
            "unaccepted_signature_failures",
        ):
            _zero(result, field, label)
        for field in (
            "positive_signed_callback_passed",
            "invalid_signature_rejected",
            "idempotent_replay_passed",
        ):
            _true(result, field, label)
    if seen != providers:
        raise RollbackEvidenceError("payments provider results are duplicated or out of order")
    for field in ("manual_pending_orders", "async_bank_transfers_pending"):
        _zero(document, field, "payments")
    for field in (
        "new_checkout_creation_blocked",
        "provider_callback_switch_complete",
        "provider_reconciliation_complete",
    ):
        _true(document, field, "payments")
    if document.get("callbacks_accepting_system") != "render-drain":
        raise RollbackEvidenceError("payments callbacks are not proven on Render drain")


def _validate_bundle_directory(bundle: Path) -> None:
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise RollbackEvidenceError("production rollback validation must run as root")
    if not bundle.is_absolute() or bundle.parent != ALLOWED_BUNDLE_ROOT:
        raise RollbackEvidenceError("bundle must be one directory below the fixed evidence root")
    if (
        Path(os.path.realpath(ALLOWED_BUNDLE_ROOT)) != ALLOWED_BUNDLE_ROOT
        or Path(os.path.realpath(bundle)) != bundle
    ):
        raise RollbackEvidenceError("bundle path resolves through a symlink")
    try:
        before = os.lstat(bundle)
    except OSError as exc:
        raise RollbackEvidenceError("rollback evidence bundle directory is missing") from exc
    if (
        not stat.S_ISDIR(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != 0
        or before.st_gid != 0
        or before.st_nlink < 2
        or stat.S_IMODE(before.st_mode) != 0o700
    ):
        raise RollbackEvidenceError("bundle directory must be a real root:root 0700 directory")
    entries = {entry.name for entry in os.scandir(bundle)}
    if entries != EXPECTED_FILES:
        raise RollbackEvidenceError("bundle directory must contain exactly the four evidence files")


def _validate_postgres_path(path: Path) -> None:
    if not path.is_absolute() or path.name != "RECONCILIATION_VERIFIED":
        raise RollbackEvidenceError("PostgreSQL marker path is unsafe")
    try:
        relative = path.relative_to(ALLOWED_POSTGRES_ROOT)
    except ValueError as exc:
        raise RollbackEvidenceError("PostgreSQL marker is outside the fixed rollback root") from exc
    if len(relative.parts) != 2 or not re.fullmatch(
        r"postgres-rollback-\d{8}T\d{6}Z", relative.parts[0]
    ):
        raise RollbackEvidenceError("PostgreSQL marker run directory is unexpected")
    if (
        Path(os.path.realpath(ALLOWED_POSTGRES_ROOT)) != ALLOWED_POSTGRES_ROOT
        or Path(os.path.realpath(path)) != path
    ):
        raise RollbackEvidenceError("PostgreSQL marker path resolves through a symlink")
    for directory in (
        ALLOWED_POSTGRES_ROOT.parent,
        ALLOWED_POSTGRES_ROOT,
        path.parent,
    ):
        try:
            identity = os.lstat(directory)
        except OSError as exc:
            raise RollbackEvidenceError(
                "PostgreSQL marker run-directory ancestry is missing"
            ) from exc
        if (
            not stat.S_ISDIR(identity.st_mode)
            or stat.S_ISLNK(identity.st_mode)
            or identity.st_uid != 0
            or identity.st_gid != 0
            or stat.S_IMODE(identity.st_mode) != 0o700
        ):
            raise RollbackEvidenceError(
                "PostgreSQL marker ancestry must be real root:root 0700 directories"
            )


def _validate_cutover_paths(
    baseline: Path, cutover_proof: Path, *, expected_cutover_id: str
) -> None:
    expected_baseline = ALLOWED_CUTOVER_ROOT / (
        f"payment-provider-baseline-{expected_cutover_id}.json"
    )
    expected_proof = ALLOWED_CUTOVER_ROOT / "provider-cutover.ok"
    if baseline != expected_baseline or cutover_proof != expected_proof:
        raise RollbackEvidenceError("provider baseline and cutover proof paths are fixed")
    if (
        Path(os.path.realpath(ALLOWED_CUTOVER_ROOT)) != ALLOWED_CUTOVER_ROOT
        or Path(os.path.realpath(baseline)) != baseline
        or Path(os.path.realpath(cutover_proof)) != cutover_proof
    ):
        raise RollbackEvidenceError("provider baseline path resolves through a symlink")
    try:
        root = os.lstat(ALLOWED_CUTOVER_ROOT)
    except OSError as exc:
        raise RollbackEvidenceError("provider cutover evidence root is missing") from exc
    if (
        not stat.S_ISDIR(root.st_mode)
        or stat.S_ISLNK(root.st_mode)
        or root.st_uid != 0
        or root.st_gid != 0
        or stat.S_IMODE(root.st_mode) != 0o700
    ):
        raise RollbackEvidenceError("provider cutover root must be root:root 0700")


def validate_bundle(
    bundle: Path,
    postgres_marker: Path,
    provider_baseline: Path,
    provider_cutover_proof: Path,
    runtime_env: Path,
    *,
    expected_rollback_id: str,
    expected_cutover_id: str,
    expected_ovh_revision: str,
    expected_render_revision: str,
    now: datetime | None = None,
    enforce_filesystem: bool = True,
) -> dict[str, Any]:
    """Return a secret-free readiness proof or raise ``RollbackEvidenceError``."""
    _hex(expected_rollback_id, HEX32, "expected rollback id")
    _hex(expected_cutover_id, HEX32, "expected cutover id")
    _hex(expected_ovh_revision, HEX40, "expected OVH revision")
    _hex(expected_render_revision, HEX40, "expected Render revision")
    if enforce_filesystem:
        _validate_bundle_directory(bundle)
        if bundle.name != f"rollback-{expected_rollback_id}":
            raise RollbackEvidenceError("bundle directory name does not match rollback id")
        _validate_postgres_path(postgres_marker)
        _validate_cutover_paths(
            provider_baseline,
            provider_cutover_proof,
            expected_cutover_id=expected_cutover_id,
        )
        if runtime_env != RUNTIME_ENV or Path(os.path.realpath(runtime_env)) != runtime_env:
            raise RollbackEvidenceError("runtime provider configuration path is not fixed")

    context, context_payload = _load_json(
        bundle / "context.json", enforce_filesystem=enforce_filesystem
    )
    redis, redis_payload = _load_json(
        bundle / "redis.json", enforce_filesystem=enforce_filesystem
    )
    r2, r2_payload = _load_json(
        bundle / "r2.json", enforce_filesystem=enforce_filesystem
    )
    payments, payments_payload = _load_json(
        bundle / "payments.json", enforce_filesystem=enforce_filesystem
    )
    postgres_payload = _read_file(
        postgres_marker,
        maximum=MAX_MARKER_BYTES,
        enforce_filesystem=enforce_filesystem,
    )
    marker = _parse_postgres_marker(postgres_payload)
    baseline, baseline_payload = _load_json(
        provider_baseline, enforce_filesystem=enforce_filesystem
    )
    cutover_proof_payload = _read_file(
        provider_cutover_proof,
        maximum=MAX_MARKER_BYTES,
        enforce_filesystem=enforce_filesystem,
    )
    runtime_payload = _read_file(
        runtime_env,
        maximum=MAX_RUNTIME_BYTES,
        enforce_filesystem=enforce_filesystem,
    )

    validation_time = now or datetime.now(timezone.utc)
    baseline_providers = _validate_provider_baseline(
        baseline,
        baseline_payload,
        cutover_proof_payload,
        expected_cutover_id=expected_cutover_id,
        expected_ovh_revision=expected_ovh_revision,
        now=validation_time,
    )
    current_providers, current_presence_digest = _runtime_provider_census(runtime_payload)
    historical_providers = _marker_historical_providers(
        marker["historical_payment_providers"]
    )
    expected_providers = tuple(
        sorted(set(baseline_providers) | set(current_providers) | set(historical_providers))
    )
    freeze_started, evidence_closed, valid_until = _validate_context(
        context,
        expected_rollback_id=expected_rollback_id,
        expected_cutover_id=expected_cutover_id,
        expected_ovh_revision=expected_ovh_revision,
        expected_render_revision=expected_render_revision,
        expected_providers=expected_providers,
        now=validation_time,
    )
    payload_digests = {
        "postgres_marker_sha256": _digest(postgres_payload),
        "redis_evidence_sha256": _digest(redis_payload),
        "r2_evidence_sha256": _digest(r2_payload),
        "payments_evidence_sha256": _digest(payments_payload),
    }
    for field, digest in payload_digests.items():
        if context[field] != digest:
            raise RollbackEvidenceError(f"context.{field} does not match its exact artifact")
    if marker["source_worker_stop_evidence_sha256"] != context["source_worker_stop_evidence_sha256"]:
        raise RollbackEvidenceError("PostgreSQL worker-stop proof does not match context")
    marker_identity = {
        "rollback_id": context["rollback_id"],
        "cutover_id": context["cutover_id"],
        "ovh_release_revision": context["ovh_release_revision"],
        "render_release_revision": context["render_release_revision"],
        "reconciled_database_manifest_sha256": context[
            "reconciled_database_manifest_sha256"
        ],
    }
    for field, expected in marker_identity.items():
        if marker[field] != expected:
            raise RollbackEvidenceError(
                f"PostgreSQL marker {field} does not match context"
            )
    marker_time = _timestamp(marker["verified_at_utc"], "PostgreSQL verified_at_utc")
    if not freeze_started <= marker_time <= evidence_closed:
        raise RollbackEvidenceError("PostgreSQL marker falls outside the frozen evidence window")

    _validate_redis(
        redis,
        context=context,
        freeze_started=freeze_started,
        evidence_closed=evidence_closed,
    )
    _validate_r2(
        r2,
        context=context,
        freeze_started=freeze_started,
        evidence_closed=evidence_closed,
    )
    _validate_payments(
        payments,
        context=context,
        freeze_started=freeze_started,
        evidence_closed=evidence_closed,
        expected_providers=expected_providers,
    )

    aggregate = b"".join(
        name.encode("ascii") + b"\0" + bytes.fromhex(digest) + b"\n"
        for name, digest in sorted(
            {
                "context.json": _digest(context_payload),
                "payments.json": payload_digests["payments_evidence_sha256"],
                "postgres.marker": payload_digests["postgres_marker_sha256"],
                "provider-baseline.json": _digest(baseline_payload),
                "provider-cutover.ok": _digest(cutover_proof_payload),
                "r2.json": payload_digests["r2_evidence_sha256"],
                "redis.json": payload_digests["redis_evidence_sha256"],
            }.items()
        )
    )
    return {
        "schema": "lecturesift-provider-rollback-readiness-v1",
        "status": "ready-for-reviewed-user-traffic-switch",
        "rollback_id": context["rollback_id"],
        "cutover_id": context["cutover_id"],
        "ovh_release_revision": context["ovh_release_revision"],
        "render_release_revision": context["render_release_revision"],
        "configured_providers": list(expected_providers),
        "provider_baseline_sha256": _digest(baseline_payload),
        "provider_cutover_proof_sha256": _digest(cutover_proof_payload),
        "current_provider_configuration_presence_sha256": current_presence_digest,
        "historical_provider_manifest_sha256": marker[
            "historical_provider_manifest_sha256"
        ],
        "artifact_set_sha256": _digest(aggregate),
        **payload_digests,
        "validated_at_utc": validation_time.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "valid_until_utc": valid_until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "user_traffic_changed": False,
        "validator_scope": "structure-cross-binding-freshness-only",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--postgres-marker", required=True, type=Path)
    parser.add_argument("--provider-baseline", required=True, type=Path)
    parser.add_argument("--provider-cutover-proof", required=True, type=Path)
    parser.add_argument("--expected-rollback-id", required=True)
    parser.add_argument("--expected-cutover-id", required=True)
    parser.add_argument("--expected-ovh-revision", required=True)
    parser.add_argument("--expected-render-revision", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        proof = validate_bundle(
            args.bundle,
            args.postgres_marker,
            args.provider_baseline,
            args.provider_cutover_proof,
            RUNTIME_ENV,
            expected_rollback_id=args.expected_rollback_id,
            expected_cutover_id=args.expected_cutover_id,
            expected_ovh_revision=args.expected_ovh_revision,
            expected_render_revision=args.expected_render_revision,
        )
    except RollbackEvidenceError as exc:
        print(f"Rollback reconciliation evidence rejected: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(proof, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
