from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
if str(DEPLOY) not in sys.path:
    sys.path.insert(0, str(DEPLOY))

import prove_rehearsal_r2_isolation as probe  # noqa: E402


REVISION = "a" * 40
RUN_ID = "20260831112233"
PRODUCTION_BUCKET = "lecturesift-production"
REHEARSAL_BUCKET = "lecturesift-rehearsal"
PROBE_KEY = ".lecturesift-capability-" + "b" * 48


def _inputs():
    runtime = {
        "S3_ENDPOINT_URL": "https://prod-account.eu.r2.cloudflarestorage.com",
        "S3_REGION": "auto",
        "S3_BUCKET": PRODUCTION_BUCKET,
        "S3_ACCESS_KEY_ID": "production-access",
        "S3_SECRET_ACCESS_KEY": "production-secret",
    }
    api: dict[str, str] = {}
    rehearsal = {
        "LECTURESIFT_REHEARSAL_S3_ENDPOINT_URL": (
            "https://rehearsal-account.eu.r2.cloudflarestorage.com"
        ),
        "LECTURESIFT_REHEARSAL_S3_REGION": "auto",
        "LECTURESIFT_REHEARSAL_S3_BUCKET": REHEARSAL_BUCKET,
        "LECTURESIFT_REHEARSAL_S3_ACCESS_KEY_ID": "rehearsal-access",
        "LECTURESIFT_REHEARSAL_S3_SECRET_ACCESS_KEY": "rehearsal-secret",
    }
    return runtime, api, rehearsal


def _transport(
    *, production_list=None, production_get=None, rehearsal_list=None,
    rehearsal_get=None
):
    answers = {
        (REHEARSAL_BUCKET, "list"): rehearsal_list
        or probe.ProbeResult(200, ""),
        (REHEARSAL_BUCKET, "get-missing"): rehearsal_get
        or probe.ProbeResult(404, "NoSuchKey"),
        (PRODUCTION_BUCKET, "list"): production_list
        or probe.ProbeResult(403, "AccessDenied"),
        (PRODUCTION_BUCKET, "get-missing"): production_get
        or probe.ProbeResult(403, "InvalidAccessKeyId"),
    }
    calls: list[tuple[str, str]] = []

    def request(endpoint, region, access, secret, bucket, operation, key):
        assert endpoint.startswith("https://")
        assert region == "auto"
        assert access == "rehearsal-access"
        assert secret == "rehearsal-secret"
        assert key == PROBE_KEY
        calls.append((bucket, operation))
        return answers[(bucket, operation)]

    return request, calls


def test_positive_controls_and_both_production_denials_create_secret_free_proof():
    runtime, api, rehearsal = _inputs()
    transport, calls = _transport()

    proof_document = probe.prove_isolation(
        runtime, api, rehearsal, revision=REVISION, run_id=RUN_ID,
        probe_key=PROBE_KEY, transport=transport,
    )

    assert calls == [
        (REHEARSAL_BUCKET, "list"),
        (REHEARSAL_BUCKET, "get-missing"),
        (PRODUCTION_BUCKET, "list"),
        (PRODUCTION_BUCKET, "get-missing"),
    ]
    assert proof_document["production_list_access"] == "denied"
    assert proof_document["production_object_read"] == "denied"
    assert proof_document["credentials_in_proof"] is False
    assert proof_document["probe_wrote_objects"] is False
    rendered = repr(proof_document)
    assert "rehearsal-access" not in rendered
    assert "rehearsal-secret" not in rendered
    assert f"'{PRODUCTION_BUCKET}'" not in rendered
    assert f"'{REHEARSAL_BUCKET}'" not in rendered


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"rehearsal_list": probe.ProbeResult(403, "AccessDenied")},
         "positive list control"),
        ({"rehearsal_get": probe.ProbeResult(403, "AccessDenied")},
         "positive object control"),
        ({"production_list": probe.ProbeResult(200, "")},
         "production bucket listing"),
        ({"production_get": probe.ProbeResult(404, "NoSuchKey")},
         "production object read"),
        ({"production_list": probe.ProbeResult(403, "SignatureDoesNotMatch")},
         "production bucket listing"),
        ({"production_get": probe.ProbeResult(500, "InternalError")},
         "production object read"),
    ],
)
def test_success_ambiguous_denial_and_errors_all_fail_closed(overrides, message):
    runtime, api, rehearsal = _inputs()
    transport, _ = _transport(**overrides)
    with pytest.raises(probe.R2IsolationError, match=message):
        probe.prove_isolation(
            runtime, api, rehearsal, revision=REVISION, run_id=RUN_ID,
            probe_key=PROBE_KEY, transport=transport,
        )


def test_distinct_strings_without_live_negative_capability_are_not_enough():
    runtime, api, rehearsal = _inputs()

    def unavailable(*_args):
        raise probe.R2IsolationError("R2 capability request could not be completed")

    with pytest.raises(probe.R2IsolationError, match="could not be completed"):
        probe.prove_isolation(
            runtime, api, rehearsal, revision=REVISION, run_id=RUN_ID,
            probe_key=PROBE_KEY, transport=unavailable,
        )


def test_exact_rehearsal_and_admission_bind_the_negative_capability_artifact():
    stack = (DEPLOY / "rehearsal_stack.sh").read_text(encoding="utf-8")
    restore = (DEPLOY / "rehearsal_restore.sh").read_text(encoding="utf-8")
    validator = (DEPLOY / "validate_rehearsal_artifacts.py").read_text(
        encoding="utf-8"
    )
    admission = (DEPLOY / "validate_rehearsal_admission.py").read_text(
        encoding="utf-8"
    )
    environment = (DEPLOY / "generate_rehearsal_envs.py").read_text(
        encoding="utf-8"
    )
    producer = (DEPLOY / "prove_rehearsal_r2_isolation.py").read_text(
        encoding="utf-8"
    )

    proof_call = stack.index('prove_rehearsal_r2_isolation.py"')
    first_candidate_container = stack.index("docker create --pull=never")
    assert proof_call < first_candidate_container
    assert "prove_rehearsal_r2_isolation.py" in restore
    assert "distinct_pending_negative_capability" in environment
    assert "r2_negative_capability_sha256" in validator
    assert "r2_negative_capability_sha256" in admission
    assert "rehearsal-r2-negative-capability.json" in validator
    assert "proof_details.st_uid != 0" in producer
    assert "stat.S_ISLNK(proof_details.st_mode)" in producer
    assert "stat.S_IMODE(proof_details.st_mode) not in {0o400, 0o600}" in producer
    assert 'run_id != run_dir.name.replace("T", "").removesuffix("Z")' in producer


def test_dotenv_parser_never_executes_shell_text():
    parsed = probe.parse_dotenv_text(
        "KEY='$(touch /tmp/r2-probe-must-not-run)'\n", label="test environment"
    )
    assert parsed == {"KEY": "$(touch /tmp/r2-probe-must-not-run)"}
