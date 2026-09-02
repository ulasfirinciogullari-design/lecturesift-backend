from __future__ import annotations

import copy
import importlib.util
import io
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "deploy" / "generate_canonical_release_archive.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location(
        "lecturesift_canonical_release_archive_test",
        HELPER_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repository: Path, *arguments: str) -> bytes:
    git = shutil.which("git")
    assert git
    return subprocess.run(
        [git, "-c", f"safe.directory={repository.as_posix()}", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    ).stdout


def _repository_and_bundle(tmp_path: Path) -> tuple[Path, str, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.email", "canonical-test@example.invalid")
    _git(repository, "config", "user.name", "Canonical Archive Test")
    (repository / "plain.txt").write_bytes(b"first\nsecond\n")
    (repository / "run.sh").write_bytes(b"#!/bin/sh\nprintf 'ok\\n'\n")
    nested = repository / "nested"
    nested.mkdir()
    (nested / "data.txt").write_bytes(b"nested\n")
    _git(repository, "-c", "core.autocrlf=false", "add", "--all")
    _git(repository, "update-index", "--chmod=+x", "run.sh")
    _git(repository, "commit", "--quiet", "--no-gpg-sign", "-m", "fixture")
    revision = _git(repository, "rev-parse", "HEAD").decode("ascii").strip()
    bundle = tmp_path / "exact.bundle"
    _git(repository, "bundle", "create", str(bundle), "HEAD")
    return repository, revision, bundle


def _rewrite_transport(
    source: Path,
    target: Path,
    *,
    crlf: bool = False,
    change_exec: bool = False,
) -> None:
    with tarfile.open(source, "r:") as incoming, tarfile.open(target, "w:") as outgoing:
        for original in incoming:
            member = copy.copy(original)
            payload = None
            if original.isfile():
                extracted = incoming.extractfile(original)
                assert extracted is not None
                payload = extracted.read()
            if crlf and original.name == "source/plain.txt":
                assert payload is not None
                payload = payload.replace(b"\n", b"\r\n")
                member.size = len(payload)
            if change_exec and original.name == "source/plain.txt":
                member.mode |= 0o111
            outgoing.addfile(member, None if payload is None else io.BytesIO(payload))


def test_helper_contract_pins_git_config_and_safe_directory() -> None:
    source = HELPER_PATH.read_text(encoding="utf-8")
    for value in (
        "core.attributesFile=/dev/null",
        "core.autocrlf=false",
        "core.eol=lf",
        "tar.umask=0002",
    ):
        assert f'"{value}"' in source
    assert 'environment["GIT_CONFIG_NOSYSTEM"] = "1"' in source
    assert 'environment["GIT_CONFIG_GLOBAL"] = os.devnull' in source
    assert 'f"safe.directory={repository.as_posix()}"' in source
    assert "os.O_EXCL" in source


def test_generation_ignores_hostile_home_and_emits_canonical_lf_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = _load_helper()
    repository, revision, bundle = _repository_and_bundle(tmp_path)
    fake_home = tmp_path / "hostile-home"
    fake_home.mkdir()
    global_attributes = fake_home / "global.attributes"
    global_attributes.write_text("* export-ignore\n", encoding="utf-8")
    (fake_home / ".gitconfig").write_text(
        "[core]\n"
        "\tautocrlf = true\n"
        "\teol = crlf\n"
        f"\tattributesFile = {global_attributes.as_posix()}\n"
        "[tar]\n"
        "\tumask = 0077\n",
        encoding="utf-8",
    )
    _git(repository, "config", "core.autocrlf", "true")
    _git(repository, "config", "core.eol", "crlf")
    _git(repository, "config", "core.attributesFile", global_attributes.as_posix())
    _git(repository, "config", "tar.umask", "0077")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.attributesFile")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", global_attributes.as_posix())

    output = tmp_path / "transport.tar"
    result = helper.generate_canonical_release_archive(
        repository,
        revision,
        bundle,
        output,
    )

    assert result.revision == revision
    assert result.archive_sha256 == helper._sha256(output)
    assert result.source_tree_sha256 == helper.verify_transport_against_bundle(
        output, bundle, revision
    )
    with tarfile.open(output, "r:") as archive:
        members = {member.name.rstrip("/"): member for member in archive}
        assert "source/plain.txt" in members
        assert archive.extractfile(members["source/plain.txt"]).read() == b"first\nsecond\n"
        assert archive.extractfile(members["source/run.sh"]).read() == b"#!/bin/sh\nprintf 'ok\\n'\n"
        assert stat.S_IMODE(members["source/plain.txt"].mode) == 0o664
        assert stat.S_IMODE(members["source/run.sh"].mode) == 0o775


def test_existing_output_is_refused_without_reuse_or_overwrite(tmp_path: Path) -> None:
    helper = _load_helper()
    repository, revision, bundle = _repository_and_bundle(tmp_path)
    output = tmp_path / "transport.tar"
    sentinel = b"stale-transport-must-not-be-reused"
    output.write_bytes(sentinel)

    with pytest.raises(helper.CanonicalArchiveError, match="already exists"):
        helper.generate_canonical_release_archive(
            repository,
            revision,
            bundle,
            output,
        )

    assert output.read_bytes() == sentinel


def test_timeout_kills_inheriting_descendant_without_pipe_or_reap_hang(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = _load_helper()
    started = tmp_path / "descendant-started"
    survived = tmp_path / "descendant-survived"
    descendant_code = "\n".join(
        (
            "from pathlib import Path",
            "import sys",
            "import time",
            "Path(sys.argv[1]).write_text('started', encoding='ascii')",
            "time.sleep(2)",
            "Path(sys.argv[2]).write_text('survived', encoding='ascii')",
            "time.sleep(4)",
        )
    )
    parent_code = "\n".join(
        (
            "from pathlib import Path",
            "import subprocess",
            "import sys",
            "import time",
            "time.sleep(0.15)",
            "subprocess.Popen([sys.executable, '-c', sys.argv[3], sys.argv[1], sys.argv[2]])",
            "deadline = time.monotonic() + 0.6",
            "while not Path(sys.argv[1]).exists() and time.monotonic() < deadline:",
            "    time.sleep(0.01)",
            "time.sleep(60)",
        )
    )
    command = [
        sys.executable,
        "-c",
        parent_code,
        str(started),
        str(survived),
        descendant_code,
    ]
    monkeypatch.setattr(helper, "_git_command", lambda *_args: command)
    monkeypatch.setattr(helper, "GIT_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(helper, "PROCESS_TERMINATION_TIMEOUT_SECONDS", 2.0)

    before = time.monotonic()
    with pytest.raises(helper.CanonicalArchiveError, match="time limit"):
        helper._run_git(None, "ignored")
    elapsed = time.monotonic() - before

    assert elapsed < 3
    assert started.is_file(), "the descendant must start before the timeout"
    time.sleep(1.3)
    assert not survived.exists(), "the timed-out descendant process was left alive"


def test_generation_refuses_a_revision_other_than_exact_head(tmp_path: Path) -> None:
    helper = _load_helper()
    repository, _revision, bundle = _repository_and_bundle(tmp_path)
    output = tmp_path / "transport.tar"

    with pytest.raises(helper.CanonicalArchiveError, match="HEAD"):
        helper.generate_canonical_release_archive(
            repository,
            "0" * 40,
            bundle,
            output,
        )

    assert not output.exists()


@pytest.mark.parametrize(
    ("crlf", "change_exec"),
    ((True, False), (False, True)),
    ids=("raw-crlf-mismatch", "executable-bit-mismatch"),
)
def test_transport_verification_rejects_raw_byte_or_exec_mismatch(
    tmp_path: Path,
    crlf: bool,
    change_exec: bool,
) -> None:
    helper = _load_helper()
    repository, revision, bundle = _repository_and_bundle(tmp_path)
    canonical = tmp_path / "canonical.tar"
    helper.generate_canonical_release_archive(
        repository,
        revision,
        bundle,
        canonical,
    )
    tampered = tmp_path / "tampered.tar"
    _rewrite_transport(
        canonical,
        tampered,
        crlf=crlf,
        change_exec=change_exec,
    )

    with pytest.raises(
        helper.CanonicalArchiveError,
        match="does not equal the bundle exact-revision tree",
    ):
        helper.verify_transport_against_bundle(tampered, bundle, revision)
