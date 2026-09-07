from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "deploy" / "validate_rollback_reconciliation_bundle.py"
SPEC = importlib.util.spec_from_file_location(
    "validate_rollback_reconciliation_bundle", TOOL_PATH
)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)

ROLLBACK_ID = "1" * 32
CUTOVER_ID = "2" * 32
OVH_REVISION = "3" * 40
RENDER_REVISION = "4" * 40
WORKER_PROOF = "5" * 64
DATABASE_MANIFEST = "6" * 64
OVH_SCHEDULER_PROOF = "7" * 64
RENDER_SCHEDULER_PROOF = "8" * 64
OVH_INSTAGRAM_PROOF = "9" * 64
RENDER_INSTAGRAM_PROOF = "a" * 64
NOW = datetime(2026, 9, 7, 12, 15, tzinfo=timezone.utc)


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _common() -> dict:
    return {
        "rollback_id": ROLLBACK_ID,
        "cutover_id": CUTOVER_ID,
        "ovh_release_revision": OVH_REVISION,
        "render_release_revision": RENDER_REVISION,
        "reconciled_database_manifest_sha256": DATABASE_MANIFEST,
        "ovh_scheduler_stop_evidence_sha256": OVH_SCHEDULER_PROOF,
        "render_scheduler_stop_evidence_sha256": RENDER_SCHEDULER_PROOF,
        "ovh_instagram_stop_evidence_sha256": OVH_INSTAGRAM_PROOF,
        "render_instagram_stop_evidence_sha256": RENDER_INSTAGRAM_PROOF,
        "started_at_utc": "2026-09-07T12:03:00Z",
        "completed_at_utc": "2026-09-07T12:09:00Z",
    }


def _redis() -> dict:
    return {
        **_common(),
        "schema": "lecturesift-provider-rollback-redis-v1",
        "source_system": "ovh",
        "target_system": "render",
        "logical_key": "lecturesift:jobs:v2",
        "strategy": "replace-single-versioned-json-key",
        "payload_schema_version": 2,
        "source_before_existed": True,
        "source_after_existed": True,
        "target_before_existed": False,
        "target_after_existed": True,
        "source_before_sha256": "6" * 64,
        "source_after_sha256": "6" * 64,
        "target_before_sha256": "7" * 64,
        "target_after_sha256": "6" * 64,
        "target_rollback_copy_sha256": "7" * 64,
        "target_non_job_before_sha256": "8" * 64,
        "target_non_job_after_sha256": "8" * 64,
        "source_job_count": 12,
        "target_job_count": 12,
        "source_active_job_count": 0,
        "target_active_job_count": 0,
        "source_processing_lock_count": 0,
        "target_processing_lock_count": 0,
        "source_worker_stop_evidence_sha256": WORKER_PROOF,
        "durability_acknowledged": True,
        "target_lock_token_checked": True,
        "unsafe_merge_attempted": False,
    }


def _r2() -> dict:
    return {
        **_common(),
        "schema": "lecturesift-provider-rollback-r2-v1",
        "strategy": "shared-authoritative-bucket-retain-no-delete",
        "shared_authoritative_bucket": True,
        "endpoint_binding_sha256": "9" * 64,
        "bucket_binding_sha256": "a" * 64,
        "pre_cutover_inventory_sha256": "b" * 64,
        "freeze_before_inventory_sha256": "c" * 64,
        "freeze_after_inventory_sha256": "c" * 64,
        "database_reference_manifest_sha256": "d" * 64,
        "reference_verification_sha256": "e" * 64,
        "freeze_before_object_count": 22,
        "freeze_after_object_count": 22,
        "freeze_before_total_bytes": 1000,
        "freeze_after_total_bytes": 1000,
        "database_reference_count": 18,
        "verified_reference_count": 18,
        "added_since_cutover_count": 4,
        "modified_since_cutover_count": 2,
        "deleted_since_cutover_count": 1,
        "retained_added_object_count": 4,
        "retained_modified_object_count": 2,
        "unresolved_database_reference_count": 0,
        "unresolved_deleted_reference_count": 0,
        "objects_copied": 0,
        "objects_deleted": 0,
        "production_objects_mutated": False,
        "version_retention_proven": True,
        "credential_probe_write_read_delete_passed": True,
        "probe_namespace_only": True,
        "all_database_references_resolve": True,
    }


