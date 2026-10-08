import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_gsc_url_inspection.py"
SPEC = importlib.util.spec_from_file_location("import_gsc_url_inspection", SCRIPT)
importer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(importer)
URL = "https://lecturesift.com/quiz-flashcards"


def _input(tmp_path, response):
    path = tmp_path / "response.json"
    path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("with_bom", [False, True])
def test_native_fields_and_additional_google_metadata_are_preserved(tmp_path, with_bom):
    response = {"inspectionResult": {
        "inspectionResultLink": "https://search.google.com/search-console/inspect?resource_id=sc-domain:lecturesift.com&id=example",
        "indexStatusResult": {
            "verdict": "NEUTRAL", "coverageState": "Alternate page with proper canonical tag",
            "googleCanonical": "https://lecturesift.com/quiz-flashcards.html",
            "userCanonical": URL, "lastCrawlTime": "2026-08-29T21:42:44Z",
            "sitemap": ["https://lecturesift.com/sitemap.xml"],
            "referringUrls": ["https://lecturesift.com/features"],
        },
        "richResultsResult": {"verdict": "PASS", "detectedItems": [{"richResultType": "Breadcrumbs", "items": []}]},
    }}
    output = tmp_path / "report.json"
    input_path = _input(tmp_path, response)
    if with_bom:
        input_path.write_bytes(b"\xef\xbb\xbf" + input_path.read_bytes())
    original_bytes = input_path.read_bytes()
    report = importer.import_response(input_path, importer.SITE_URL, URL, output)
    assert report["raw_response"] == response
    for name in ("googleCanonical", "userCanonical", "lastCrawlTime"):
        assert report[name] == response["inspectionResult"]["indexStatusResult"][name]
    assert report["inspectionResultLink"] == response["inspectionResult"]["inspectionResultLink"]
    assert all(report["source_field_presence"].values())
    assert report["source"] == "user-provided Google URL Inspection API JSON"
    assert report["importedAtUtc"] != report["lastCrawlTime"]
    assert "not a verified live" in report["source_verification"]
    assert "CLI arguments" in report["request_context_source"]
    assert report["source_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert json.loads(output.read_text()) == report


@pytest.mark.parametrize("status,link,expected_google,expected_user", [
    ({}, None, None, None),
    ({"googleCanonical": "", "userCanonical": URL}, "", "", URL),
    ({"userCanonical": URL}, None, None, URL),
])
def test_missing_and_empty_fields_do_not_backfill_each_other(tmp_path, status, link, expected_google, expected_user):
    result = {"indexStatusResult": status}
    if link is not None:
        result["inspectionResultLink"] = link
    report = importer.build_report(_input(tmp_path, {"inspectionResult": result}), importer.SITE_URL, URL)
    assert report["googleCanonical"] == expected_google
    assert report["userCanonical"] == expected_user
    assert report["inspectionResultLink"] == link
    assert report["source_field_presence"] == {
        "googleCanonical": "googleCanonical" in status,
        "userCanonical": "userCanonical" in status,
        "lastCrawlTime": False,
        "inspectionResultLink": link is not None,
    }
    assert report["raw_response"] == {"inspectionResult": result}


@pytest.mark.parametrize("response", [
    {"url": URL, "verdict": "PASS", "coverageState": "Submitted and indexed"},
    {"error": {"code": 403, "message": "Permission denied"}},
    {"inspectionResult": {"indexStatusResult": {}}, "headers": {"authorization": "redacted-test-value"}},
    {"inspectionResult": {"coverageState": "Submitted and indexed"}},
    {"inspectionResult": {"indexStatusResult": {"googleCanonical": None}}},
    {"inspectionResult": {"indexStatusResult": {"userCanonical": 123}}},
    {"inspectionResult": {"indexStatusResult": {}, "inspectionResultLink": None}},
    {"inspectionResult": {"indexStatusResult": {}, "access_token": "redacted-test-value"}},
])
def test_summaries_api_errors_wrappers_and_invalid_native_fields_are_rejected(tmp_path, response):
    output = tmp_path / "report.json"
    with pytest.raises(importer.InspectionImportError):
        importer.import_response(_input(tmp_path, response), importer.SITE_URL, URL, output)
    assert not output.exists()


@pytest.mark.parametrize("site_url,url", [
    ("https://lecturesift.com/", URL),
    (importer.SITE_URL, "https://lecturesift.com/quiz-flashcards?token=example"),
    (importer.SITE_URL, "https://lecturesift.com/quiz-flashcards#example"),
    (importer.SITE_URL, "https://lecturesift.com/quiz-flashcards?"),
    (importer.SITE_URL, "https://lecturesift.com:443/quiz-flashcards"),
    (importer.SITE_URL, "https://user@lecturesift.com/quiz-flashcards"),
    (importer.SITE_URL, "https://lecturesift.com/%71uiz-flashcards"),
    (importer.SITE_URL, "https://lecturesift.com/../quiz-flashcards"),
])
def test_request_context_rejects_other_properties_and_non_plain_urls(tmp_path, site_url, url):
    path = _input(tmp_path, {"inspectionResult": {"indexStatusResult": {}}})
    with pytest.raises(importer.InspectionImportError):
        importer.build_report(path, site_url, url)


def test_input_bound_ambiguous_json_and_existing_output_are_not_accepted(tmp_path):
    path = tmp_path / "response.json"
    path.write_bytes(b" " * (importer.MAX_INPUT_BYTES + 1))
    with pytest.raises(importer.InspectionImportError, match="2 MB"):
        importer.build_report(path, importer.SITE_URL, URL)
    path.write_text('{"inspectionResult":{"indexStatusResult":{"googleCanonical":"first","googleCanonical":"second"}}}')
    with pytest.raises(importer.InspectionImportError, match="Duplicate"):
        importer.build_report(path, importer.SITE_URL, URL)
    path = _input(tmp_path, {"inspectionResult": {"indexStatusResult": {}}})
    output = tmp_path / "existing.json"
    output.write_text("keep this evidence unchanged")
    with pytest.raises(FileExistsError):
        importer.import_response(path, importer.SITE_URL, URL, output)
    assert output.read_text() == "keep this evidence unchanged"
