from __future__ import annotations

import importlib.util
from pathlib import Path
import stat
import sys
import zipfile

import pytest
from PIL import Image


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


def _complete_r2_evidence_report() -> dict[str, object]:
    report = _format_report("dedicated")
    evidence = {
        case_name: sorted(values)
        for case_name, values in artifacts.R2_PAYLOAD_EVIDENCE_BY_CASE.items()
    }
    report["r2_payloads_verified_by_case"] = evidence
    report["r2_payloads_verified"] = sum(len(values) for values in evidence.values())
    return report


def test_admission_format_coverage_accepts_complete_dedicated_provider_state():
    report = _format_report("dedicated")
    assert (
        artifacts._validate_format_coverage(report, len(report["cases"]))
        == "dedicated"
    )


def test_admission_accepts_exact_case_bound_r2_payload_evidence():
    report = _complete_r2_evidence_report()
    assert artifacts._validate_r2_payload_evidence(report, len(report["cases"])) == 11


@pytest.mark.parametrize("reported", [5, 10, 12, True, "11", None])
def test_admission_rejects_inconsistent_r2_payload_totals(reported):
    report = _complete_r2_evidence_report()
    report["r2_payloads_verified"] = reported
    with pytest.raises(artifacts.ArtifactError, match="evidence total"):
        artifacts._validate_r2_payload_evidence(report, len(report["cases"]))


@pytest.mark.parametrize("case_name", sorted(artifacts.ALL_FORMAT_CASES))
def test_admission_rejects_missing_case_bound_r2_payload_evidence(case_name):
    report = _complete_r2_evidence_report()
    report["r2_payloads_verified_by_case"][case_name].pop()
    with pytest.raises(artifacts.ArtifactError, match="payload evidence"):
        artifacts._validate_r2_payload_evidence(report, len(report["cases"]))


def test_admission_rejects_extra_duplicate_and_unknown_r2_payload_evidence():
    duplicate = _complete_r2_evidence_report()
    duplicate["r2_payloads_verified_by_case"]["mp4_video"].append("pdf_sample")
    with pytest.raises(artifacts.ArtifactError, match="payload evidence"):
        artifacts._validate_r2_payload_evidence(duplicate, len(duplicate["cases"]))

    extra_label = _complete_r2_evidence_report()
    extra_label["r2_payloads_verified_by_case"]["native_documents"].append("other")
    extra_label["r2_payloads_verified"] = 12
    with pytest.raises(artifacts.ArtifactError, match="payload evidence"):
        artifacts._validate_r2_payload_evidence(extra_label, len(extra_label["cases"]))

    extra_case = _complete_r2_evidence_report()
    extra_case["r2_payloads_verified_by_case"]["unknown"] = ["pdf_sample"]
    with pytest.raises(artifacts.ArtifactError, match="evidence cases"):
        artifacts._validate_r2_payload_evidence(extra_case, len(extra_case["cases"]))