def _payments() -> dict:
    return {
        **_common(),
        "schema": "lecturesift-provider-rollback-payments-v1",
        "strategy": "provider-export-and-signed-callback-drain",
        "configured_providers": ["iyzico"],
        "provider_results": [
            {
                "provider": "iyzico",
                "callback_url_sha256": "f" * 64,
                "provider_export_sha256": "0" * 64,
                "local_order_manifest_before_sha256": "1" * 64,
                "local_order_manifest_after_sha256": "2" * 64,
                "callback_audit_sha256": "3" * 64,
                "events_examined": 7,
                "events_matched": 7,
                "duplicate_events_replayed": 1,
                "unmatched_provider_events": 0,
                "unmatched_local_orders": 0,
                "pending_provider_payments": 0,
                "pending_local_payments": 0,
                "amount_or_currency_mismatches": 0,
                "unaccepted_signature_failures": 0,
                "positive_signed_callback_passed": True,
                "invalid_signature_rejected": True,
                "idempotent_replay_passed": True,
                "callback_target": "render",
                "reconciled_database_manifest_sha256": DATABASE_MANIFEST,
            }
        ],
        "manual_pending_orders": 0,
        "async_bank_transfers_pending": 0,
        "new_checkout_creation_blocked": True,
        "provider_callback_switch_complete": True,
        "callbacks_accepting_system": "render-drain",
        "provider_reconciliation_complete": True,
    }


def _marker() -> bytes:
    return (
        "status=postgres-rollback-reconciled\n"
        "verified_at_utc=2026-09-07T12:02:00Z\n"
        f"rollback_id={ROLLBACK_ID}\n"
        f"cutover_id={CUTOVER_ID}\n"
        f"ovh_release_revision={OVH_REVISION}\n"
        f"render_release_revision={RENDER_REVISION}\n"
        f"reconciled_database_manifest_sha256={DATABASE_MANIFEST}\n"
        "historical_payment_providers=iyzico\n"
        f"historical_provider_manifest_sha256={'b' * 64}\n"
        "render_matches_ovh=true\n"
        "automatic_row_merge=false\n"
        "whole_database_replacement=false\n"
        "replacement_scope=public-and-lecturesift-worker-schemas\n"
        "approved_app_schemas_replaced=true\n"
        "target_database_policy_preserved=true\n"
        "target_app_acl_policy=database-owner-only\n"
        f"source_worker_stop_evidence_sha256={WORKER_PROOF}\n"
        "redis_reconciliation_complete=false\n"
        "r2_reconciliation_complete=false\n"
        "traffic_changed=false\n"
    ).encode("ascii")


def _cutover_proof() -> bytes:
    return (
        f"cutover_id={CUTOVER_ID}\n"
        f"release_revision={OVH_REVISION}\n"
        "status=provider-cutover-verified\n"
        "version=3\n"
    ).encode("ascii")


