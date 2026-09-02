#!/usr/bin/env python3
"""Validate and hash the exact inner rehearsal result artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

import verify_schema_transition as schema_verifier


FORMAT = "lecturesift-exact-rehearsal-result-v3"
RUN_ROOT = Path("/var/backups/lecturesift/rehearsal")
RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z")
REVISION = re.compile(r"[0-9a-f]{40}")
SAFE_FAMILIES = (
    "DATABASE",
    "SCHEMA",
    "SCHEMA_OBJECT",
    "TABLE",
    "ANOMALY",
    "STATUS",
    "SCHEMA_COMPAT",
    "TABLE_DIFF",
    "UNVALIDATED_FK",
    "MANIFEST_COMPLETE",
)
HASH_FIELDS = (
    "application_e2e_sha256",
    "environment_proof_sha256",
    "formats_e2e_sha256",
    "purge_e2e_sha256",
    "r2_negative_capability_sha256",
    "schema_after_e2e_sha256",
    "schema_transition_sha256",
    "target_after_e2e_manifest_sha256",
    "target_migrated_manifest_sha256",
)
ALL_FORMAT_CASES = frozenset(
    {"native_documents", "ocr_images", "mp3_audio", "mp4_video"}
)
BASE_FORMAT_CASES = frozenset({"native_documents", "ocr_images"})
AI_FORMAT_CASES = frozenset({"mp3_audio", "mp4_video"})
FORMATS_BY_CASE = {
    "native_documents": frozenset({"txt", "md", "docx", "pdf", "pptx"}),
    "ocr_images": frozenset({"png", "jpg", "jpeg", "webp", "tif", "tiff"}),
    "mp3_audio": frozenset({"mp3"}),
    "mp4_video": frozenset({"mp4_8s"}),
}
R2_PAYLOAD_EVIDENCE_BY_CASE = {
    "native_documents": frozenset({"pdf_sample", "archive_zip"}),
    "ocr_images": frozenset({"pdf_sample", "archive_zip"}),
    "mp3_audio": frozenset({"pdf_sample", "archive_zip", "audio_mp3"}),
    "mp4_video": frozenset(
        {"pdf_sample", "archive_zip", "audio_mp3", "slide_sample"}
    ),
}


class ArtifactError(RuntimeError):
    pass


def _private_file(path: Path) -> bytes:
    try:
        details = path.lstat()
    except OSError as exc:
        raise ArtifactError(f"missing rehearsal artifact: {path.name}") from exc
    if (
        not stat.S_ISREG(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or (os.name == "posix" and details.st_uid != 0)
        or stat.S_IMODE(details.st_mode) & 0o077
        or path.resolve(strict=True) != path
    ):
        raise ArtifactError(f"unsafe rehearsal artifact: {path.name}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ArtifactError(f"unreadable rehearsal artifact: {path.name}") from exc


def _json_artifact(path: Path) -> tuple[dict[str, object], bytes]:
    payload = _private_file(path)
    try:
        document = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"invalid JSON rehearsal artifact: {path.name}") from exc
    if not isinstance(document, dict):
        raise ArtifactError(f"invalid JSON rehearsal object: {path.name}")
    return document, payload


def _canonical_manifest(raw: Path) -> str:
    lines = raw.read_text(encoding="utf-8").splitlines()
    selected = [
        line
        for line in lines
        if any(line.startswith(f"{family}|") for family in SAFE_FAMILIES)
    ]
    return "\n".join(sorted(selected)) + "\n"


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _unique_string_set(value: object, *, label: str) -> frozenset[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise ArtifactError(f"format E2E {label} is malformed")
    return frozenset(value)


def _validate_format_coverage(formats: dict[str, object], submitted: int) -> str:
    """Require the complete AI-enabled format matrix for release admission.

    The format runner deliberately supports an ``intentionally_absent`` AI
    state so operators can diagnose the document/OCR path without a provider
    credential.  Such a report is useful evidence for a debug run, but it must
    never be promotable into an exact production admission.
    """

    requested = _unique_string_set(
        formats.get("requested_cases"), label="requested case coverage"
    )
    executed = _unique_string_set(formats.get("cases"), label="executed case coverage")
    emitted_formats = _unique_string_set(
        formats.get("formats"), label="emitted format coverage"
    )
    ai_provider = formats.get("ai_provider_state")
    if ai_provider not in {"dedicated", "intentionally_absent"}:
        raise ArtifactError("format E2E AI provider state is not explicit")
    if formats.get("ai_provider_tested") is not (ai_provider == "dedicated"):
        raise ArtifactError("format E2E AI provider evidence is inconsistent")

    if ai_provider != "dedicated":
        raise ArtifactError(
            "exact admission requires a dedicated rehearsal AI provider"
        )

    # Operators may select a smaller family set for diagnosis, but an exact
    # production admission is valid only when the report requested every case.
    if requested != ALL_FORMAT_CASES:
        raise ArtifactError("debug format subset cannot produce an admission")

    expected_cases = ALL_FORMAT_CASES
    expected_skips: dict[str, str] = {}
    expected_formats = frozenset(
        item for case in expected_cases for item in FORMATS_BY_CASE[case]
    )
    if executed != expected_cases or submitted != len(expected_cases):
        raise ArtifactError("format E2E required case coverage is incomplete")
    if formats.get("skipped_cases") != expected_skips:
        raise ArtifactError("format E2E skipped-case evidence is inconsistent")
    if emitted_formats != expected_formats:
        raise ArtifactError("format E2E emitted format coverage is incomplete")
    return "dedicated"


def _validate_r2_payload_evidence(formats: dict[str, object], submitted: int) -> int:
    """Bind every durable R2 payload proof to one exact format case.

    A result reopen is counted per job, while payload evidence is counted per
    independently downloaded object.  The video case therefore proves more
    than one object.  An exact case-to-evidence map prevents a forged global
    total from hiding a missing PDF, ZIP, MP3 or slide proof.
    """

    raw = formats.get("r2_payloads_verified_by_case")
    if not isinstance(raw, dict) or set(raw) != ALL_FORMAT_CASES:
        raise ArtifactError("format E2E R2 payload evidence cases are malformed")
    if submitted != len(R2_PAYLOAD_EVIDENCE_BY_CASE):
        raise ArtifactError("format E2E R2 payload evidence job count is inconsistent")

    total = 0
    for case_name, expected in R2_PAYLOAD_EVIDENCE_BY_CASE.items():
        actual = _unique_string_set(
            raw.get(case_name), label=f"R2 {case_name} payload evidence"
        )
        if actual != expected:
            raise ArtifactError(
                f"format E2E R2 {case_name} payload evidence is incomplete"
            )
        total += len(actual)

    recorded = formats.get("r2_payloads_verified")
    if isinstance(recorded, bool) or not isinstance(recorded, int) or recorded != total:
        raise ArtifactError("format E2E R2 payload evidence total is inconsistent")
    return total


def validate(root: Path, run_dir: Path, revision: str) -> dict[str, str]:
    if not REVISION.fullmatch(revision):
        raise ArtifactError("invalid rehearsal revision")
    try:
        resolved_root = root.resolve(strict=True)
        resolved_run = run_dir.resolve(strict=True)
        run_details = run_dir.lstat()
    except OSError as exc:
        raise ArtifactError("rehearsal paths are missing") from exc
    if (
        resolved_root != root
        or resolved_run != run_dir
        or run_dir.parent != RUN_ROOT
        or not RUN_ID.fullmatch(run_dir.name)
        or not stat.S_ISDIR(run_details.st_mode)
        or stat.S_ISLNK(run_details.st_mode)
        or (os.name == "posix" and run_details.st_uid != 0)
        or stat.S_IMODE(run_details.st_mode) != 0o700
    ):
        raise ArtifactError("unsafe rehearsal result directory")

    contract = root / "deploy" / "schema_contract_payment_provider_sessions_v1.txt"
    preserved_contract = (
        root / "deploy" / "schema_contract_billing_email_verifications_v1.txt"
    )
    before = run_dir / "target.txt"
    migrated = run_dir / "target-migrated.txt"
    after_e2e = run_dir / "target-after-e2e.txt"
    for raw in (before, migrated, after_e2e):
        _private_file(raw)
    transition, contract_digest = schema_verifier.verify_transition(
        before, migrated, contract, preserved_contract
    )
    after_digest = schema_verifier.verify_current(
        after_e2e, contract, preserved_contract
    )
    if after_digest != contract_digest:
        raise ArtifactError("schema contract digest changed during E2E")

    migrated_safe = run_dir / "target-migrated.safe"
    after_safe = run_dir / "target-after-e2e.safe"
    migrated_safe_payload = _private_file(migrated_safe)
    after_safe_payload = _private_file(after_safe)
    if migrated_safe_payload.decode("utf-8") != _canonical_manifest(migrated):
        raise ArtifactError("migrated manifest canonical artifact does not match raw output")
    if after_safe_payload.decode("utf-8") != _canonical_manifest(after_e2e):
        raise ArtifactError("post-E2E manifest canonical artifact does not match raw output")
    if migrated_safe_payload != after_safe_payload:
        raise ArtifactError("E2E changed the strict canonical database manifest")

    schema_transition = _private_file(run_dir / "schema-transition.txt")
    expected_transition = (
        f"schema_contract_sha256={contract_digest}\n"
        f"schema_transition={transition}\n"
    ).encode()
    if schema_transition != expected_transition:
        raise ArtifactError("schema-transition artifact does not match verification")
    schema_after = _private_file(run_dir / "schema-after-e2e.txt")
    expected_after = (
        f"schema_contract_sha256={contract_digest}\n"
        "schema_contract_state=current\n"
    ).encode()
    if schema_after != expected_after:
        raise ArtifactError("post-E2E schema artifact does not match verification")

    application, application_payload = _json_artifact(
        run_dir / "application-e2e.json"
    )
    application_true = {
        "account",
        "admin",
        "analysis_completed",
        "durable_result_published",
        "email_provider_disabled",
        "instagram_provider_disabled",
        "invalid_payment_webhook_rejected",
        "ok",
        "old_admin_token_rejected",
        "payment_providers_disabled",
        "r2_roundtrip",
        "rehearsal_account_closed",
        "result_reopened",
    }
    if any(application.get(field) is not True for field in application_true):
        raise ArtifactError("application E2E artifact is not successful")
    application_jobs = application.get("rehearsal_job_ids")
    if not isinstance(application_jobs, list) or len(application_jobs) != 1:
        raise ArtifactError("application E2E job identity is incomplete")

    formats, formats_payload = _json_artifact(run_dir / "formats-e2e.json")
    format_jobs = formats.get("rehearsal_job_ids")
    submitted = formats.get("jobs_submitted")
    if (
        formats.get("ok") is not True
        or formats.get("rehearsal_account_closed") is not True
        or not isinstance(format_jobs, list)
        or isinstance(submitted, bool)
        or not isinstance(submitted, int)
        or submitted <= 0
        or len(format_jobs) != submitted
        or formats.get("celery_jobs_done") != submitted
        or formats.get("r2_results_reopened") != submitted
        or formats.get("job_metadata_removed") != submitted
        or formats.get("r2_residual_objects") != 0
    ):
        raise ArtifactError("format E2E artifact is not successful")
    format_ai_provider = _validate_format_coverage(formats, submitted)
    _validate_r2_payload_evidence(formats, submitted)

    purge, purge_payload = _json_artifact(run_dir / "e2e-purge.json")
    if (
        purge.get("ok") is not True
        or purge.get("rehearsal_users_removed") != 2
        or purge.get("rehearsal_jobs_purged")
        != len(application_jobs) + len(format_jobs)
    ):
        raise ArtifactError("purge E2E artifact is not successful")

    environment, environment_payload = _json_artifact(
        run_dir / "rehearsal-environment-proof.json"
    )
    environment_fields = {
        "format",
        "revision",
        "run_id",
        "production_api_worker_env_inherited",
        "production_database_env_inherited",
        "production_sensitive_overlap",
        "storage_credentials",
        "billing_provider",
        "email_provider",
        "instagram_provider",
        "ai_provider",
        "api_allowed_egress_hosts",
        "worker_allowed_egress_hosts",
        "api_keys",
        "worker_keys",
    }
    ai_provider = environment.get("ai_provider")
    api_hosts = environment.get("api_allowed_egress_hosts")
    worker_hosts = environment.get("worker_allowed_egress_hosts")
    api_keys = environment.get("api_keys")
    worker_keys = environment.get("worker_keys")
    if (
        set(environment) != environment_fields
        or environment.get("format")
        != "lecturesift-rehearsal-environment-proof-v1"
        or environment.get("revision") != revision
        or environment.get("run_id")
        != run_dir.name.replace("T", "").removesuffix("Z")
        or environment.get("production_api_worker_env_inherited") is not False
        or environment.get("production_database_env_inherited") is not False
        or environment.get("production_sensitive_overlap") is not False
        or environment.get("storage_credentials")
        != "distinct_pending_negative_capability"
        or environment.get("billing_provider") != "disabled"
        or environment.get("email_provider") != "disabled"
        or environment.get("instagram_provider") != "disabled"
        or ai_provider != "dedicated"
        or format_ai_provider != ai_provider
        or not isinstance(api_hosts, list)
        or len(api_hosts) != 1
        or not isinstance(api_hosts[0], str)
        or not api_hosts[0].endswith(".r2.cloudflarestorage.com")
        or worker_hosts
        != api_hosts + ["api.openai.com"]
        or not isinstance(api_keys, list)
        or not isinstance(worker_keys, list)
        or any(not isinstance(key, str) for key in api_keys + worker_keys)
        or api_keys != sorted(set(api_keys))
        or worker_keys != sorted(set(worker_keys))
        or "OPENAI_API_KEY" in api_keys
        or "OPENAI_API_KEY" not in worker_keys
    ):
        raise ArtifactError("rehearsal environment proof is not isolated")

    r2_capability, r2_capability_payload = _json_artifact(
        run_dir / "rehearsal-r2-negative-capability.json"
    )
    r2_capability_fields = {
        "format",
        "revision",
        "run_id",
        "rehearsal_list_control",
        "rehearsal_missing_object_control",
        "production_list_access",
        "production_object_read",
        "production_list_denial_code",
        "production_object_denial_code",
        "production_endpoint_host_sha256",
        "production_bucket_sha256",
        "rehearsal_endpoint_host_sha256",
        "rehearsal_bucket_sha256",
        "credentials_in_proof",
        "probe_wrote_objects",
    }
    if (
        set(r2_capability) != r2_capability_fields
        or r2_capability.get("format")
        != "lecturesift-rehearsal-r2-negative-capability-v1"
        or r2_capability.get("revision") != revision
        or r2_capability.get("run_id")
        != run_dir.name.replace("T", "").removesuffix("Z")
        or r2_capability.get("rehearsal_list_control") != "allowed"
        or r2_capability.get("rehearsal_missing_object_control") != "confirmed"
        or r2_capability.get("production_list_access") != "denied"
        or r2_capability.get("production_object_read") != "denied"
        or r2_capability.get("production_list_denial_code")
        not in {"AccessDenied", "InvalidAccessKeyId"}
        or r2_capability.get("production_object_denial_code")
        not in {"AccessDenied", "InvalidAccessKeyId"}
        or any(
            not isinstance(r2_capability.get(field), str)
            or not re.fullmatch(r"[0-9a-f]{64}", str(r2_capability.get(field)))
            for field in {
                "production_endpoint_host_sha256",
                "production_bucket_sha256",
                "rehearsal_endpoint_host_sha256",
                "rehearsal_bucket_sha256",
            }
        )
        or r2_capability.get("credentials_in_proof") is not False
        or r2_capability.get("probe_wrote_objects") is not False
    ):
        raise ArtifactError("rehearsal R2 negative-capability proof is invalid")

    values = {
        "application_e2e_sha256": _digest(application_payload),
        "environment_proof_sha256": _digest(environment_payload),
        "formats_e2e_sha256": _digest(formats_payload),
        "purge_e2e_sha256": _digest(purge_payload),
        "r2_negative_capability_sha256": _digest(r2_capability_payload),
        "schema_after_e2e_sha256": _digest(schema_after),
        "schema_transition_sha256": _digest(schema_transition),
        "target_after_e2e_manifest_sha256": _digest(after_safe_payload),
        "target_migrated_manifest_sha256": _digest(migrated_safe_payload),
    }
    aggregate = (
        f"rehearsal_result_format={FORMAT}\n"
        f"rehearsal_run_id={run_dir.name}\n"
        f"rehearsal_ai_provider={format_ai_provider}\n"
        + "".join(f"{key}={values[key]}\n" for key in sorted(values))
    ).encode()
    return {
        "rehearsal_result_format": FORMAT,
        "rehearsal_run_id": run_dir.name,
        "rehearsal_ai_provider": format_ai_provider,
        **values,
        "rehearsal_artifact_set_sha256": _digest(aggregate),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args(argv)
    try:
        values = validate(args.root, args.run_dir, args.revision)
    except (ArtifactError, schema_verifier.ContractError, OSError, UnicodeError) as exc:
        print(f"Rehearsal artifacts rejected: {exc}", file=sys.stderr)
        return 1
    for key in sorted(values):
        print(f"{key}={values[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
