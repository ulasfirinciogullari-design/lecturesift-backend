from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import re
import subprocess
import threading

import pytest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "deploy" / "validate_rehearsal_admission.py"
SHELL_GATES = (
    "deploy/trusted_stage_release_controller.sh",
    "deploy/trusted_exact_rehearsal_controller.sh",
    "deploy/stage_release_candidate.sh",
    "deploy/run_exact_rehearsal.sh",
)
FUNCTION_NAME = "verify_no_git_export_attributes"


def _extract_shell_function(relative: str) -> str:
    lines = (ROOT / relative).read_text(encoding="utf-8").splitlines(keepends=True)
    start = next(
        index
        for index, line in enumerate(lines)
        if line.rstrip("\r\n") == f"{FUNCTION_NAME}() {{"
    )
    heredoc_end: str | None = None
    heredoc = re.compile(r"<<-?['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")
    result: list[str] = []
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
    raise AssertionError(f"unterminated {FUNCTION_NAME}() in {relative}")


def _normalized_shell(source: str) -> str:
    return " ".join(source.replace("\\\n", " ").split())


def _assert_timeout_token_is_fixed(script: str, token: str, relative: str) -> None:
    value = token.strip("\"'")
    if re.fullmatch(r"[1-9][0-9]*s?", value):
        seconds = int(value.rstrip("s"))
        assert seconds <= 60, f"unreasonably long attribute gate in {relative}"
        return
    variable = re.fullmatch(r"\$\{?([A-Z][A-Z0-9_]*)\}?", value)
    assert variable, f"non-fixed timeout token {token!r} in {relative}"
    assignment = re.search(
        rf"(?m)^{re.escape(variable.group(1))}=([1-9][0-9]*)$",
        script,
    )
    assert assignment, f"unbound timeout variable {value} in {relative}"
    assert int(assignment.group(1)) <= 60


@pytest.mark.parametrize("relative", SHELL_GATES)
def test_shell_export_attribute_pipeline_has_hard_process_deadlines(relative: str) -> None:
    script = (ROOT / relative).read_text(encoding="utf-8")
    function = _extract_shell_function(relative)
    normalized = _normalized_shell(function)
    timeout_prefix = r"timeout --signal=KILL\s+\S+\s+"

    required = (
        timeout_prefix
        + r'git -C "\$repository" rev-parse --path-format=absolute --git-common-dir',
        timeout_prefix
        + r'git -C "\$repository" rev-parse --path-format=absolute --git-path info/attributes',
        timeout_prefix
        + r'git -C "\$repository" ls-tree -rzt --name-only "\$expected_revision"',
        timeout_prefix
        + r'git -c core\.attributesFile=/dev/null -C "\$repository" check-attr',
        timeout_prefix + r"python3 -c",
    )
    for pattern in required:
        assert re.search(pattern, normalized), f"unbounded command in {relative}: {pattern}"

    tokens = re.findall(r"timeout --signal=KILL\s+(\S+)", function)
    assert len(tokens) >= len(required)
    for token in tokens:
        _assert_timeout_token_is_fixed(script, token, relative)
    assert script.index("set -Eeuo pipefail") < script.index(f"{FUNCTION_NAME}() {{")


@pytest.mark.parametrize("relative", SHELL_GATES)
def test_shell_export_attribute_pipeline_has_one_fixed_address_space_cap(
    relative: str,
) -> None:
    function = _extract_shell_function(relative)
    fixed_cap = "ulimit -v $((768 * 1024))"
    assert function.count(fixed_cap) == 1

    cap = function.index(fixed_cap)
    subshell = function.rfind("\n  (\n", 0, cap)
    pipefail = function.index("set -o pipefail", cap)
    producer = function.index("ls-tree -rzt --name-only", pipefail)
    consumer = function.index("check-attr", producer)
    parser = function.index("python3 -c", consumer)
    subshell_end = function.index("\n  ) || return 1", parser)
    assert subshell < cap < pipefail < producer < consumer < parser < subshell_end