def _bundle(tmp_path: Path) -> tuple[Path, Path]:
    bundle = tmp_path / f"rollback-{ROLLBACK_ID}"
    bundle.mkdir(parents=True)
    redis_payload = _json_bytes(_redis())
    r2_payload = _json_bytes(_r2())
    payments_payload = _json_bytes(_payments())
    marker_payload = _marker()
    marker = tmp_path / "RECONCILIATION_VERIFIED"
    marker.write_bytes(marker_payload)
    cutover_proof = tmp_path / "provider-cutover.ok"
    cutover_proof.write_bytes(_cutover_proof())
    baseline = {
        "schema": "lecturesift-payment-provider-baseline-v1",
        "cutover_id": CUTOVER_ID,
        "release_revision": OVH_REVISION,
        "provider_cutover_proof_sha256": hashlib.sha256(
            cutover_proof.read_bytes()
        ).hexdigest(),
        "configuration_presence_sha256": "a" * 64,
        "configured_providers": ["iyzico"],
        "recorded_at_utc": "2026-09-07T11:00:00Z",
    }
    (tmp_path / "payment-provider-baseline.json").write_bytes(_json_bytes(baseline))
    (tmp_path / "runtime.env").write_text(
        "IYZICO_API_KEY=live-key\nIYZICO_SECRET_KEY=live-secret\n",
        encoding="utf-8",
    )
    context = {
        "schema": "lecturesift-provider-rollback-context-v1",
        "rollback_id": ROLLBACK_ID,
        "cutover_id": CUTOVER_ID,
        "ovh_release_revision": OVH_REVISION,
        "render_release_revision": RENDER_REVISION,
        "freeze_started_at_utc": "2026-09-07T12:00:00Z",
        "evidence_closed_at_utc": "2026-09-07T12:10:00Z",
        "valid_until_utc": "2026-09-07T12:30:00Z",
        "postgres_marker_sha256": hashlib.sha256(marker_payload).hexdigest(),
        "redis_evidence_sha256": hashlib.sha256(redis_payload).hexdigest(),
        "r2_evidence_sha256": hashlib.sha256(r2_payload).hexdigest(),
        "payments_evidence_sha256": hashlib.sha256(payments_payload).hexdigest(),
        "source_worker_stop_evidence_sha256": WORKER_PROOF,
        "reconciled_database_manifest_sha256": DATABASE_MANIFEST,
        "expected_configured_providers": ["iyzico"],
        "ovh_api_mode": "freeze",
        "render_api_mode": "drain",
        "ovh_worker_stopped": True,
        "render_worker_stopped": True,
        "ovh_queue_empty": True,
        "render_queue_empty": True,
        "ovh_scheduler_stopped": True,
        "render_scheduler_stopped": True,
        "ovh_instagram_publisher_stopped": True,
        "render_instagram_publisher_stopped": True,
        "ovh_scheduler_stop_evidence_sha256": OVH_SCHEDULER_PROOF,
        "render_scheduler_stop_evidence_sha256": RENDER_SCHEDULER_PROOF,
        "ovh_instagram_stop_evidence_sha256": OVH_INSTAGRAM_PROOF,
        "render_instagram_stop_evidence_sha256": RENDER_INSTAGRAM_PROOF,
        "new_checkout_creation_blocked": True,
        "user_traffic_changed": False,
        "provider_callback_traffic_changed": True,
    }
    (bundle / "redis.json").write_bytes(redis_payload)
    (bundle / "r2.json").write_bytes(r2_payload)
    (bundle / "payments.json").write_bytes(payments_payload)
    (bundle / "context.json").write_bytes(_json_bytes(context))
    return bundle, marker


def _validate(bundle: Path, marker: Path, *, now: datetime = NOW):
    return validator.validate_bundle(
        bundle,
        marker,
        marker.parent / "payment-provider-baseline.json",
        marker.parent / "provider-cutover.ok",
        marker.parent / "runtime.env",
        expected_rollback_id=ROLLBACK_ID,
        expected_cutover_id=CUTOVER_ID,
        expected_ovh_revision=OVH_REVISION,
        expected_render_revision=RENDER_REVISION,
        now=now,
        enforce_filesystem=False,
    )


def _rewrite_json(path: Path, transform) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    transform(value)
    path.write_bytes(_json_bytes(value))


def _refresh_context_digest(bundle: Path, field: str, filename: str) -> None:
    _rewrite_json(
        bundle / "context.json",
        lambda context: context.__setitem__(
            field, hashlib.sha256((bundle / filename).read_bytes()).hexdigest()
        ),
    )


def test_valid_bundle_is_bound_fresh_and_does_not_claim_a_traffic_switch(tmp_path: Path):
    bundle, marker = _bundle(tmp_path)

    proof = _validate(bundle, marker)

    assert proof["status"] == "ready-for-reviewed-user-traffic-switch"
    assert proof["rollback_id"] == ROLLBACK_ID
    assert proof["user_traffic_changed"] is False
    assert proof["validator_scope"] == "structure-cross-binding-freshness-only"
    assert len(proof["artifact_set_sha256"]) == 64