@pytest.mark.parametrize(
    ("case_name", "include_audio", "include_slide", "expected_evidence"),
    [
        ("native_documents", False, False, ["pdf_sample", "archive_zip"]),
        ("ocr_images", False, False, ["pdf_sample", "archive_zip"]),
        (
            "mp3_audio",
            True,
            False,
            ["pdf_sample", "archive_zip", "audio_mp3"],
        ),
        (
            "mp4_video",
            True,
            True,
            ["pdf_sample", "archive_zip", "audio_mp3", "slide_sample"],
        ),
    ],
)
def test_r2_payload_probe_reopens_zip_and_media_evidence(
    tmp_path: Path,
    monkeypatch,
    case_name,
    include_audio,
    include_slide,
    expected_evidence,
):
    job_id = "11111111-1111-4111-8111-111111111111"
    pdf_payload = b"%PDF-1.7\nrehearsal"
    audio_payload = b"ID3\x04rehearsal-audio"
    artifacts_manifest = [
        {
            "file": "Transcript.pdf",
            "format": "PDF",
            "size_bytes": len(pdf_payload),
        }
    ]
    if include_audio:
        artifacts_manifest.append(
            {
                "file": "LectureSift_Ders_Sesi.mp3",
                "format": "MP3",
                "size_bytes": len(audio_payload),
            }
        )
    result = {
        "artifacts": artifacts_manifest,
        "slides": [{"file": "slide-001.jpg"}] if include_slide else [],
    }
    archive_key = f"jobs/{job_id}/LectureSift_Study_Pack.zip"
    requested_keys: list[str] = []

    def materialize(selected_job_id: str, destination: Path, keys: list[str]) -> int:
        assert selected_job_id == job_id
        requested_keys.extend(keys)
        prefix = f"jobs/{job_id}/"
        for key in keys:
            relative = key[len(prefix) :]
            target = destination / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if key == archive_key:
                with zipfile.ZipFile(target, "w") as archive:
                    archive.writestr("Transcript.pdf", pdf_payload)
                    if include_audio:
                        archive.writestr("LectureSift_Ders_Sesi.mp3", audio_payload)
            elif key.endswith(".pdf"):
                target.write_bytes(pdf_payload)
            elif key.endswith(".mp3"):
                target.write_bytes(audio_payload)
            elif key.endswith(".jpg"):
                Image.new("RGB", (4, 4), "white").save(target, format="JPEG")
            else:
                raise AssertionError(f"unexpected R2 key: {key}")
        return len(keys)

    verified_audio: list[Path] = []
    monkeypatch.setattr(rehearsal.STORAGE, "materialize_files", materialize)
    monkeypatch.setattr(
        rehearsal,
        "_verify_mp3",
        lambda path: verified_audio.append(path),
    )

    evidence = rehearsal._verify_r2_payloads(
        case_name,
        job_id,
        result,
        tmp_path,
        archive_key,
    )

    assert evidence == expected_evidence
    assert archive_key in requested_keys
    assert any(key.endswith("/package/Transcript.pdf") for key in requested_keys)
    assert bool(verified_audio) is include_audio
    assert any(key.endswith("/slides/slide-001.jpg") for key in requested_keys) is include_slide


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../evil.pdf",
        r"..\evil.pdf",
        "/evil.pdf",
        r"C:\evil.pdf",
        r"\\server\share\evil.pdf",
        "nested/evil.pdf",
        r"nested\evil.pdf",
        "evil.pdf\x00ignored",
        "CON",
    ],
)
def test_r2_payload_probe_rejects_cross_platform_unsafe_artifact_names_before_download(
    tmp_path: Path,
    monkeypatch,
    unsafe_name,
):
    job_id = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setattr(
        rehearsal.STORAGE,
        "materialize_files",
        lambda *_args, **_kwargs: pytest.fail("unsafe names must fail before R2 download"),
    )
    with pytest.raises(RuntimeError, match="safe cross-platform leaf"):
        rehearsal._verify_r2_payloads(
            "native_documents",
            job_id,
            {
                "artifacts": [
                    {"file": unsafe_name, "format": "PDF", "size_bytes": 12}
                ],
                "slides": [],
            },
            tmp_path,
            f"jobs/{job_id}/LectureSift_Study_Pack.zip",
        )


@pytest.mark.parametrize(
    "unsafe_name",
    ["../../slide.jpg", r"..\slide.jpg", r"C:\slide.jpg", r"slides\slide.jpg"],
)
def test_r2_payload_probe_rejects_unsafe_slide_names_before_download(
    tmp_path: Path,
    monkeypatch,
    unsafe_name,
):
    job_id = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setattr(
        rehearsal.STORAGE,
        "materialize_files",
        lambda *_args, **_kwargs: pytest.fail("unsafe names must fail before R2 download"),
    )
    with pytest.raises(RuntimeError, match="safe cross-platform leaf"):
        rehearsal._verify_r2_payloads(
            "mp4_video",
            job_id,
            {
                "artifacts": [
                    {"file": "Transcript.pdf", "format": "PDF", "size_bytes": 12},
                    {"file": "Lecture.mp3", "format": "MP3", "size_bytes": 12},
                ],
                "slides": [{"file": unsafe_name}],
            },
            tmp_path,
            f"jobs/{job_id}/LectureSift_Study_Pack.zip",
        )


