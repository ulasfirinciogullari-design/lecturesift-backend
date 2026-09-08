"""Explicit referral schema entry point; deliberately blocked in this preview.

Never invoked by normal API/worker startup or the existing v3 owner migration.
See REFERRAL_RELEASE_GATES.md before promoting the release capability.
"""
from __future__ import annotations

import argparse

from lecturesift import billing_service, referrals


def migrate(*, confirm: bool) -> None:
    if not confirm or not referrals.SCHEMA_RECOVERY_RELEASE_READY:
        raise RuntimeError(
            "Referral DDL is blocked until versioned schema/recovery/role contracts "
            "and PostgreSQL 18 restore evidence are reviewed in a release."
        )
    # This branch becomes reachable only in the subsequent reviewed release.
    with billing_service.ENGINE.begin() as connection:
        referrals.METADATA.create_all(connection)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-referral-schema-v1", action="store_true")
    args = parser.parse_args()
    migrate(confirm=args.confirm_referral_schema_v1)


if __name__ == "__main__":
    main()