def test_redis_source_change_is_rejected_even_when_context_hash_is_refreshed(tmp_path: Path):
    bundle, marker = _bundle(tmp_path)
    _rewrite_json(
        bundle / "redis.json",
        lambda redis: redis.__setitem__("source_after_sha256", "9" * 64),
    )
    _refresh_context_digest(bundle, "redis_evidence_sha256", "redis.json")

    with pytest.raises(validator.RollbackEvidenceError, match="source payload changed"):
        _validate(bundle, marker)


def test_r2_inventory_mutation_or_production_delete_is_rejected(tmp_path: Path):
    bundle, marker = _bundle(tmp_path)
    _rewrite_json(
        bundle / "r2.json",
        lambda r2: (
            r2.__setitem__("freeze_after_inventory_sha256", "d" * 64),
            r2.__setitem__("objects_deleted", 1),
        ),
    )
    _refresh_context_digest(bundle, "r2_evidence_sha256", "r2.json")

    with pytest.raises(validator.RollbackEvidenceError, match="inventory changed"):
        _validate(bundle, marker)


def test_pending_or_unproven_payment_callback_is_rejected(tmp_path: Path):
    bundle, marker = _bundle(tmp_path)

    def mutate(payments):
        result = payments["provider_results"][0]
        result["pending_provider_payments"] = 1
        result["positive_signed_callback_passed"] = False

    _rewrite_json(bundle / "payments.json", mutate)
    _refresh_context_digest(bundle, "payments_evidence_sha256", "payments.json")

    with pytest.raises(validator.RollbackEvidenceError, match="pending_provider_payments"):
        _validate(bundle, marker)


def test_immutable_baseline_prevents_a_bundle_from_omitting_paytr(tmp_path: Path):
    bundle, marker = _bundle(tmp_path)
    _rewrite_json(
        marker.parent / "payment-provider-baseline.json",
        lambda baseline: baseline.__setitem__(
            "configured_providers", ["iyzico", "paytr"]
        ),
    )

    with pytest.raises(
        validator.RollbackEvidenceError,
        match="expected configured providers",
    ):
        validator.validate_bundle(
            bundle,
            marker,
            marker.parent / "payment-provider-baseline.json",
            marker.parent / "provider-cutover.ok",
            marker.parent / "runtime.env",
            expected_rollback_id=ROLLBACK_ID,
            expected_cutover_id=CUTOVER_ID,
            expected_ovh_revision=OVH_REVISION,
            expected_render_revision=RENDER_REVISION,
            now=NOW,
            enforce_filesystem=False,
        )


def test_current_runtime_provider_added_after_cutover_cannot_be_omitted(tmp_path: Path):
    bundle, marker = _bundle(tmp_path)
    (marker.parent / "runtime.env").write_text(
        "IYZICO_API_KEY=live-key\nIYZICO_SECRET_KEY=live-secret\n"
        "PAYTR_MERCHANT_ID=id\nPAYTR_MERCHANT_KEY=key\nPAYTR_MERCHANT_SALT=salt\n",
        encoding="utf-8",
    )

    with pytest.raises(
        validator.RollbackEvidenceError,
        match="trusted provider census",
    ):
        _validate(bundle, marker)


def test_live_scheduler_or_instagram_publisher_is_rejected(tmp_path: Path):
    bundle, marker = _bundle(tmp_path)
    _rewrite_json(
        bundle / "context.json",
        lambda context: context.__setitem__("ovh_scheduler_stopped", False),
    )

    with pytest.raises(
        validator.RollbackEvidenceError,
        match="ovh_scheduler_stopped must be exactly true",
    ):
        _validate(bundle, marker)


def test_postgres_marker_identity_must_match_the_bundle(tmp_path: Path):
    bundle, marker = _bundle(tmp_path)
    marker.write_bytes(
        marker.read_bytes().replace(
            f"cutover_id={CUTOVER_ID}".encode(),
            f"cutover_id={'a' * 32}".encode(),
        )
    )
    _rewrite_json(
        bundle / "context.json",
        lambda context: context.__setitem__(
            "postgres_marker_sha256", hashlib.sha256(marker.read_bytes()).hexdigest()
        ),
    )

    with pytest.raises(validator.RollbackEvidenceError, match="cutover_id does not match"):
        _validate(bundle, marker)


