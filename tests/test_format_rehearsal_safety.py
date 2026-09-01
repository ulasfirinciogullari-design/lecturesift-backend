from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "lecturesift_rehearsal_formats_e2e",
    ROOT / "deploy" / "rehearsal_formats_e2e.py",
)
assert SPEC and SPEC.loader
rehearsal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rehearsal)

DEPLOY = ROOT / "deploy"
if str(DEPLOY) not in sys.path:
    sys.path.insert(0, str(DEPLOY))
ARTIFACT_SPEC = importlib.util.spec_from_file_location(
    "lecturesift_validate_rehearsal_artifacts",
    DEPLOY / "validate_rehearsal_artifacts.py",
)
assert ARTIFACT_SPEC and ARTIFACT_SPEC.loader
artifacts = importlib.util.module_from_spec(ARTIFACT_SPEC)
ARTIFACT_SPEC.loader.exec_module(artifacts)


@pytest.mark.parametrize(
    ("job", "terminal"),
    [
        ({"status": "done", "queue_mode": "celery", "worker_state": "done"}, True),
        ({"status": "done", "queue_mode": "celery", "worker_state": "publishing"}, False),
        ({"status": "done", "queue_mode": "celery", "worker_state": "processing"}, False),
        ({"status": "error", "queue_mode": "celery", "worker_state": "failed"}, True),
        ({"status": "error", "queue_mode": "celery", "worker_state": "rejected"}, True),
        ({"status": "error", "queue_mode": "celery", "worker_state": "unavailable"}, True),
        ({"status": "error", "queue_mode": "celery", "worker_state": "retrying"}, False),
        ({"status": "queued", "queue_mode": "celery", "worker_state": "queued"}, False),
        ({"status": "done", "queue_mode": "inline"}, True),
        ({"status": "error", "queue_mode": "inline"}, True),
        ({}, False),
    ],
)
def test_rehearsal_cleanup_requires_durable_terminal_state(job, terminal):
    assert rehearsal._job_is_durably_terminal(job) is terminal


def _format_report(ai_provider: str) -> dict[str, object]:
    cases = (
        set(artifacts.ALL_FORMAT_CASES)
        if ai_provider == "dedicated"
        else set(artifacts.BASE_FORMAT_CASES)
    )
    return {
        "requested_cases": sorted(artifacts.ALL_FORMAT_CASES),
        "cases": sorted(cases),
        "formats": sorted(
            item for case in cases for item in artifacts.FORMATS_BY_CASE[case]
        ),
        "skipped_cases": (
            {}
            if ai_provider == "dedicated"
            else {
                case: "dedicated_rehearsal_openai_key_absent"
                for case in sorted(artifacts.AI_FORMAT_CASES)
            }
        ),
        "ai_provider_state": ai_provider,
        "ai_provider_tested": ai_provider == "dedicated",
    }


def test_admission_format_coverage_accepts_complete_dedicated_provider_state():
    report = _format_report("dedicated")
    assert (
        artifacts._validate_format_coverage(report, len(report["cases"]))
        == "dedicated"
    )


def test_absent_ai_provider_is_explicit_debug_evidence_but_never_admitted():
    report = _format_report("intentionally_absent")
    assert report["skipped_cases"] == {
        case: "dedicated_rehearsal_openai_key_absent"
        for case in sorted(artifacts.AI_FORMAT_CASES)
    }
    with pytest.raises(
        artifacts.ArtifactError,
        match="exact admission requires a dedicated rehearsal AI provider",
    ):
        artifacts._validate_format_coverage(report, len(report["cases"]))


def test_debug_format_subset_can_never_produce_an_admission():
    report = _format_report("dedicated")
    report["requested_cases"] = sorted(artifacts.BASE_FORMAT_CASES)
    with pytest.raises(artifacts.ArtifactError, match="debug format subset"):
        artifacts._validate_format_coverage(report, len(report["cases"]))


@pytest.mark.parametrize("missing_case", sorted(artifacts.ALL_FORMAT_CASES))
def test_admission_rejects_each_missing_required_format_case(missing_case):
    report = _format_report("dedicated")
    report["cases"].remove(missing_case)
    report["formats"] = sorted(
        item
        for case in report["cases"]
        for item in artifacts.FORMATS_BY_CASE[case]
    )
    with pytest.raises(artifacts.ArtifactError, match="required case coverage"):
        artifacts._validate_format_coverage(report, len(report["cases"]))


def test_absent_ai_provider_cannot_bypass_admission_with_incomplete_skip_evidence():
    report = _format_report("intentionally_absent")
    report["skipped_cases"].pop("mp4_video")
    with pytest.raises(
        artifacts.ArtifactError,
        match="exact admission requires a dedicated rehearsal AI provider",
    ):
        artifacts._validate_format_coverage(report, len(report["cases"]))