def test_stage_candidate_trusts_only_the_fixed_timeout_binary() -> None:
    stage = (ROOT / "deploy" / "stage_release_candidate.sh").read_text(
        encoding="utf-8"
    )
    logical = stage.replace("\\\n", " ")
    assert re.search(r"(?m)^unset -f [^\n]*\btimeout\b", logical)
    assert re.search(r"(?m)^for command_name in [^;]*\btimeout\b", logical)


def _function_calls(tree: ast.Module, name: str, method: str) -> list[ast.Call]:
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and node.func.attr == method
    ]


def _keyword_name(call: ast.Call, keyword: str) -> str | None:
    value = next((item.value for item in call.keywords if item.arg == keyword), None)
    return value.id if isinstance(value, ast.Name) else None


def _numeric_expression(node: ast.expr) -> int:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return _numeric_expression(node.left) * _numeric_expression(node.right)
    raise AssertionError(f"address-space cap is not a fixed integer: {ast.dump(node)}")


def test_python_git_metadata_commands_use_the_hard_timeout_constant() -> None:
    source = VALIDATOR_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and (
            any(
                isinstance(target, ast.Name)
                and target.id == "GIT_COMMAND_TIMEOUT_SECONDS"
                for target in getattr(node, "targets", ())
            )
            or (
                isinstance(getattr(node, "target", None), ast.Name)
                and node.target.id == "GIT_COMMAND_TIMEOUT_SECONDS"
            )
        )
    )
    value = assignment.value
    assert isinstance(value, ast.Constant) and isinstance(value.value, (int, float))
    assert 0 < value.value <= 60

    info_calls = _function_calls(tree, "_git_info_attributes_path", "run")
    digest_calls = _function_calls(tree, "_git_tree_digest", "run")[:2]
    assert len(info_calls) == 2
    assert len(digest_calls) == 2
    for call in (*info_calls, *digest_calls):
        assert _keyword_name(call, "timeout") == "GIT_COMMAND_TIMEOUT_SECONDS"


def _assignment_value(tree: ast.Module, name: str) -> ast.expr:
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        )
    )
    return assignment.value


def _bounded_git_cap_name(call: ast.Call) -> str | None:
    if not call.args:
        return None
    command = call.args[0]
    if not (
        isinstance(command, ast.Call)
        and isinstance(command.func, ast.Name)
        and command.func.id == "_bounded_git_command"
        and command.args
        and isinstance(command.args[0], ast.Name)
    ):
        return None
    return command.args[0].id


