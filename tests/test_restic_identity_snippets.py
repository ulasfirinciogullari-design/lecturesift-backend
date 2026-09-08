from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _python_snippet_after(path: str, anchor: str) -> str:
    tail = _read(path).split(anchor, 1)[1]
    start = tail.index("python3 -c '") + len("python3 -c '")
    return tail[start : tail.index("\n'", start)]


def _run_snippet(
    snippet: str, input_text: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", snippet, *arguments],
        input=input_text,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_preflight_repository_identity_snippet_accepts_a_real_id():
    snippet = _python_snippet_after(
        "deploy/preflight.sh", "CURRENT_REPOSITORY_ID_SHA256="
    )
    repository_id = "a" * 64

    result = _run_snippet(snippet, json.dumps({"id": repository_id}))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == hashlib.sha256(repository_id.encode("ascii")).hexdigest()


@pytest.mark.parametrize("payload", ({}, {"id": ""}))
def test_preflight_repository_identity_snippet_rejects_an_empty_id(payload: dict):
    snippet = _python_snippet_after(
        "deploy/preflight.sh", "CURRENT_REPOSITORY_ID_SHA256="
    )

    result = _run_snippet(snippet, json.dumps(payload))

    assert result.returncode != 0
    assert result.stdout == ""


def test_seed_current_key_hint_snippet_accepts_one_current_key():
    snippet = _python_snippet_after(
        "deploy/seed_first_cutover_backup.sh", "CURRENT_KEY_HINT="
    )
    key_hint = "ABCDEF0123456789"

    result = _run_snippet(
        snippet,
        json.dumps([{"id": key_hint, "current": True}]),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == key_hint.lower()


@pytest.mark.parametrize("payload", ([], [{"id": "", "current": True}]))
def test_seed_current_key_hint_snippet_rejects_an_empty_key(payload: list[dict]):
    snippet = _python_snippet_after(
        "deploy/seed_first_cutover_backup.sh", "CURRENT_KEY_HINT="
    )

    result = _run_snippet(snippet, json.dumps(payload))

    assert result.returncode != 0
    assert result.stdout == ""


def test_seed_full_key_id_snippet_accepts_one_matching_key():
    snippet = _python_snippet_after(
        "deploy/seed_first_cutover_backup.sh", "CURRENT_KEY_ID="
    )
    full_key_id = "abcdef0123456789" * 4

    result = _run_snippet(snippet, f"not-a-key\n{full_key_id}\n", full_key_id[:12])

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == full_key_id


@pytest.mark.parametrize("key_listing", ("", "not-a-key\n"))
def test_seed_full_key_id_snippet_rejects_an_empty_match(key_listing: str):
    snippet = _python_snippet_after(
        "deploy/seed_first_cutover_backup.sh", "CURRENT_KEY_ID="
    )

    result = _run_snippet(snippet, key_listing, "abcdef01")

    assert result.returncode != 0
    assert result.stdout == ""


def test_restic_identity_snippets_do_not_raise_a_conditional_none():
    for path in ("deploy/preflight.sh", "deploy/seed_first_cutover_backup.sh"):
        assert "raise SystemExit(1) if" not in _read(path)
