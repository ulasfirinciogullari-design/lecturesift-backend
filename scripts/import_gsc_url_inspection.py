"""Import a user-provided Google URL Inspection API response without networking.

This validates the export's structure, not its authenticity or request context.
No canonical is inferred from another field, HTML, or the supplied request URL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


SITE_URL = "sc-domain:lecturesift.com"
MAX_INPUT_BYTES = 2_000_000
SOURCE = "user-provided Google URL Inspection API JSON"
SENSITIVE_KEYS = {
    "access_token", "refresh_token", "id_token", "client_secret", "authorization",
    "cookie", "set-cookie", "headers", "password", "private_key",
}


class InspectionImportError(ValueError):
    """The input cannot be safely represented as a native inspection result."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InspectionImportError("Duplicate JSON object keys are not supported")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise InspectionImportError("Non-finite JSON numbers are not supported")


def _reject_credentials(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in SENSITIVE_KEYS:
                raise InspectionImportError("Import only the API JSON body, without credentials or headers")
            _reject_credentials(child)
    elif isinstance(value, list):
        for child in value:
            _reject_credentials(child)


def _validate_context(site_url: str, inspection_url: str) -> None:
    if site_url != SITE_URL:
        raise InspectionImportError("--site-url must be sc-domain:lecturesift.com")
    try:
        parsed = urlsplit(inspection_url)
    except ValueError as error:
        raise InspectionImportError("--inspection-url must be a valid public URL") from error
    # Reject queries, fragments, escapes, dot segments, credentials and ports.
    # The spelling check also rejects an empty '?' or '#' and URL whitespace.
    plain_path = re.fullmatch(r"/(?:[a-z0-9-]+(?:\.html)?(?:/[a-z0-9-]+(?:\.html)?)*/*)?", parsed.path)
    if (parsed.scheme != "https" or parsed.netloc != "lecturesift.com"
            or not plain_path or "//" in parsed.path
            or inspection_url != f"https://lecturesift.com{parsed.path}"):
        raise InspectionImportError("--inspection-url must be a plain https://lecturesift.com/ path without query or fragment")


def build_report(input_path: Path, site_url: str, inspection_url: str) -> dict:
    """Read at most 2 MB and retain the native response's complete metadata."""
    _validate_context(site_url, inspection_url)
    if not input_path.is_file():
        raise InspectionImportError("--input must be a regular JSON file")
    with input_path.open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise InspectionImportError("Input JSON exceeds the 2 MB limit")
    try:
        response = json.loads(
            raw.decode("utf-8-sig"), object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise InspectionImportError("Input must be a valid UTF-8 JSON API response") from error
    if not isinstance(response, dict) or set(response) != {"inspectionResult"}:
        raise InspectionImportError("Expected only a native inspectionResult at the JSON root; API errors, HTTP wrappers and GSC Wizard summaries are not inspection results")
    result = response["inspectionResult"]
    if not isinstance(result, dict) or not isinstance(result.get("indexStatusResult"), dict):
        raise InspectionImportError("inspectionResult.indexStatusResult must be an object")
    try:
        _reject_credentials(response)
    except RecursionError as error:
        raise InspectionImportError("Input JSON nesting is too deep") from error
    status = result["indexStatusResult"]
    fields = {
        "googleCanonical": status,
        "userCanonical": status,
        "lastCrawlTime": status,
        "inspectionResultLink": result,
    }
    for name, container in fields.items():
        if name in container and not isinstance(container[name], str):
            raise InspectionImportError(f"{name} must be a string when present; null is not a missing field")
    return {
        "schema_version": 1,
        "source": SOURCE,
        "source_verification": "User-provided JSON; structural validation only, not a verified live Google API call or proof of origin.",
        "siteUrl": site_url,
        "inspectionUrl": inspection_url,
        "request_context_source": "CLI arguments supplied by the user; this response does not independently verify the inspected URL or property.",
        "importedAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        **{name: container.get(name) for name, container in fields.items()},
        "source_field_presence": {name: name in container for name, container in fields.items()},
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_response": response,
    }


def import_response(input_path: Path, site_url: str, inspection_url: str, output_path: Path) -> dict:
    report = build_report(input_path, site_url, inspection_url)
    try:
        encoded = (json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
    except UnicodeEncodeError as error:
        raise InspectionImportError("Input contains invalid Unicode text") from error
    # O_EXCL also refuses an existing symlink. Restrict access to the new export.
    descriptor = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--site-url", required=True)
    parser.add_argument("--inspection-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        import_response(args.input, args.site_url, args.inspection_url, args.output)
    except FileExistsError:
        parser.error("Output already exists; choose a new output file")
    except InspectionImportError as error:
        parser.error(str(error))
    except OSError:
        parser.error("Could not read the input or create the new output file")
    print("Imported user-provided JSON. No Google API request or live verification was performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
