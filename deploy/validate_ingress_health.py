#!/usr/bin/env python3
"""Validate one bounded ingress health payload against an exact provider contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any


MAX_BYTES = 1024 * 1024
REVISION = re.compile(r"[0-9a-f]{40}")


class HealthContractError(RuntimeError):
    pass


def validate_health(
    payload: Any, *, expected_provider: str, expected_revision: str, expected_mode: str
) -> str:
    if expected_provider not in {"render", "ovh"}:
        raise HealthContractError("expected provider is invalid")
    if REVISION.fullmatch(expected_revision) is None:
        raise HealthContractError("expected revision is invalid")
    if expected_mode not in {"freeze", "off"}:
        raise HealthContractError("expected maintenance mode is invalid")
    if not isinstance(payload, dict):
        raise HealthContractError("health payload is not an object")
    revision = str(payload.get("revision") or "").lower()
    if (
        payload.get("ok") is not True
        or payload.get("deployment_provider") != expected_provider
        or payload.get("maintenance_mode") != expected_mode
        or revision != expected_revision
    ):
        raise HealthContractError("health payload does not match the exact provider contract")
    return revision


def _strict_document(raw: bytes) -> Any:
    if not raw or len(raw) > MAX_BYTES or b"\0" in raw:
        raise HealthContractError("health payload has an invalid size")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise HealthContractError("health payload contains duplicate fields")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HealthContractError("health payload is not strict JSON") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=("render", "ovh"))
    parser.add_argument("--revision", required=True)
    parser.add_argument("--maintenance-mode", required=True, choices=("freeze", "off"))
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        print(
            validate_health(
                _strict_document(raw),
                expected_provider=args.provider,
                expected_revision=args.revision,
                expected_mode=args.maintenance_mode,
            )
        )
        return 0
    except HealthContractError as exc:
        print(f"Ingress health proof failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
