from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "deploy" / "trusted_stage_release_controller.sh"
FUNCTION_NAME = "verify_no_git_export_attributes"


def _extract_function() -> str:
    """Return the real controller function without sourcing its root-only body."""
    lines = CONTROLLER.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if line.rstrip("\r\n") == f"{FUNCTION_NAME}() {{"
        ),
        None,
    )
    assert start is not None, f"{FUNCTION_NAME}() is missing from {CONTROLLER}"

    heredoc_end: str | None = None
    result: list[str] = []
    heredoc = re.compile(r"<<-?['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")
    for line in lines[start:]:
        result.append(line)
        stripped = line.rstrip("\r\n")
        if heredoc_end is not None:
            if stripped.lstrip("\t") == heredoc_end:
                heredoc_end = None
            continue
        match = heredoc.search(stripped)
        if match:
            heredoc_end = match.group(1)
            continue
        if stripped == "}":
            return "".join(result)
    raise AssertionError(f"unterminated {FUNCTION_NAME}() in {CONTROLLER}")


def _usable_bash() -> str:
    candidates: list[Path] = []
    git = shutil.which("git")
    if git and os.name == "nt":
        git_root = Path(git).resolve().parent.parent
        candidates.extend(
            (
                git_root / "bin" / "bash.exe",
                git_root / "usr" / "bin" / "bash.exe",
                # The bundled Git runtime is Bash even when exposed as sh.exe.
                git_root / "usr" / "bin" / "sh.exe",
            )
        )

    # On Windows, PATH commonly resolves `bash` to System32/bash.exe (WSL).
    # Prefer Git's MSYS runtime because it can consume the native C:/ paths
    # passed by this test harness.  The path visibility probe below also keeps
    # an incompatible WSL binary from being accepted as a fallback.
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
                    'set -o pipefail; [[ -f "$1" ]] || exit 97; '
                    "printf POLICY_BASH_OK",
                    "policy-bash-probe",
                    CONTROLLER.as_posix(),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0 and probe.stdout == "POLICY_BASH_OK":
            return str(candidate)

    if os.name == "nt":
        pytest.skip("a usable Git Bash runtime is unavailable on this Windows host")
    pytest.fail("Bash is required for the deployment security-policy tests")


def _harness() -> str:
    return (
        r'''#!/usr/bin/env bash
set -Eeuo pipefail
export GIT_ATTR_NOSYSTEM=1
python_runtime=$1
shift

# Git for Windows' compact shell has no python3 or realpath binaries.  These
# compatibility functions preserve the controller function itself verbatim.
python3() {
  "$python_runtime" "$@"
}

# The compact Git runtime bundled on Windows omits GNU timeout.  Timeout
# placement and watchdog behavior have separate regression tests; this shim
# lets the cross-platform policy matrix exercise the unchanged real function.
if [[ "${POLICY_TIMEOUT_SHIM:-false}" == "true" ]]; then
  timeout() {
    [[ "${1:-}" == "--signal=KILL" ]] || return 98
    shift 2
    "$@"
  }
fi

if ! command -v realpath >/dev/null 2>&1; then
  realpath() {
    local mode=$1 path
    shift
    [[ "${1:-}" != "--" ]] || shift
    path=${1:-}
    python3 - "$mode" "$path" <<'PY'
import os
from pathlib import Path
import re
import sys

mode, raw_path = sys.argv[1:]
if os.name == "nt" and re.match(r"^/[A-Za-z]/", raw_path):
    raw_path = f"{raw_path[1]}:/{raw_path[3:]}"
try:
    resolved = Path(raw_path).resolve(strict=mode == "-e")
except (OSError, RuntimeError):
    raise SystemExit(1)
print(str(resolved).replace("\\", "/"))
PY
  }
fi
'''
        + _extract_function()
        + f'''\n
(( $# % 2 == 0 )) || exit 98
while (( $# )); do
  repository=$1
  expected_revision=$2
  shift 2
  if {FUNCTION_NAME} "$repository" "$expected_revision"; then
    printf 'POLICY_RESULT|0\\n'
  else
    printf 'POLICY_RESULT|1\\n'
  fi
done
'''
    )


