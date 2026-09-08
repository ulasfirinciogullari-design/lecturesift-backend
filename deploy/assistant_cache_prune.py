"""Future release maintenance entry point; no DDL or chat activation.

Run from the repository root: python -m deploy.assistant_cache_prune
Provisioning a scheduler is a separate release operation. This process emits
counts only, never response text, account IDs, guest digests or credentials.
"""

import json

from lecturesift.assistant_wallet import prune_private_cache


def main():
    print(json.dumps(prune_private_cache(), sort_keys=True))


if __name__ == "__main__":
    main()
