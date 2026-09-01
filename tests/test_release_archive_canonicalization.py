from __future__ import annotations

import ast
import io
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SHELL_ARCHIVE_PRODUCERS = (
    "deploy/release.sh",
    "deploy/run_exact_rehearsal.sh",
    "deploy/stage_release_candidate.sh",
    "deploy/trusted_exact_rehearsal_controller.sh",
    "deploy/trusted_stage_release_controller.sh",
)
CANONICAL_ARCHIVE_CONFIG = (
    "-c core.attributesFile=/dev/null -c core.autocrlf=false "
    "-c core.eol=lf -c tar.umask=0002"
)
RUN_EXACT_REHEARSAL = ROOT / "deploy" / "run_exact_rehearsal.sh"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _logical_shell(relative: str) -> str:
    return " ".join(_read(relative).replace("\\\n", " ").split())


def _git(repository: Path, *arguments: str, environment=None) -> bytes:
    git = shutil.which("git")
    assert git, "Git is required for archive canonicalization tests"
    completed = subprocess.run(
        [git, "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        timeout=30,
    )
    return completed.stdout


def _usable_bash() -> str:
    candidates: list[Path] = []
    git = shutil.which("git")
    if git and os.name == "nt":
        git_root = Path(git).resolve().parent.parent
        candidates.extend(
            (
                git_root / "bin" / "bash.exe",
                git_root / "usr" / "bin" / "bash.exe",
                git_root / "usr" / "bin" / "sh.exe",
            )
        )
    discovered = shutil.which("bash")
    if discovered:
        candidates.append(Path(discovered))

    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate))
        if key in seen or not candidate.is_file():
            continue
        seen.add(key)
        try:
            probe = subprocess.run(
                [
                    str(candidate),
                    "-c",
                    'set -o pipefail; [[ -f "$1" ]] || exit 97; printf BASH_OK',
                    "archive-pipeline-bash-probe",
                    RUN_EXACT_REHEARSAL.as_posix(),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0 and probe.stdout == "BASH_OK":
            return str(candidate)

    if os.name == "nt":
        pytest.skip("a usable Git Bash runtime is unavailable on this Windows host")
    pytest.fail("Bash is required for the release archive pipeline test")


def _write_admission_archive_pipeline() -> str:
    source = RUN_EXACT_REHEARSAL.read_text(encoding="utf-8")
    match = re.search(
        r"(?m)^  git -c core\.attributesFile=/dev/null .*?"
        r"^    tar -xf - -C \"\$local_tree\" \|\| return 1$",
        source,
        flags=re.DOTALL,
    )
    assert match, "write_admission must retain its guarded canonical archive pipeline"
    return "\n".join(line[2:] for line in match.group(0).splitlines())


def test_every_release_git_archive_has_the_canonical_fixed_config() -> None:
    discovered_shell_producers = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "deploy").rglob("*.sh")
        if "archive --format=tar" in path.read_text(encoding="utf-8")
    }
    assert discovered_shell_producers == set(SHELL_ARCHIVE_PRODUCERS)

    for relative in SHELL_ARCHIVE_PRODUCERS:
        script = _logical_shell(relative)
        assert "export GIT_CONFIG_NOSYSTEM=1" in script, relative
        assert "export GIT_CONFIG_GLOBAL=/dev/null" in script, relative
        archive_offsets = [
            match.start() for match in re.finditer(r"\barchive --format=tar\b", script)
        ]
        assert archive_offsets, f"no release archive command found in {relative}"
        for offset in archive_offsets:
            command_prefix = script[max(0, offset - 600) : offset]
            assert CANONICAL_ARCHIVE_CONFIG in command_prefix, relative

    source = _read("deploy/validate_rehearsal_admission.py")
    assert 'git_environment["GIT_CONFIG_NOSYSTEM"] = "1"' in source
    assert 'git_environment["GIT_CONFIG_GLOBAL"] = os.devnull' in source
    tree = ast.parse(source)
    digest_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_git_tree_digest"
    )
    archive_process = next(
        node
        for node in ast.walk(digest_function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and node.func.attr == "Popen"
    )
    bounded = archive_process.args[0]
    assert isinstance(bounded, ast.Call)
    assert isinstance(bounded.func, ast.Name)
    assert bounded.func.id == "_bounded_git_command"
    assert isinstance(bounded.args[0], ast.Name)
    assert bounded.args[0].id == "GIT_ARCHIVE_ADDRESS_SPACE_BYTES"
    literal_arguments = [
        argument.value
        for argument in bounded.args[1:]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    ]
    assert literal_arguments[:8] == [
        "-c",
        "core.attributesFile=/dev/null",
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.eol=lf",
        "-c",
        "tar.umask=0002",
    ]


def test_canonical_archive_overrides_hostile_crlf_and_tar_umask(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.email", "archive-test@example.invalid")
    _git(repository, "config", "user.name", "Archive Canonicalization Test")
    _git(repository, "config", "core.autocrlf", "true")
    _git(repository, "config", "core.eol", "crlf")
    _git(repository, "config", "tar.umask", "0077")
    (repository / "plain.txt").write_bytes(b"first\nsecond\n")
    (repository / "run.sh").write_bytes(b"#!/bin/sh\nprintf 'ok\\n'\n")
    nested = repository / "nested"
    nested.mkdir()
    (nested / "data.txt").write_bytes(b"nested\n")
    _git(repository, "add", "--all")
    _git(repository, "update-index", "--chmod=+x", "run.sh")
    _git(repository, "commit", "--quiet", "--no-gpg-sign", "-m", "fixture")

    hostile = os.environ.copy()
    hostile.update(
        {
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "core.autocrlf",
            "GIT_CONFIG_VALUE_0": "true",
            "GIT_CONFIG_KEY_1": "core.eol",
            "GIT_CONFIG_VALUE_1": "crlf",
            "GIT_CONFIG_KEY_2": "tar.umask",
            "GIT_CONFIG_VALUE_2": "0077",
        }
    )
    arguments = (
        "-c",
        "core.attributesFile=/dev/null",
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.eol=lf",
        "-c",
        "tar.umask=0002",
        "archive",
        "--format=tar",
        "HEAD",
    )
    first = _git(repository, *arguments, environment=hostile)
    second = _git(repository, *arguments, environment=hostile)
    assert first == second

    with tarfile.open(fileobj=io.BytesIO(first), mode="r:") as archive:
        members = {member.name.rstrip("/"): member for member in archive}
        assert archive.extractfile(members["plain.txt"]).read() == b"first\nsecond\n"
        assert archive.extractfile(members["run.sh"]).read() == b"#!/bin/sh\nprintf 'ok\\n'\n"
        # tar.umask=0002 is fixed and host-independent.  It deliberately emits
        # 0664/0775 headers; trusted extraction then narrows them to the
        # canonical runtime modes 0644/0755 before execution or Docker build.
        assert stat.S_IMODE(members["plain.txt"].mode) == 0o664
        assert stat.S_IMODE(members["run.sh"].mode) == 0o775
        assert stat.S_IMODE(members["nested"].mode) == 0o775
        assert stat.S_IMODE(members["nested/data.txt"].mode) == 0o664


def test_release_extractors_normalize_before_execution_or_docker_build() -> None:
    trusted = _logical_shell("deploy/trusted_stage_release_controller.sh")
    trusted_extract = trusted.index(
        'tar -xf "$state/git-tree.tar" -C "$state/reviewed"'
    )
    trusted_normalize = trusted.index(
        'normalize_release_tree_modes "$state/reviewed"', trusted_extract
    )
    trusted_execute = trusted.index('bash -p "$candidate"', trusted_normalize)
    assert trusted_extract < trusted_normalize < trusted_execute

    candidate = _logical_shell("deploy/stage_release_candidate.sh")
    transport_extract = candidate.index(
        'tar --extract --file "$archive" --directory "$incoming_release"'
    )
    transport_normalize = candidate.index(
        'normalize_release_tree_modes "$incoming_release"', transport_extract
    )
    comparison_extract = candidate.index(
        'archive --format=tar "$revision" | tar -xf - -C "$comparison_tree"'
    )
    comparison_normalize = candidate.index(
        'normalize_release_tree_modes "$comparison_tree"', comparison_extract
    )
    first_build = candidate.index("docker build --pull", transport_normalize)
    assert transport_extract < transport_normalize < first_build
    assert comparison_extract < comparison_normalize < first_build

    release = _logical_shell("deploy/release.sh")
    release_extract = release.index('| tar -xf - -C "$release_context"')
    directory_modes = release.index(
        'find "$release_context" -xdev -type d -exec chmod 0755', release_extract
    )
    nonexec_modes = release.index(
        'find "$release_context" -xdev -type f ! -perm /111 -exec chmod 0644',
        directory_modes,
    )
    docker_build = release.index("docker build --pull", nonexec_modes)
    assert release_extract < directory_modes < nonexec_modes < docker_build


def test_release_consumers_require_fresh_destinations_not_stale_trees() -> None:
    release = _logical_shell("deploy/release.sh")
    assert 'release_context="$(mktemp -d -- /var/tmp/lecturesift-release.XXXXXXXX)"' in release
    assert release.index("mktemp -d -- /var/tmp/lecturesift-release") < release.index(
        'archive --format=tar "$expected_revision"'
    )

    candidate = _logical_shell("deploy/stage_release_candidate.sh")
    target_guard = (
        'for target in "$release" "$worktree" "$incoming_release" '
        '"$incoming_worktree" "$comparison_tree" "$evidence"'
    )
    assert target_guard in candidate
    assert candidate.index("target-already-exists") < candidate.index(
        'tar --extract --file "$archive"'
    )

    rehearsal = _logical_shell("deploy/run_exact_rehearsal.sh")
    assert '[[ ! -e "$local_tree" && ! -L "$local_tree" ]] || return 1' in rehearsal
    assert rehearsal.index('[[ ! -e "$local_tree"') < rehearsal.index(
        'archive --format=tar "$revision"'
    )
    assert (
        'archive --format=tar "$revision" | tar -xf - -C "$local_tree" || return 1'
        in rehearsal
    )


@pytest.mark.parametrize(
    ("upstream_status", "downstream_status"),
    ((7, 0), (0, 9)),
    ids=("git-archive-failure", "tar-extraction-failure"),
)
def test_write_admission_archive_pipeline_fails_closed_on_either_side(
    upstream_status: int,
    downstream_status: int,
) -> None:
    pipeline = _write_admission_archive_pipeline()
    harness = f'''set -o pipefail
root=/unused
revision={'0' * 40}
local_tree=/unused
git() {{
  if (( UPSTREAM_STATUS == 0 )); then
    printf 'synthetic archive payload'
  fi
  return "$UPSTREAM_STATUS"
}}
tar() {{
  command cat >/dev/null
  return "$DOWNSTREAM_STATUS"
}}
probe() {{
{pipeline}
  printf 'PIPELINE_CONTINUED\\n'
}}
if probe; then
  printf 'UNEXPECTED_SUCCESS\\n'
  exit 90
fi
printf 'PIPELINE_REFUSED\\n'
'''
    environment = os.environ.copy()
    environment.update(
        {
            "UPSTREAM_STATUS": str(upstream_status),
            "DOWNSTREAM_STATUS": str(downstream_status),
        }
    )
    completed = subprocess.run(
        [_usable_bash(), "-c", harness],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "PIPELINE_REFUSED\n"


def _extract_normalizer(relative: str) -> str:
    lines = _read(relative).splitlines(keepends=True)
    start = next(
        index
        for index, line in enumerate(lines)
        if line.rstrip("\r\n") == "normalize_release_tree_modes() {"
    )
    for end in range(start + 1, len(lines)):
        if lines[end].rstrip("\r\n") == "}":
            return "".join(lines[start : end + 1])
    raise AssertionError(f"unterminated normalizer in {relative}")


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode semantics require Linux")
@pytest.mark.parametrize(
    "relative",
    (
        "deploy/trusted_stage_release_controller.sh",
        "deploy/stage_release_candidate.sh",
    ),
)
def test_mode_normalizer_strips_special_and_write_bits_but_keeps_exec(
    tmp_path: Path,
    relative: str,
) -> None:
    bash = shutil.which("bash")
    assert bash
    tree = tmp_path / "tree"
    nested = tree / "nested"
    nested.mkdir(parents=True)
    regular = nested / "regular.txt"
    executable = nested / "run.sh"
    regular.write_bytes(b"regular")
    executable.write_bytes(b"executable")
    tree.chmod(0o1777)
    nested.chmod(0o2777)
    regular.chmod(0o666)
    executable.chmod(0o4777)

    harness = _extract_normalizer(relative) + '\nnormalize_release_tree_modes "$1"\n'
    subprocess.run(
        [bash, "-c", harness, "mode-normalizer", str(tree)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )

    assert stat.S_IMODE(tree.stat().st_mode) == 0o755
    assert stat.S_IMODE(nested.stat().st_mode) == 0o755
    assert stat.S_IMODE(regular.stat().st_mode) == 0o644
    assert stat.S_IMODE(executable.stat().st_mode) == 0o755
