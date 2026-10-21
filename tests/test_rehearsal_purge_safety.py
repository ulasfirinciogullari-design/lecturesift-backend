from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "rehearsal_purge_e2e",
    ROOT / "deploy" / "rehearsal_purge_e2e.py",
)
assert SPEC and SPEC.loader
purge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = purge
SPEC.loader.exec_module(purge)


def _foreign_key(
    table_name: str,
    column_name: str,
    *,
    delete_action: str = "a",
    deferrable: bool = False,
    initially_deferred: bool = False,
    column_count: int = 1,
):
    return purge.UserForeignKey(
        table_name=table_name,
        column_name=column_name,
        constraint_name=f"{table_name}_{column_name}_fkey",
        delete_action=delete_action,
        deferrable=deferrable,
        initially_deferred=initially_deferred,
        column_count=column_count,
    )


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Connection:
    def __init__(self, rows):
        self._rows = rows
        self.statement = None

    def execute(self, statement):
        self.statement = str(statement)
        return _Rows(self._rows)


class _QueryResult:
    def __init__(self, *, rows=(), scalar=0):
        self._rows = rows
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar


class _TermReferenceConnection:
    def __init__(self, references, *, conflicts=0):
        self.references = references
        self.conflicts = conflicts
        self.calls = []

    def execute(self, statement, parameters):
        sql = str(statement)
        self.calls.append((sql, parameters))
        if "SELECT DISTINCT binding.reference" in sql:
            return _QueryResult(rows=self.references)
        return _QueryResult(scalar=self.conflicts)


def test_direct_user_foreign_key_inventory_captures_fail_closed_semantics():
    connection = _Connection(
        [
            (
                "billing_email_verifications",
                "user_id",
                "billing_email_verifications_user_id_fkey",
                "a",
                False,
                False,
                1,
            )
        ]
    )

    assert purge._direct_user_foreign_keys(connection) == [
        _foreign_key("billing_email_verifications", "user_id")
    ]
    assert "fk.confdeltype" in connection.statement
    assert "fk.condeferrable" in connection.statement
    assert "fk.condeferred" in connection.statement
    assert "cardinality(fk.conkey)" in connection.statement


def test_owner_only_user_foreign_key_is_exact_and_excluded_from_api_cleanup():
    assert purge.OWNER_ONLY_USER_FOREIGN_KEYS == frozenset(
        {("billing_email_verifications", "user_id")}
    )

    foreign_keys = [
        _foreign_key("billing_auth_tokens", "user_id"),
        _foreign_key("billing_email_verifications", "user_id"),
        _foreign_key("billing_subscriptions", "user_id"),
    ]

    assert purge._mutable_user_foreign_keys(foreign_keys) == [
        _foreign_key("billing_auth_tokens", "user_id"),
        _foreign_key("billing_subscriptions", "user_id"),
    ]


def test_owner_only_user_foreign_key_contract_must_be_present():
    with pytest.raises(
        purge.PurgeError,
        match="owner-only user foreign-key contract is missing",
    ):
        purge._mutable_user_foreign_keys(
            [_foreign_key("billing_auth_tokens", "user_id")]
        )


@pytest.mark.parametrize(
    "unsafe_foreign_key",
    [
        _foreign_key(
            "billing_email_verifications",
            "user_id",
            delete_action="c",
        ),
        _foreign_key(
            "billing_email_verifications",
            "user_id",
            deferrable=True,
        ),
        _foreign_key(
            "billing_email_verifications",
            "user_id",
            initially_deferred=True,
        ),
        _foreign_key(
            "billing_email_verifications",
            "user_id",
            column_count=2,
        ),
    ],
)
def test_owner_only_user_foreign_key_must_be_immediate_single_column_no_action(
    unsafe_foreign_key,
):
    with pytest.raises(
        purge.PurgeError,
        match="owner-only user foreign-key contract is unsafe",
    ):
        purge._mutable_user_foreign_keys([unsafe_foreign_key])


def test_duplicate_owner_only_user_foreign_key_is_rejected():
    owner_only = _foreign_key("billing_email_verifications", "user_id")

    with pytest.raises(
        purge.PurgeError,
        match="owner-only user foreign-key contract is ambiguous",
    ):
        purge._mutable_user_foreign_keys([owner_only, owner_only])


