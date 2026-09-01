"""Remove only proven E2E rows from a disposable migration rehearsal clone.

Normal account closure intentionally retains anonymised commerce, usage and
audit records.  A migration rehearsal additionally needs to prove the database
returns byte-for-byte to its pre-E2E manifest.  This script is therefore a
separate, hard-guarded cleanup primitive and must never be imported by the
application or run against a non-rehearsal database.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import uuid

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import Connection, make_url

from lecturesift import config


DATABASE_RE = re.compile(r"^lecturesift_rehearsal_[0-9]{14}$")
JOB_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
EMAIL_PATTERNS = (
    re.compile(r"^ovh-rehearsal-[0-9a-f]{32}@example[.]invalid$"),
    re.compile(r"^format-rehearsal-[0-9a-f]{32}@example[.]invalid$"),
)


class PurgeError(RuntimeError):
    pass


def _database_url() -> str:
    value = config.DATABASE_URL
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


def _read_summary(path: Path, *, kind: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PurgeError(f"invalid {kind} result file") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise PurgeError(f"{kind} did not produce an explicit successful result")
    if payload.get("rehearsal_account_closed") is not True:
        raise PurgeError(f"{kind} did not close its rehearsal account")
    return payload


def _uuid(value: object, *, label: str) -> str:
    candidate = str(value or "")
    try:
        parsed = uuid.UUID(candidate)
    except (ValueError, AttributeError) as exc:
        raise PurgeError(f"{label} is not a UUID") from exc
    if str(parsed) != candidate.lower() or parsed.version != 4:
        raise PurgeError(f"{label} is not a canonical UUIDv4")
    return candidate.lower()


def _email(value: object, *, label: str) -> str:
    candidate = str(value or "").lower()
    if not any(pattern.fullmatch(candidate) for pattern in EMAIL_PATTERNS):
        raise PurgeError(f"{label} is not a generated rehearsal address")
    return candidate


def _job_ids(value: object, *, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise PurgeError(f"{label} must contain at least one job id")
    jobs = [str(item or "").lower() for item in value]
    if len(jobs) != len(set(jobs)) or any(not JOB_RE.fullmatch(item) for item in jobs):
        raise PurgeError(f"{label} contains an invalid or duplicate job id")
    return jobs


def _delete_in(connection: Connection, sql: str, values: list[str]) -> int:
    if not values:
        return 0
    statement = text(sql).bindparams(bindparam("values", expanding=True))
    return int(connection.execute(statement, {"values": values}).rowcount or 0)


def _direct_user_foreign_keys(connection: Connection) -> list[tuple[str, str]]:
    rows = connection.execute(
        text(
            """
            SELECT c.relname, a.attname
            FROM pg_constraint fk
            JOIN pg_class c ON c.oid = fk.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN unnest(fk.conkey) WITH ORDINALITY AS key(attnum, ord) ON true
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = key.attnum
            WHERE fk.contype = 'f'
              AND fk.confrelid = 'public.billing_users'::regclass
              AND n.nspname = 'public'
            ORDER BY c.relname, a.attname
            """
        )
    ).all()
    if not rows:
        raise PurgeError("billing_users foreign-key inventory is unexpectedly empty")
    return [(str(row[0]), str(row[1])) for row in rows]


def _assert_no_matches(
    connection: Connection,
    foreign_keys: list[tuple[str, str]],
    user_ids: list[str],
    job_ids: list[str],
) -> None:
    preparer = connection.dialect.identifier_preparer
    for table_name, column_name in foreign_keys:
        table = preparer.quote(table_name)
        column = preparer.quote(column_name)
        remaining = connection.execute(
            text(f"SELECT count(*) FROM public.{table} WHERE {column} IN :values").bindparams(
                bindparam("values", expanding=True)
            ),
            {"values": user_ids},
        ).scalar_one()
        if int(remaining):
            raise PurgeError(f"rehearsal user rows remain in {table_name}")
    checks = (
        ("lecturesift_admin_account_events", "subject_user_id", user_ids),
        ("lecturesift_cost_events", "user_id", user_ids),
        ("lecturesift_cost_events", "job_id", job_ids),
        ("lecturesift_runtime_metrics", "job_id", job_ids),
    )
    for table_name, column_name, values in checks:
        remaining = connection.execute(
            text(
                f"SELECT count(*) FROM public.{table_name} "
                f"WHERE {column_name} IN :values"
            ).bindparams(bindparam("values", expanding=True)),
            {"values": values},
        ).scalar_one()
        if int(remaining):
            raise PurgeError(f"rehearsal marker rows remain in {table_name}")


def purge(application_result: Path, formats_result: Path) -> dict[str, object]:
    if os.getenv("LECTURESIFT_REHEARSAL") != "1":
        raise PurgeError("LECTURESIFT_REHEARSAL=1 is required")
    database_name = make_url(_database_url()).database or ""
    if not DATABASE_RE.fullmatch(database_name):
        raise PurgeError("refusing cleanup outside a timestamped rehearsal database")

    application = _read_summary(application_result, kind="application E2E")
    formats = _read_summary(formats_result, kind="format E2E")
    user_ids = [
        _uuid(application.get("rehearsal_user_id"), label="application user id"),
        _uuid(formats.get("rehearsal_user_id"), label="format user id"),
    ]
    emails = [
        _email(application.get("rehearsal_email"), label="application email"),
        _email(formats.get("rehearsal_email"), label="format email"),
    ]
    job_ids = _job_ids(application.get("rehearsal_job_ids"), label="application jobs")
    job_ids += _job_ids(formats.get("rehearsal_job_ids"), label="format jobs")
    if len(user_ids) != len(set(user_ids)) or len(job_ids) != len(set(job_ids)):
        raise PurgeError("rehearsal identities overlap unexpectedly")

    engine = create_engine(_database_url(), pool_pre_ping=True)
    deleted: dict[str, int] = {}
    try:
        with engine.begin() as connection:
            observed_database = str(connection.execute(text("SELECT current_database()")).scalar_one())
            if observed_database != database_name or not DATABASE_RE.fullmatch(observed_database):
                raise PurgeError("database identity changed after connection")
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SET LOCAL statement_timeout = '30s'"))

            users = connection.execute(
                text(
                    "SELECT id FROM public.billing_users "
                    "WHERE id IN :values AND email LIKE 'deleted+%@users.invalid'"
                ).bindparams(bindparam("values", expanding=True)),
                {"values": user_ids},
            ).scalars().all()
            if set(map(str, users)) != set(user_ids):
                raise PurgeError("E2E account identities are missing or were not anonymised")

            order_references = connection.execute(
                text(
                    "SELECT reference FROM public.billing_payment_orders "
                    "WHERE user_id IN :values"
                ).bindparams(bindparam("values", expanding=True)),
                {"values": user_ids},
            ).scalars().all()
            deleted["billing_payment_provider_sessions"] = _delete_in(
                connection,
                "DELETE FROM public.billing_payment_provider_sessions "
                "WHERE order_reference IN :values",
                [str(value) for value in order_references],
            )

            foreign_keys = _direct_user_foreign_keys(connection)
            preparer = connection.dialect.identifier_preparer
            for table_name, column_name in foreign_keys:
                table = preparer.quote(table_name)
                column = preparer.quote(column_name)
                deleted[table_name] = deleted.get(table_name, 0) + _delete_in(
                    connection,
                    f"DELETE FROM public.{table} WHERE {column} IN :values",
                    user_ids,
                )

            deleted["lecturesift_admin_account_events"] = _delete_in(
                connection,
                "DELETE FROM public.lecturesift_admin_account_events "
                "WHERE subject_user_id IN :values",
                user_ids,
            )
            deleted["lecturesift_cost_events_user"] = _delete_in(
                connection,
                "DELETE FROM public.lecturesift_cost_events WHERE user_id IN :values",
                user_ids,
            )
            deleted["lecturesift_cost_events_job"] = _delete_in(
                connection,
                "DELETE FROM public.lecturesift_cost_events WHERE job_id IN :values",
                job_ids,
            )
            deleted["lecturesift_runtime_metrics"] = _delete_in(
                connection,
                "DELETE FROM public.lecturesift_runtime_metrics WHERE job_id IN :values",
                job_ids,
            )
            deleted["billing_users"] = _delete_in(
                connection,
                "DELETE FROM public.billing_users WHERE id IN :values",
                user_ids,
            )
            if deleted["billing_users"] != len(user_ids):
                raise PurgeError("did not remove exactly the two proven rehearsal users")
            _assert_no_matches(connection, foreign_keys, user_ids, job_ids)

            # The generated registration addresses must no longer be present
            # in live identity columns. Audit rows use an unrelated anonymised
            # address and were removed by subject_user_id above.
            remaining_email = connection.execute(
                text("SELECT count(*) FROM public.billing_users WHERE email IN :values").bindparams(
                    bindparam("values", expanding=True)
                ),
                {"values": emails},
            ).scalar_one()
            if int(remaining_email):
                raise PurgeError("a generated rehearsal email remains")
    finally:
        engine.dispose()

    return {
        "ok": True,
        "database": database_name,
        "rehearsal_users_removed": len(user_ids),
        "rehearsal_jobs_purged": len(job_ids),
        "rows_deleted": dict(sorted(deleted.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--application-result", type=Path, required=True)
    parser.add_argument("--formats-result", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = purge(args.application_result, args.formats_result)
    except PurgeError as exc:
        print(f"Rehearsal purge refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