def test_python_git_children_use_fixed_prlimit_address_space_caps() -> None:
    source = VALIDATOR_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    attribute_cap = _numeric_expression(
        _assignment_value(tree, "GIT_ATTRIBUTE_ADDRESS_SPACE_BYTES")
    )
    archive_cap = _numeric_expression(
        _assignment_value(tree, "GIT_ARCHIVE_ADDRESS_SPACE_BYTES")
    )
    assert attribute_cap == 768 * 1024 * 1024
    assert archive_cap == 3 * 1024 * 1024 * 1024
    assert attribute_cap < archive_cap <= 4 * 1024 * 1024 * 1024

    prlimit_path = _assignment_value(tree, "PRLIMIT_PATH")
    git_path = _assignment_value(tree, "GIT_PATH")
    assert isinstance(prlimit_path, ast.Constant)
    assert prlimit_path.value == "/usr/bin/prlimit"
    assert isinstance(git_path, ast.Constant)
    assert git_path.value == "/usr/bin/git"

    metadata_children = _function_calls(tree, "_git_info_attributes_path", "run")
    attribute_children = _function_calls(
        tree, "_assert_no_effective_git_export_attributes", "Popen"
    )
    checkout_children = _function_calls(tree, "_git_tree_digest", "run")[:2]
    archive_children = _function_calls(tree, "_git_tree_digest", "Popen")
    assert len(metadata_children) == 2
    assert len(attribute_children) == 2
    assert len(checkout_children) == 2
    assert len(archive_children) == 1
    for call in (*metadata_children, *attribute_children, *checkout_children):
        assert _bounded_git_cap_name(call) == "GIT_ATTRIBUTE_ADDRESS_SPACE_BYTES"
        assert all(keyword.arg != "preexec_fn" for keyword in call.keywords)
    assert (
        _bounded_git_cap_name(archive_children[0])
        == "GIT_ARCHIVE_ADDRESS_SPACE_BYTES"
    )
    assert all(keyword.arg != "preexec_fn" for keyword in archive_children[0].keywords)

    validator = _load_validator()
    assert validator._bounded_git_command(
        validator.GIT_ATTRIBUTE_ADDRESS_SPACE_BYTES,
        "status",
    ) == [
        "/usr/bin/prlimit",
        f"--as={768 * 1024 * 1024}",
        "--",
        "/usr/bin/git",
        "status",
    ]
    assert validator._bounded_git_command(
        validator.GIT_ARCHIVE_ADDRESS_SPACE_BYTES,
        "archive",
    ) == [
        "/usr/bin/prlimit",
        f"--as={3 * 1024 * 1024 * 1024}",
        "--",
        "/usr/bin/git",
        "archive",
    ]


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "lecturesift_rehearsal_admission_timeout_test",
        VALIDATOR_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _BlockingStream:
    def __init__(self, released: threading.Event) -> None:
        self._released = released

    def read(self, _size: int = -1) -> bytes:
        self._released.wait()
        return b""

    def close(self) -> None:
        # Closing the parent's copy of the producer pipe must not simulate EOF
        # on the consumer's independently held descriptor.
        return None


class _StalledProcess:
    def __init__(self) -> None:
        self.released = threading.Event()
        self.stdout = _BlockingStream(self.released)
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        self.released.set()

    terminate = kill

    def poll(self) -> int | None:
        return -9 if self.released.is_set() else None

    def wait(self, timeout: float | None = None) -> int:
        if not self.released.wait(timeout):
            raise subprocess.TimeoutExpired("fake-git", timeout)
        return -9


def test_python_streaming_watchdog_terminates_stalled_pipeline(monkeypatch) -> None:
    validator = _load_validator()
    assert hasattr(validator, "GIT_COMMAND_TIMEOUT_SECONDS")
    monkeypatch.setattr(validator, "GIT_COMMAND_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(
        validator,
        "_git_info_attributes_path",
        lambda _root, _environment: Path("/nonexistent/info/attributes"),
    )

    processes: list[_StalledProcess] = []

    commands: list[list[str]] = []

    def fake_popen(*args, **_kwargs):
        commands.append(args[0])
        process = _StalledProcess()
        processes.append(process)
        return process

    monkeypatch.setattr(validator.subprocess, "Popen", fake_popen)
    outcome: list[BaseException | None] = []

    def invoke() -> None:
        try:
            validator._assert_no_effective_git_export_attributes(
                Path("/isolated/repository"),
                "a" * 40,
                {"GIT_ATTR_NOSYSTEM": "1"},
            )
        except BaseException as exc:  # captured for assertion in the main thread
            outcome.append(exc)
        else:
            outcome.append(None)

    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    worker.join(timeout=1.0)
    if worker.is_alive():
        for process in processes:
            process.released.set()
        worker.join(timeout=1.0)
        pytest.fail("Git attribute inspection exceeded its hard watchdog deadline")

    assert len(processes) == 2
    assert all(
        command[:4]
        == [
            validator.PRLIMIT_PATH,
            f"--as={validator.GIT_ATTRIBUTE_ADDRESS_SPACE_BYTES}",
            "--",
            validator.GIT_PATH,
        ]
        for command in commands
    )
    assert all(process.killed for process in processes)
    assert len(outcome) == 1
    assert isinstance(outcome[0], validator.AdmissionError)