def test_purchase_term_references_are_bounded_to_all_rehearsal_user_sources():
    user_ids = ["00000000-0000-4000-8000-000000000001"]
    connection = _TermReferenceConnection(["ADMIN-ONE", "LS-ORDER-TWO"])

    assert purge._term_references_for_users(connection, user_ids) == [
        "ADMIN-ONE",
        "LS-ORDER-TWO",
    ]
    inventory_sql, inventory_parameters = connection.calls[0]
    assert "public.billing_subscriptions" in inventory_sql
    assert "public.billing_payment_orders" in inventory_sql
    assert "public.billing_manual_orders" in inventory_sql
    assert inventory_parameters == {"user_ids": user_ids}
    conflict_sql, conflict_parameters = connection.calls[1]
    assert "binding.user_id NOT IN" in conflict_sql
    assert conflict_parameters == {
        "references": ["ADMIN-ONE", "LS-ORDER-TWO"],
        "user_ids": user_ids,
    }


def test_purchase_term_reference_shared_with_another_user_fails_closed():
    connection = _TermReferenceConnection(["ADMIN-SHARED"], conflicts=1)

    with pytest.raises(
        purge.PurgeError,
        match="terms reference is also bound to another user",
    ):
        purge._term_references_for_users(
            connection,
            ["00000000-0000-4000-8000-000000000001"],
        )


class _Deleted:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _OptionalTableConnection:
    def __init__(self, present, *, remaining=None):
        self.present = present
        self.remaining = remaining or {}
        self.calls = []

    def execute(self, statement, parameters):
        sql = str(statement)
        self.calls.append((sql, parameters))
        if "pg_catalog.pg_class" in sql:
            return _QueryResult(rows=[name for name in parameters["names"] if name in self.present])
        table, column = re.search(r"public\.(\w+) WHERE (\w+) IN", sql).groups()
        if sql.startswith("DELETE"):
            return _Deleted(1)
        return _QueryResult(scalar=self.remaining.get((table, column), 0))


USERS = ["00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"]
JOBS = ["00000000-0000-4000-8000-0000000000aa"]


def test_core_clone_skips_every_absent_product_and_workspace_table():
    connection = _OptionalTableConnection(set())
    assert purge._purge_optional_tables(connection, USERS, JOBS) == {}
    assert len(connection.calls) == 1
    assert connection.calls[0][1] == {"names": list(purge.OPTIONAL_USER_TABLES)}


def test_product_clone_purges_only_its_present_assistant_tables():
    connection = _OptionalTableConnection(
        {"assistant_credit_grants_v1", "assistant_credit_requests_v1"}
    )
    assert purge._purge_optional_tables(connection, USERS, JOBS) == {
        "assistant_credit_grants_v1": 1,
        "assistant_credit_requests_v1": 1,
    }
    deletes = [sql for sql, _ in connection.calls if sql.startswith("DELETE")]
    assert len(deletes) == 2
    assert all("WHERE user_id IN" in sql for sql in deletes)
    assert not any("workspace" in sql for sql, _ in connection.calls[1:])


def test_workspace_clone_purges_items_before_folders_and_checks_jobs():
    connection = _OptionalTableConnection(set(purge.OPTIONAL_USER_TABLES))
    assert set(purge._purge_optional_tables(connection, USERS, JOBS)) == set(
        purge.OPTIONAL_USER_TABLES
    )
    deletes = [sql for sql, _ in connection.calls if sql.startswith("DELETE")]
    tables = [re.search(r"public\.(\w+)", sql).group(1) for sql in deletes]
    assert tables.index("workspace_items_v1") < tables.index("workspace_folders_v1")
    job_checks = {
        re.search(r"public\.(\w+) WHERE job_id IN", sql).group(1): parameters
        for sql, parameters in connection.calls
        if " WHERE job_id IN" in sql
    }
    # Items and feedback are keyed by job as well; guest feedback has no user.
    assert job_checks == {
        "workspace_items_v1": {"values": JOBS},
        "lecturesift_job_feedback": {"values": JOBS},
    }