def test_postgres_database_manifest_must_match_the_bundle(tmp_path: Path):
    bundle, marker = _bundle(tmp_path)
    marker.write_bytes(
        marker.read_bytes().replace(
            f"reconciled_database_manifest_sha256={DATABASE_MANIFEST}".encode(),
            f"reconciled_database_manifest_sha256={'f' * 64}".encode(),
        )
    )
    _rewrite_json(
        bundle / "context.json",
        lambda context: context.__setitem__(
            "postgres_marker_sha256", hashlib.sha256(marker.read_bytes()).hexdigest()
        ),
    )

    with pytest.raises(
        validator.RollbackEvidenceError,
        match="reconciled_database_manifest_sha256 does not match",
    ):
        _validate(bundle, marker)


def test_bundle_rejects_tampering_unknown_fields_and_expiry(tmp_path: Path):
    bundle, marker = _bundle(tmp_path)
    (bundle / "redis.json").write_bytes((bundle / "redis.json").read_bytes() + b" ")
    with pytest.raises(validator.RollbackEvidenceError, match="exact artifact"):
        _validate(bundle, marker)

    bundle, marker = _bundle(tmp_path / "second")
    _rewrite_json(bundle / "context.json", lambda context: context.__setitem__("approved", True))
    with pytest.raises(validator.RollbackEvidenceError, match="fields do not match"):
        _validate(bundle, marker)

    bundle, marker = _bundle(tmp_path / "third")
    with pytest.raises(validator.RollbackEvidenceError, match="not current"):
        _validate(bundle, marker, now=datetime(2026, 9, 7, 12, 31, tzinfo=timezone.utc))


def test_tracked_runbook_and_validator_explicitly_forbid_unsafe_automation():
    runbook = (ROOT / "deploy" / "ROLLBACK_RECONCILIATION.md").read_text(
        encoding="utf-8"
    )
    source = TOOL_PATH.read_text(encoding="utf-8")

    for phrase in (
        "Never copy an RDB",
        "delete a production R2 object",
        "mark a payment paid by hand",
        "does not perform that switch",
        "genuine signed positive callback",
    ):
        assert phrase in runbook
    for forbidden_import in ("import boto3", "import redis", "import httpx", "import subprocess"):
        assert forbidden_import not in source
    selector_stop = runbook.index(
        "sudo systemctl stop lecturesift-ingress-selector.service"
    )
    ingress_stop = runbook.index("sudo systemctl stop lecturesift-ingress.service")
    assert selector_stop < ingress_stop


def test_postgres_rollback_marker_is_bound_to_exact_session_and_releases():
    script = (ROOT / "deploy" / "rollback_postgres_to_render.sh").read_text(
        encoding="utf-8"
    )

    for value in (
        "LECTURESIFT_PROVIDER_ROLLBACK_ID",
        "LECTURESIFT_PROVIDER_CUTOVER_ID",
        "LECTURESIFT_OVH_RELEASE_REVISION",
        "LECTURESIFT_RENDER_RELEASE_REVISION",
        "provider_cutover_evidence.py",
        "validate-final",
        "payload.get(\"revision\") == os.environ[\"HEALTH_EXPECTED_REVISION\"]",
        "payload.get(\"revision\") == os.environ[\"EXPECTED_RENDER_REVISION\"]",
        "printf 'rollback_id=%s\\n'",
        "printf 'cutover_id=%s\\n'",
        "printf 'ovh_release_revision=%s\\n'",
        "printf 'render_release_revision=%s\\n'",
        "capture_local_payment_provider_census",
        "printf 'reconciled_database_manifest_sha256=%s\\n'",
        "printf 'historical_payment_providers=%s\\n'",
        "printf 'historical_provider_manifest_sha256=%s\\n'",
        "verify_instagram_publishers_stopped.sh",
    ):
        assert value in script
    assert script.count("assert_ovh_instagram_publishers_stopped") >= 3
    assert script.rindex("assert_ovh_instagram_publishers_stopped") < script.index(
        'render_mutated="true"'
    )
    validator_source = TOOL_PATH.read_text(encoding="utf-8")
    assert "PostgreSQL marker ancestry must be real root:root 0700 directories" in validator_source