def test_r2_payload_probe_rejects_casefold_aliased_artifacts_before_download(
    tmp_path: Path,
    monkeypatch,
):
    job_id = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setattr(
        rehearsal.STORAGE,
        "materialize_files",
        lambda *_args, **_kwargs: pytest.fail("aliased names must fail before R2 download"),
    )
    with pytest.raises(RuntimeError, match="artifact manifest"):
        rehearsal._verify_r2_payloads(
            "native_documents",
            job_id,
            {
                "artifacts": [
                    {"file": "Transcript.pdf", "format": "PDF", "size_bytes": 12},
                    {"file": "transcript.PDF", "format": "TXT", "size_bytes": 12},
                ],
                "slides": [],
            },
            tmp_path,
            f"jobs/{job_id}/LectureSift_Study_Pack.zip",
        )


@pytest.mark.parametrize(
    ("member_names", "symlink_name"),
    [
        (["Transcript.pdf", r"..\evil.pdf"], None),
        (["Transcript.pdf", r"C:\evil.pdf"], None),
        (["Transcript.pdf", "transcript.PDF"], None),
        (["Transcript.pdf"], "Transcript.pdf"),
    ],
)
def test_r2_payload_probe_rejects_unsafe_or_aliased_zip_members(
    tmp_path: Path,
    monkeypatch,
    member_names,
    symlink_name,
):
    job_id = "11111111-1111-4111-8111-111111111111"
    archive_key = f"jobs/{job_id}/LectureSift_Study_Pack.zip"
    pdf_payload = b"%PDF-1.7\nrehearsal"

    def materialize(selected_job_id: str, destination: Path, keys: list[str]) -> int:
        assert selected_job_id == job_id
        for key in keys:
            relative = key.removeprefix(f"jobs/{job_id}/")
            target = destination / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if key == archive_key:
                with zipfile.ZipFile(target, "w") as archive:
                    for member_name in member_names:
                        info = zipfile.ZipInfo(member_name)
                        if member_name == symlink_name:
                            info.create_system = 3
                            info.external_attr = (stat.S_IFLNK | 0o777) << 16
                        archive.writestr(info, pdf_payload)
            else:
                target.write_bytes(pdf_payload)
        return len(keys)

    monkeypatch.setattr(rehearsal.STORAGE, "materialize_files", materialize)
    with pytest.raises(RuntimeError, match="ZIP"):
        rehearsal._verify_r2_payloads(
            "native_documents",
            job_id,
            {
                "artifacts": [
                    {
                        "file": "Transcript.pdf",
                        "format": "PDF",
                        "size_bytes": len(pdf_payload),
                    }
                ],
                "slides": [],
            },
            tmp_path,
            archive_key,
        )


def test_r2_payload_probe_rejects_backslash_archive_path_before_download(
    tmp_path: Path,
    monkeypatch,
):
    job_id = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setattr(
        rehearsal.STORAGE,
        "materialize_files",
        lambda *_args, **_kwargs: pytest.fail("unsafe archive path must fail before R2 download"),
    )
    with pytest.raises(RuntimeError, match="unsafe ZIP archive path"):
        rehearsal._verify_r2_payloads(
            "native_documents",
            job_id,
            {
                "artifacts": [
                    {"file": "Transcript.pdf", "format": "PDF", "size_bytes": 12}
                ],
                "slides": [],
            },
            tmp_path,
            f"jobs/{job_id}/package\\LectureSift_Study_Pack.zip",
        )


