from __future__ import annotations

import importlib.util
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
