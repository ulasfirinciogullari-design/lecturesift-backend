#!/usr/bin/env python3
"""Validate/canonicalize password-free PostgreSQL role login evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


VERSION = "v1"
_ROLE = re.compile(r"[a-z_][a-z0-9_]{0,62}")


class ProbeError(RuntimeError):
    pass


def _strict_json(text: str) -> dict[str, object]:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ProbeError("role login evidence contains duplicate JSON fields")
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=no_duplicates)
    except json.JSONDecodeError as exc:
        raise ProbeError("role login evidence contains malformed JSON") from exc
    if not isinstance(value, dict):
        raise ProbeError("role login evidence JSON is not an object")
    return value


def canonicalize(
    path: Path, *, database: str, owner_user: str, api_user: str, worker_user: str
) -> str:
    if not path.is_file() or path.is_symlink():
        raise ProbeError("role login evidence is missing or unsafe")
    expected_users = {"owner": owner_user, "api": api_user, "worker": worker_user}
    if (
        _ROLE.fullmatch(database) is None
        or any(_ROLE.fullmatch(user) is None for user in expected_users.values())
        or len(set(expected_users.values())) != 3
    ):
        raise ProbeError("expected PostgreSQL database/roles are invalid or not distinct")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ProbeError("role login evidence is unreadable") from exc
    if (
        len(lines) != 5
        or lines[0] != f"ROLE_LOGIN_MANIFEST|{VERSION}"
        or lines[-1] != f"ROLE_LOGIN_COMPLETE|{VERSION}|3"
        or any(not line.startswith("ROLE|") for line in lines[1:-1])
    ):
        raise ProbeError("role login evidence has an invalid envelope or count")
    required = {
        "kind",
        "current_user",
        "session_user",
        "database",
        "server_version_num",
        "login",
        "superuser",
        "inherit",
        "createdb",
        "createrole",
        "replication",
        "bypassrls",
        "connect",
        "temporary",
        "public_create",
        "search_path",
        "transaction_read_only",
    }
    records: dict[str, dict[str, object]] = {}
    server_versions: set[str] = set()
    for line in lines[1:-1]:
        record = _strict_json(line.removeprefix("ROLE|"))
        if set(record) != required:
            raise ProbeError("role login evidence fields are not exact")
        kind = record.get("kind")
        if not isinstance(kind, str) or kind not in expected_users or kind in records:
            raise ProbeError("role login evidence kind is missing or duplicated")
        expected_user = expected_users[kind]
        version = record.get("server_version_num")
        boolean_fields = {
            "login",
            "superuser",
            "inherit",
            "createdb",
            "createrole",
            "replication",
            "bypassrls",
            "connect",
            "temporary",
            "public_create",
            "transaction_read_only",
        }
        if (
            record.get("current_user") != expected_user
            or record.get("session_user") != expected_user
            or record.get("database") != database
            or not isinstance(version, str)
            or re.fullmatch(r"18[0-9]{4}", version) is None
            or any(not isinstance(record.get(field), bool) for field in boolean_fields)
            or not isinstance(record.get("search_path"), str)
            or not record["search_path"]
            or any(character.isspace() for character in record["search_path"])
            or record.get("login") is not True
            or record.get("connect") is not True
            or record.get("transaction_read_only") is not True
        ):
            raise ProbeError("role login identity, database, version or read-only state is invalid")
        if kind in {"api", "worker"} and any(
            record.get(field) is not False
            for field in (
                "superuser",
                "createdb",
                "createrole",
                "replication",
                "bypassrls",
                "temporary",
                "public_create",
            )
        ):
            raise ProbeError("an application role has elevated PostgreSQL authority")
        server_versions.add(version)
        records[kind] = record
    if set(records) != set(expected_users) or len(server_versions) != 1:
        raise ProbeError("role login evidence is incomplete or spans server versions")
    version = next(iter(server_versions))
    database_record = json.dumps(
        {"database": database, "server_version_num": version},
        sort_keys=True,
        separators=(",", ":"),
    )
    role_lines = [
        "ROLE|" + json.dumps(records[kind], sort_keys=True, separators=(",", ":"))
        for kind in sorted(records)
    ]
    return "\n".join(
        [
            f"ROLE_LOGIN_MANIFEST|{VERSION}",
            f"DATABASE|{database_record}",
            *role_lines,
            f"ROLE_LOGIN_COMPLETE|{VERSION}|3",
            "",
        ]
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--database", required=True)
    parser.add_argument("--owner-user", required=True)
    parser.add_argument("--api-user", required=True)
    parser.add_argument("--worker-user", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        sys.stdout.write(
            canonicalize(
                args.path,
                database=args.database,
                owner_user=args.owner_user,
                api_user=args.api_user,
                worker_user=args.worker_user,
            )
        )
        return 0
    except ProbeError as exc:
        print(f"PostgreSQL role login evidence failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
