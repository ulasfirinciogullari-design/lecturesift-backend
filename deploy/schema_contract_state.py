#!/usr/bin/env python3
"""Choose the exact frozen schema-contract family of a live database.

The cutover, seed, finalize, first-start, rehearsal and rollback tools must
decide between the historical v3 core inventory and the v4 product or v5
workspace contracts from the database itself, never from an environment
label. The public table set must equal exactly one family's EXPECTED_TABLES;
a partial, extra or unknown set is rejected. Detection only selects the
family: every caller still strict-verifies the complete manifest with the
selected verifier before it relies on that database.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Final


def _sibling_verifier(version: int) -> ModuleType:
    # Load the frozen verifier next to this file by path, so the table sets
    # cannot drift and neither script nor package import order matters.
    path = Path(__file__).resolve().with_name(f"verify_schema_transition_v{version}.py")
    spec = importlib.util.spec_from_file_location(
        f"lecturesift_schema_contract_state_v{version}", path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"frozen schema verifier v{version} is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contract_v3 = _sibling_verifier(3)
contract_v4 = _sibling_verifier(4)
contract_v5 = _sibling_verifier(5)
INVENTORY_VERSION: Final = "v1"
_TABLE_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")
_UNSIGNED_INTEGER = re.compile(r"0|[1-9][0-9]*")


@dataclass(frozen=True)
class Route:
    """Every frozen file family that belongs to one schema contract."""

    manifest: str
    verifier: str
    manifest_complete: str
    recovery_manifest: str
    recovery_manifest_version: int
    application_schema_compatibility: str
    product_schema_version: int


TABLE_SETS: Final = {
    3: contract_v3.EXPECTED_TABLES,
    4: contract_v4.EXPECTED_TABLES,
    5: contract_v5.EXPECTED_TABLES,
}
# recovery_manifest_v3.sql/v4.sql are the strict recovery forms of the v4/v5
# rehearsal manifests and therefore carry those verifier versions. The v3 core
# family is routed only for the core-only OVH target and the rehearsal host.
ROUTES: Final = {
    3: Route(
        manifest="rehearsal_manifest_v3.sql",
        verifier="verify_schema_transition_v3.py",
        manifest_complete="v3",
        recovery_manifest="recovery_manifest_v2.sql",
        recovery_manifest_version=2,
        application_schema_compatibility="lecturesift-schema-v2",
        product_schema_version=0,
    ),
    4: Route(
        manifest="rehearsal_manifest_v4.sql",
        verifier="verify_schema_transition_v4.py",
        manifest_complete="v4",
        recovery_manifest="recovery_manifest_v3.sql",
        recovery_manifest_version=3,
        application_schema_compatibility="lecturesift-schema-v3",
        product_schema_version=1,
    ),
    5: Route(
        manifest="rehearsal_manifest_v5.sql",
        verifier="verify_schema_transition_v5.py",
        manifest_complete="v5",
        recovery_manifest="recovery_manifest_v4.sql",
        recovery_manifest_version=4,
        application_schema_compatibility="lecturesift-schema-v4",
        product_schema_version=2,
    ),
}
# A provider cutover source must already run a product contract. The v3 core
# family predates the deployed Render schema and is never a valid source.
PROVIDER_SOURCE_VERSIONS: Final = frozenset({4, 5})
ALL_VERSIONS: Final = frozenset(ROUTES)


class SchemaStateError(RuntimeError):
    pass


def parse_inventory(path: Path) -> frozenset[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SchemaStateError("the table inventory is unreadable") from exc
    # psql may separate result sets with blank lines; nothing else is ignored.
    lines = [line.rstrip("\r") for line in text.splitlines() if line.rstrip("\r")]
    if not lines or not lines[-1].startswith("INVENTORY_COMPLETE|"):
        raise SchemaStateError("the table inventory lacks one terminal completion record")
    parts = lines[-1].split("|")
    if (
        len(parts) != 3
        or parts[1] != INVENTORY_VERSION
        or not _UNSIGNED_INTEGER.fullmatch(parts[2])
    ):
        raise SchemaStateError("the table inventory completion record is invalid")
    tables: list[str] = []
    for line in lines[:-1]:
        family, separator, name = line.partition("|")
        if family != "PUBLIC_TABLE" or not separator or not _TABLE_NAME.fullmatch(name):
            raise SchemaStateError("the table inventory contains an unexpected record")
        tables.append(name)
    if len(tables) != int(parts[2]) or len(set(tables)) != len(tables):
        raise SchemaStateError("the table inventory count or uniqueness is invalid")
    return frozenset(tables)


def classify(tables: frozenset[str]) -> int:
    matches = [version for version, expected in TABLE_SETS.items() if tables == expected]
    if len(matches) != 1:
        raise SchemaStateError(
            "the public table set is partial or matches no exact schema contract"
        )
    return matches[0]


def detect(path: Path, accepted: frozenset[int]) -> int:
    version = classify(parse_inventory(path))
    if version not in accepted:
        raise SchemaStateError(
            f"schema contract v{version} is not accepted for this operation"
        )
    return version


def manifest_contract_version(path: Path, accepted: frozenset[int]) -> int:
    """Return the contract named by a manifest's single terminal record."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise SchemaStateError("a manifest is unreadable") from exc
    completion = [line for line in lines if line.startswith("MANIFEST_COMPLETE|")]
    if len(completion) != 1:
        raise SchemaStateError("a manifest lacks one completion record")
    token = completion[0].split("|", 2)[1]
    matches = [
        version for version, route in ROUTES.items() if route.manifest_complete == token
    ]
    if len(matches) != 1 or matches[0] not in accepted:
        raise SchemaStateError("a manifest names an unaccepted schema contract")
    return matches[0]


def _accepted(value: str) -> frozenset[int]:
    try:
        versions = frozenset(int(item) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("accepted versions must be integers") from exc
    if not versions or not versions <= ALL_VERSIONS:
        raise argparse.ArgumentTypeError("accepted versions must be known contracts")
    return versions


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    detect_parser = commands.add_parser("detect")
    detect_parser.add_argument("--inventory", type=Path, required=True)
    detect_parser.add_argument("--accept", type=_accepted, required=True)
    manifest_parser = commands.add_parser("manifest-version")
    manifest_parser.add_argument("--manifest", type=Path, required=True)
    manifest_parser.add_argument("--accept", type=_accepted, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "detect":
            print(detect(args.inventory, args.accept))
        else:
            print(manifest_contract_version(args.manifest, args.accept))
    except SchemaStateError as exc:
        print(f"Schema contract state rejected: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