@pytest.mark.parametrize(
    "artifact_sizes",
    [
        [rehearsal._MAX_REHEARSAL_ZIP_MEMBER_BYTES + 1],
        [
            rehearsal._MAX_REHEARSAL_ZIP_MEMBER_BYTES,
            rehearsal._MAX_REHEARSAL_ZIP_MEMBER_BYTES,
            1,
        ],
    ],
)
def test_r2_payload_probe_rejects_oversized_manifest_before_download(
    tmp_path: Path,
    monkeypatch,
    artifact_sizes,
):
    job_id = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setattr(
        rehearsal.STORAGE,
        "materialize_files",
        lambda *_args, **_kwargs: pytest.fail("oversized manifest must fail before R2 download"),
    )
    manifest = [
        {
            "file": f"Artifact-{index}.{'pdf' if index == 0 else 'txt'}",
            "format": "PDF" if index == 0 else "TXT",
            "size_bytes": size,
        }
        for index, size in enumerate(artifact_sizes)
    ]
    with pytest.raises(RuntimeError, match="artifact manifest"):
        rehearsal._verify_r2_payloads(
            "native_documents",
            job_id,
            {"artifacts": manifest, "slides": []},
            tmp_path,
            f"jobs/{job_id}/LectureSift_Study_Pack.zip",
        )


@pytest.mark.parametrize("scenario", ["member_count", "member_size", "total_size"])
def test_r2_payload_probe_applies_zip_metadata_caps_before_crc_or_read(
    tmp_path: Path,
    monkeypatch,
    scenario,
):
    job_id = "11111111-1111-4111-8111-111111111111"
    archive_key = f"jobs/{job_id}/LectureSift_Study_Pack.zip"
    if scenario == "member_count":
        artifact_names = ["Transcript.pdf"]
        member_specs = [
            (f"Artifact-{index}.txt", 1)
            for index in range(rehearsal._MAX_REHEARSAL_ZIP_MEMBERS + 1)
        ]
    elif scenario == "member_size":
        artifact_names = ["Transcript.pdf"]
        member_specs = [
            ("Transcript.pdf", rehearsal._MAX_REHEARSAL_ZIP_MEMBER_BYTES + 1)
        ]
    else:
        artifact_names = ["Transcript.pdf", "Notes.txt", "Quiz.txt"]
        member_specs = [
            (name, 50 * 1024 * 1024)
            for name in artifact_names
        ]

    class FakeMember:
        def __init__(self, filename: str, file_size: int):
            self.filename = filename
            self.file_size = file_size
            self.external_attr = (stat.S_IFREG | 0o600) << 16

        def is_dir(self) -> bool:
            return False

    class FakeArchive:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def infolist(self):
            return [FakeMember(name, size) for name, size in member_specs]

        def testzip(self):
            pytest.fail("CRC must not run before ZIP metadata caps")

        def read(self, _name):
            pytest.fail("ZIP members must not be read before metadata caps")

    def materialize(selected_job_id: str, destination: Path, keys: list[str]) -> int:
        assert selected_job_id == job_id
        for key in keys:
            relative = key.removeprefix(f"jobs/{job_id}/")
            target = destination / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(
                b"%PDF-1.7\nplaceholder" if key.endswith(".pdf") else b"placeholder"
            )
        return len(keys)

    manifest = [
        {
            "file": name,
            "format": "PDF" if name.endswith(".pdf") else "TXT",
            "size_bytes": 1,
        }
        for name in artifact_names
    ]
    monkeypatch.setattr(rehearsal.STORAGE, "materialize_files", materialize)
    monkeypatch.setattr(rehearsal.zipfile, "ZipFile", lambda _path: FakeArchive())

    with pytest.raises(RuntimeError, match="ZIP"):
        rehearsal._verify_r2_payloads(
            "native_documents",
            job_id,
            {"artifacts": manifest, "slides": []},
            tmp_path,
            archive_key,
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
