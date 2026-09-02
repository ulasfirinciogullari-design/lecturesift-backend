#!/usr/bin/env python3
"""Validate and canonicalize a LectureSift PostgreSQL security manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


VERSION = "v1"
_COMPLETE = re.compile(
    r"^SECURITY_COMPLETE\|v1\|([0-9]+)\|([0-9a-f]{32})$"
)


class ManifestError(RuntimeError):
    pass


def canonicalize(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ManifestError("security manifest is missing or unsafe")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ManifestError("security manifest is unreadable") from exc
    if len(lines) < 3 or lines[0] != f"SECURITY_MANIFEST|{VERSION}":
        raise ManifestError("security manifest header/version is missing")
    match = _COMPLETE.fullmatch(lines[-1])
    if not match:
        raise ManifestError("security manifest completion sentinel is missing")
    objects = lines[1:-1]
    if any(not line.startswith("SECURITY_OBJECT|") for line in objects):
        raise ManifestError("security manifest contains an unknown record family")
    if objects != sorted(objects) or len(objects) != len(set(objects)):
        raise ManifestError("security manifest objects are not canonical and unique")
    expected_count = int(match.group(1))
    if expected_count != len(objects) or expected_count == 0:
        raise ManifestError("security manifest completion count does not match")

    payloads: list[str] = []
    families: set[str] = set()
    expected_roles: list[dict[str, object]] = []
    coverage: list[dict[str, object]] = []
    for line in objects:
        payload = line.removeprefix("SECURITY_OBJECT|")
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ManifestError("security manifest contains malformed JSON") from exc
        if not isinstance(parsed, dict) or not isinstance(parsed.get("family"), str):
            raise ManifestError("security manifest contains an invalid object")
        payloads.append(payload)
        families.add(parsed["family"])
        if parsed["family"] == "expected_role":
            expected_roles.append(parsed)
        if parsed["family"] == "security_coverage":
            coverage.append(parsed)

    required = {
        "database",
        "database_acl",
        "default_acl",
        "expected_role",
        "extension",
        "column_acl",
        "relation",
        "relation_acl",
        "role",
        "role_setting",
        "schema",
        "schema_acl",
        "security_coverage",
        "tablespace",
        "tablespace_acl",
        "type",
        "type_acl",
        "view_definition",
    }
    if not required.issubset(families):
        raise ManifestError("security manifest is missing a required object family")
    if (
        len(expected_roles) != 3
        or {record.get("kind") for record in expected_roles}
        != {"owner", "api", "worker"}
        or any(record.get("present") is not True for record in expected_roles)
        or len({record.get("name") for record in expected_roles}) != 3
    ):
        raise ManifestError("security manifest does not prove all distinct expected roles")
    required_coverage = {
        "contract",
        "family",
        "large_objects",
        "parameter_acl_rows",
        "tablespaces",
        "types",
    }
    if (
        len(coverage) != 1
        or set(coverage[0]) != required_coverage
        or coverage[0].get("contract") != "postgres-security-v1"
        or any(
            isinstance(coverage[0].get(field), bool)
            or not isinstance(coverage[0].get(field), int)
            or int(coverage[0][field]) < 0
            for field in (
                "large_objects",
                "parameter_acl_rows",
                "tablespaces",
                "types",
            )
        )
    ):
        raise ManifestError("security manifest coverage record is invalid")
    computed = hashlib.md5(
        "\n".join(payloads).encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    if computed != match.group(2):
        raise ManifestError("security manifest completion digest does not match")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    args = parser.parse_args(argv)
    try:
        sys.stdout.write(canonicalize(Path(args.manifest)))
        return 0
    except ManifestError as exc:
        print(f"PostgreSQL security manifest rejected: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
