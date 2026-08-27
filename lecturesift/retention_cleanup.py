"""Daily deletion of expired final ZIPs and scheduled account data."""

from __future__ import annotations

from .commerce import anonymize_account, due_account_deletions, expired_output_rows, mark_output_expired
from .jobs import JOBS
from .storage import STORAGE


def run_cleanup() -> dict:
    outputs = 0
    accounts = 0
    for item in expired_output_rows():
        try:
            STORAGE.delete_key(item["key"])
            mark_output_expired(item["job_id"])
            outputs += 1
        except Exception:
            continue
    for user_id in due_account_deletions():
        try:
            for key in anonymize_account(user_id):
                STORAGE.delete_key(key)
            accounts += 1
        except Exception:
            continue
    return {
        "expired_outputs_deleted": outputs,
        "accounts_anonymized": accounts,
        "local_jobs_cleaned": JOBS.cleanup_expired(),
    }


def main() -> None:
    print(run_cleanup(), flush=True)


if __name__ == "__main__":
    main()
