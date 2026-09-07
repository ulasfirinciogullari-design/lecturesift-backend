from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "deploy" / "record_payment_provider_baseline.py"
SPEC = importlib.util.spec_from_file_location("record_payment_provider_baseline", TOOL_PATH)
assert SPEC and SPEC.loader
baseline_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(baseline_tool)

CUTOVER_ID = "2" * 32
REVISION = "3" * 40
NOW = datetime(2026, 9, 7, 11, tzinfo=timezone.utc)


def _proof() -> bytes:
    return (
        f"cutover_id={CUTOVER_ID}\n"
        f"release_revision={REVISION}\n"
        "status=provider-cutover-verified\n"
        "version=3\n"
    ).encode("ascii")


def _record(tmp_path: Path, runtime_text: str):
    runtime = tmp_path / "runtime.env"
    proof = tmp_path / "provider-cutover.ok"
    output = tmp_path / "payment-provider-baseline.json"
    runtime.write_text(runtime_text, encoding="utf-8")
    proof.write_bytes(_proof())
    result = baseline_tool.record_baseline(
        runtime,
        proof,
        output,
        expected_cutover_id=CUTOVER_ID,
        expected_revision=REVISION,
        now=NOW,
        enforce_filesystem=False,
    )
    return result, output, runtime, proof


def test_baseline_is_derived_without_persisting_credentials(tmp_path: Path):
    result, output, _, _ = _record(
        tmp_path,
        "IYZICO_API_KEY=iyzi-private\n"
        "IYZICO_SECRET_KEY=iyzi-secret\n"
        "PAYTR_MERCHANT_ID=merchant-1\n"
        "PAYTR_MERCHANT_KEY=paytr-private\n"
        "PAYTR_MERCHANT_SALT=paytr-salt\n",
    )

    assert result["configured_providers"] == ["iyzico", "paytr"]
    payload = output.read_text(encoding="utf-8")
    for secret in ("iyzi-private", "iyzi-secret", "paytr-private", "paytr-salt"):
        assert secret not in payload


def test_partial_provider_configuration_is_rejected(tmp_path: Path):
    with pytest.raises(baseline_tool.ProviderBaselineError, match="iyzico credential set is partial"):
        _record(tmp_path, "IYZICO_API_KEY=only-half\n")


def test_existing_baseline_is_immutable_and_idempotent(tmp_path: Path):
    result, output, runtime, proof = _record(
        tmp_path,
        "IYZICO_API_KEY=first-key\nIYZICO_SECRET_KEY=first-secret\n",
    )
    repeated = baseline_tool.record_baseline(
        runtime,
        proof,
        output,
        expected_cutover_id=CUTOVER_ID,
        expected_revision=REVISION,
        now=NOW,
        enforce_filesystem=False,
    )
    assert repeated == result

    runtime.write_text(
        "IYZICO_API_KEY=first-key\nIYZICO_SECRET_KEY=first-secret\n"
        "PAYTR_MERCHANT_ID=id\nPAYTR_MERCHANT_KEY=key\nPAYTR_MERCHANT_SALT=salt\n",
        encoding="utf-8",
    )
    with pytest.raises(
        baseline_tool.ProviderBaselineError,
        match="immutable provider baseline disagrees",
    ):
        baseline_tool.record_baseline(
            runtime,
            proof,
            output,
            expected_cutover_id=CUTOVER_ID,
            expected_revision=REVISION,
            now=NOW,
            enforce_filesystem=False,
        )


def test_check_mode_cannot_repair_a_missing_baseline(tmp_path: Path):
    runtime = tmp_path / "runtime.env"
    proof = tmp_path / "provider-cutover.ok"
    output = tmp_path / "payment-provider-baseline.json"
    runtime.write_text(
        "IYZICO_API_KEY=key\nIYZICO_SECRET_KEY=secret\n", encoding="utf-8"
    )
    proof.write_bytes(_proof())

    with pytest.raises(
        baseline_tool.ProviderBaselineError, match="provider baseline is missing"
    ):
        baseline_tool.record_baseline(
            runtime,
            proof,
            output,
            expected_cutover_id=CUTOVER_ID,
            expected_revision=REVISION,
            now=NOW,
            enforce_filesystem=False,
            require_existing=True,
        )


def test_check_mode_keeps_original_census_when_provider_is_added_later(tmp_path: Path):
    result, output, runtime, proof = _record(
        tmp_path,
        "IYZICO_API_KEY=first-key\nIYZICO_SECRET_KEY=first-secret\n",
    )
    runtime.write_text(
        "IYZICO_API_KEY=first-key\nIYZICO_SECRET_KEY=first-secret\n"
        "PAYTR_MERCHANT_ID=id\nPAYTR_MERCHANT_KEY=key\nPAYTR_MERCHANT_SALT=salt\n",
        encoding="utf-8",
    )

    checked = baseline_tool.record_baseline(
        runtime,
        proof,
        output,
        expected_cutover_id=CUTOVER_ID,
        expected_revision=REVISION,
        now=NOW,
        enforce_filesystem=False,
        require_existing=True,
    )

    assert checked == result
    assert checked["configured_providers"] == ["iyzico"]


def test_cutover_finalizer_records_baseline_before_reporting_success():
    finalizer = (ROOT / "deploy" / "finalize_provider_cutover.sh").read_text(
        encoding="utf-8"
    )
    final_proof = finalizer.index('"$CUTOVER_EVIDENCE_TOOL" finalize')
    baseline = finalizer.index('"$PROVIDER_BASELINE_TOOL"', final_proof)
    success = finalizer.index("Provider cutover gate verified and recorded", baseline)
    assert final_proof < baseline < success

    preflight = (ROOT / "deploy" / "preflight.sh").read_text(encoding="utf-8")
    final_validation = preflight.index('"$CUTOVER_EVIDENCE_TOOL" validate-final')
    baseline_check = preflight.index(
        '"$PROVIDER_BASELINE_TOOL" --check', final_validation
    )
    first_start = preflight.index('"$CUTOVER_EVIDENCE_TOOL" first-start-status')
    assert final_validation < baseline_check < first_start
    assert "immutable pre-traffic payment-provider baseline is absent or changed" in preflight