@pytest.fixture(scope="module")
def policy_runner(tmp_path_factory: pytest.TempPathFactory):
    bash = _usable_bash()
    directory = tmp_path_factory.mktemp("git-attribute-policy")
    harness = directory / "policy-harness.sh"
    harness.write_text(_harness(), encoding="utf-8", newline="\n")
    harness.chmod(0o700)

    def run(cases: list[tuple[Path, str]]) -> list[bool]:
        assert cases
        environment = os.environ.copy()
        environment["GIT_ATTR_NOSYSTEM"] = "1"
        environment["LC_ALL"] = "C"
        if os.name == "nt":
            environment["POLICY_TIMEOUT_SHIM"] = "true"
        arguments = [bash, str(harness), sys.executable]
        for repository, revision in cases:
            arguments.extend((str(repository), revision))
        completed = subprocess.run(
            arguments,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
            env=environment,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout
        results = [
            line == "POLICY_RESULT|0"
            for line in completed.stdout.splitlines()
            if line.startswith("POLICY_RESULT|")
        ]
        assert len(results) == len(cases), completed.stdout or completed.stderr
        return results

    return run


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    assert git, "Git is required for archive-attribute policy tests"
    environment = os.environ.copy()
    environment["GIT_ATTR_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    completed = subprocess.run(
        [git, "-c", "core.attributesFile=/dev/null", "-C", str(repository), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return completed


def _initialize_repository(directory: Path, attributes: dict[str, str]) -> str:
    directory.mkdir()
    git = shutil.which("git")
    assert git, "Git is required for archive-attribute policy tests"
    subprocess.run(
        [git, "init", "--quiet", str(directory)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
    )
    _git(directory, "config", "user.email", "security-test@example.invalid")
    _git(directory, "config", "user.name", "Archive Attribute Policy Test")

    files = {
        "requirements.txt": "example==1\n",
        "requirements.lock": "example==1\n",
        "requirements-dev.txt": "pytest==1\n",
        "deploy/supply_chain.lock": "lock\n",
        "deploy/secret.sh": "#!/bin/sh\nexit 0\n",
        "README.md": "$Format:%H$\n",
        "docs/notes.txt": "$Format:%H$\n",
        **attributes,
    }
    for relative, content in files.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    _git(directory, "add", "--all")
    _git(directory, "commit", "--quiet", "--no-gpg-sign", "-m", "policy fixture")
    return _git(directory, "rev-parse", "HEAD").stdout.strip()


def _commit_attribute_revision(
    repository: Path,
    attributes: dict[str, str],
    message: str,
) -> str:
    for current in repository.rglob(".gitattributes"):
        if ".git" not in current.relative_to(repository).parts:
            current.unlink()
    for relative, content in attributes.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    _git(repository, "add", "--all")
    _git(repository, "commit", "--quiet", "--no-gpg-sign", "-m", message)
    return _git(repository, "rev-parse", "HEAD").stdout.strip()


def _current_benign_attributes() -> str:
    expected = (
        "requirements.txt text eol=lf\n"
        "requirements.lock text eol=lf\n"
        "requirements-dev.txt text eol=lf\n"
        "deploy/supply_chain.lock text eol=lf\n"
    )
    assert (ROOT / ".gitattributes").read_text(encoding="utf-8") == expected
    return expected


def test_current_benign_root_gitattributes_is_admitted(
    tmp_path: Path,
    policy_runner,
) -> None:
    repository = tmp_path / "repository"
    revision = _initialize_repository(
        repository,
        {".gitattributes": _current_benign_attributes()},
    )

    admitted = policy_runner([(repository, revision)])

    assert admitted == [True]


def test_export_attribute_attack_matrix_is_rejected(
    tmp_path: Path,
    policy_runner,
) -> None:
    attacks = (
        ("direct-export-ignore", {".gitattributes": "deploy/secret.sh export-ignore\n"}),
        ("direct-export-subst", {".gitattributes": "README.md export-subst\n"}),
        (
            "nested-export-ignore",
            {
                ".gitattributes": _current_benign_attributes(),
                "deploy/.gitattributes": "secret.sh export-ignore\n",
            },
        ),
        (
            "nested-export-subst",
            {
                ".gitattributes": _current_benign_attributes(),
                "docs/.gitattributes": "notes.txt export-subst\n",
            },
        ),
        (
            "macro-export-ignore",
            {".gitattributes": "[attr]hidden export-ignore\ndeploy/secret.sh hidden\n"},
        ),
        (
            "macro-export-subst",
            {".gitattributes": "[attr]rewrite export-subst\nREADME.md rewrite\n"},
        ),
        ("directory-export-ignore", {".gitattributes": "deploy export-ignore\n"}),
        ("directory-export-subst", {".gitattributes": "docs export-subst\n"}),
    )
    repository = tmp_path / "attack-revisions"
    _initialize_repository(
        repository,
        {".gitattributes": _current_benign_attributes()},
    )
    cases: list[tuple[Path, str]] = []
    for scenario, attributes in attacks:
        revision = _commit_attribute_revision(repository, attributes, scenario)
        cases.append((repository, revision))

    admitted = policy_runner(cases)

    outcomes = {
        scenario: was_admitted
        for (scenario, _), was_admitted in zip(attacks, admitted, strict=True)
    }
    assert not any(outcomes.values()), outcomes


def test_attribute_policy_is_bound_to_the_requested_revision(
    tmp_path: Path,
    policy_runner,
) -> None:
    repository = tmp_path / "repository"
    benign_revision = _initialize_repository(
        repository,
        {".gitattributes": _current_benign_attributes()},
    )
    (repository / ".gitattributes").write_text(
        "deploy/secret.sh export-ignore\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repository, "add", ".gitattributes")
    _git(repository, "commit", "--quiet", "--no-gpg-sign", "-m", "malicious revision")
    malicious_revision = _git(repository, "rev-parse", "HEAD").stdout.strip()

    admitted = policy_runner(
        [(repository, benign_revision), (repository, malicious_revision)]
    )

    assert admitted == [True, False]


def test_repository_local_info_attributes_are_rejected(
    tmp_path: Path,
    policy_runner,
) -> None:
    cases: list[tuple[Path, str]] = []
    exercised = ["regular"]
    for kind in ("regular", "symlink", "broken-symlink"):
        repository = tmp_path / kind
        revision = _initialize_repository(
            repository,
            {".gitattributes": _current_benign_attributes()},
        )
        info_attributes = repository / ".git" / "info" / "attributes"
        if kind == "regular":
            info_attributes.write_text("* export-ignore\n", encoding="utf-8")
            cases.append((repository, revision))
            continue
        target = tmp_path / f"{kind}-target"
        if kind == "symlink":
            target.write_text("* export-ignore\n", encoding="utf-8")
        try:
            info_attributes.symlink_to(target)
        except (NotImplementedError, OSError):
            continue
        exercised.append(kind)
        cases.append((repository, revision))

    admitted = policy_runner(cases)

    assert admitted == [False] * len(cases), exercised
    if os.name != "nt":
        assert exercised == ["regular", "symlink", "broken-symlink"]