@pytest.mark.parametrize(
    "remaining",
    [
        ("admin_order_archives_v1", "user_id"),
        ("workspace_items_v1", "job_id"),
        ("lecturesift_job_feedback", "job_id"),
    ],
)
def test_optional_table_residue_fails_closed(remaining):
    connection = _OptionalTableConnection(
        set(purge.OPTIONAL_USER_TABLES), remaining={remaining: 1}
    )
    with pytest.raises(purge.PurgeError, match=f"rehearsal rows remain in {remaining[0]}"):
        purge._purge_optional_tables(connection, USERS, JOBS)


# Real PostgreSQL 18 proof on the allocated synthetic CI service only.
from test_product_schema_release import client, database  # noqa: E402,F401
from test_workspace_schema_release import current, migrate  # noqa: E402,F401
from test_feedback_schema_release import migrate as migrate_feedback  # noqa: E402


def test_real_product_and_workspace_clones_purge_only_rehearsal_rows(current, tmp_path):
    from sqlalchemy import text

    engine, name, _ = current
    other = "00000000-0000-4000-8000-0000000000ff"
    grant = (
        "INSERT INTO assistant_credit_grants_v1 VALUES "
        "(:id, :uid, 'ADMIN-synthetic', 'admin', 5, 5, now() + interval '1 day', now())"
    )
    with engine.begin() as connection:
        # The retained grant must belong to a real user, or the strict
        # manifest the workspace release runs next reports it as an anomaly.
        connection.execute(
            text(
                "INSERT INTO billing_users (id,email,password_salt,password_hash,credit_minutes,created_at) "
                "VALUES (:id,'retained@example.invalid','x','y',0,now())"
            ),
            dict(id=other),
        )
        for index, uid in enumerate((*USERS, other)):
            connection.execute(text(grant), dict(id=f"grant-{index}", uid=uid))
    with engine.begin() as connection:
        assert purge._purge_optional_tables(connection, USERS, JOBS) == {
            "assistant_credit_grants_v1": 2,
            "assistant_credit_requests_v1": 0,
        }

    assert migrate(name, tmp_path / "workspace") == 4
    with engine.begin() as connection:
        for uid, job, folder in ((USERS[0], JOBS[0], "folder-a"), (other, "other-job", "folder-b")):
            connection.execute(
                text("INSERT INTO workspace_folders_v1 VALUES (:folder, :uid, 'Notes', now())"),
                dict(folder=folder, uid=uid),
            )
            connection.execute(
                text("INSERT INTO workspace_items_v1 VALUES (:job, :uid, :folder)"),
                dict(job=job, uid=uid, folder=folder),
            )
            connection.execute(
                text("INSERT INTO admin_ad_free_grants_v1 VALUES (:uid, true, now())"), dict(uid=uid)
            )
            connection.execute(
                text("INSERT INTO admin_order_archives_v1 VALUES (:ref, :uid, now())"),
                dict(ref="LS-" + folder, uid=uid),
            )
    assert migrate_feedback(name, tmp_path / "feedback") == 1
    with engine.begin() as connection:
        for uid, job in ((USERS[0], JOBS[0]), (other, "other-job")):
            connection.execute(
                text(
                    "INSERT INTO lecturesift_job_feedback VALUES "
                    "(:id, :job, :uid, 'up', NULL, false, 'tr', now(), now())"
                ),
                dict(id="feedback-" + job, job=job, uid=uid),
            )
    with engine.begin() as connection:
        deleted = purge._purge_optional_tables(connection, USERS, JOBS)
    assert {table: count for table, count in deleted.items() if count} == {
        "workspace_items_v1": 1,
        "workspace_folders_v1": 1,
        "admin_ad_free_grants_v1": 1,
        "admin_order_archives_v1": 1,
        "lecturesift_job_feedback": 1,
    }
    with engine.connect() as connection:
        for table in purge.OPTIONAL_USER_TABLES:
            if table == "assistant_credit_requests_v1":
                continue
            assert connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE user_id = :uid"), dict(uid=other)
            ).scalar_one() == 1
