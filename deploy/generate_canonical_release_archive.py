"""Generate one fresh, platform-independent exact-release transport archive."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile


REVISION = re.compile(r"[0-9a-f]{40}")
GIT_TIMEOUT_SECONDS = 360
PROCESS_TERMINATION_TIMEOUT_SECONDS = 5
MAX_TREE_ENTRIES = 100_000
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 1024 * 1024 * 1024
ARCHIVE_CONFIG = (
    "-c",
    "core.attributesFile=/dev/null",
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.eol=lf",
    "-c",
    "tar.umask=0002",
)


class CanonicalArchiveError(RuntimeError):
    pass


@dataclass(frozen=True)
class CanonicalArchiveResult:
    revision: str
    archive_sha256: str
    source_tree_sha256: str
    archive_bytes: int


def _create_windows_kill_job(process: subprocess.Popen[bytes]) -> int | None:
    if os.name != "nt":
        return None

    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    try:
        limits = ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job,
            9,  # JobObjectExtendedLimitInformation
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        process_handle = wintypes.HANDLE(int(process._handle))
        if not kernel32.AssignProcessToJobObject(job, process_handle):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        return int(job)
    except BaseException:
        kernel32.CloseHandle(job)
        raise


def _terminate_windows_job(job: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    if not kernel32.TerminateJobObject(wintypes.HANDLE(job), 1):
        error = ctypes.get_last_error()
        if error != 6:  # ERROR_INVALID_HANDLE: the job was already closed.
            raise OSError(error, "TerminateJobObject failed")


def _close_windows_job(job: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    if not kernel32.CloseHandle(wintypes.HANDLE(job)):
        raise OSError(ctypes.get_last_error(), "CloseHandle failed")


def _kill_process_tree(process: subprocess.Popen[bytes], windows_job: int | None) -> None:
    if os.name == "nt":
        if windows_job is None:
            process.kill()
        else:
            _terminate_windows_job(windows_job)
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _reap_after_kill(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=PROCESS_TERMINATION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise CanonicalArchiveError("Git process could not be reaped after termination") from exc


def _git_path() -> str:
    discovered = shutil.which("git")
    if not discovered:
        raise CanonicalArchiveError("Git is unavailable")
    return str(Path(discovered).resolve(strict=True))


def _clean_git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in tuple(environment):
        if key.startswith("GIT_"):
            environment.pop(key)
    environment["GIT_ATTR_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["LC_ALL"] = "C"
    return environment


def _git_command(repository: Path | None, *arguments: str) -> list[str]:
    command = [_git_path()]
    if repository is not None:
        command.extend(("-c", f"safe.directory={repository.as_posix()}"))
    command.extend(arguments)
    return command


def _run_git(
    repository: Path | None,
    *arguments: str,
    stdout=subprocess.PIPE,
) -> subprocess.CompletedProcess[bytes]:
    command = _git_command(repository, *arguments)
    capture_stdout = stdout == subprocess.PIPE
    captured_stdout = tempfile.TemporaryFile(mode="w+b") if capture_stdout else None
    output_target = captured_stdout if capture_stdout else stdout
    process: subprocess.Popen[bytes] | None = None
    windows_job: int | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=repository,
            stdin=subprocess.DEVNULL,
            stdout=output_target,
            stderr=subprocess.DEVNULL,
            env=_clean_git_environment(),
            start_new_session=os.name != "nt",
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            ),
        )
        windows_job = _create_windows_kill_job(process)
        try:
            returncode = process.wait(timeout=GIT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            _kill_process_tree(process, windows_job)
            _reap_after_kill(process)
            raise CanonicalArchiveError("Git command exceeded its time limit") from exc

        # A successful Git process must not leave a filter/index helper behind.
        # Closing a Windows kill-on-close job terminates every descendant.  On
        # POSIX the dedicated process group gives the same cleanup boundary.
        if windows_job is not None:
            job_to_close = windows_job
            windows_job = None
            _close_windows_job(job_to_close)
        elif os.name != "nt":
            _kill_process_tree(process, None)

        if returncode:
            raise CanonicalArchiveError("Git command failed")
        output = None
        if captured_stdout is not None:
            captured_stdout.flush()
            captured_stdout.seek(0)
            output = captured_stdout.read()
        return subprocess.CompletedProcess(command, returncode, output, b"")
    except CanonicalArchiveError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise CanonicalArchiveError("Git command failed") from exc
    finally:
        if process is not None and process.poll() is None:
            try:
                _kill_process_tree(process, windows_job)
            finally:
                _reap_after_kill(process)
        if windows_job is not None:
            job_to_close = windows_job
            windows_job = None
            _close_windows_job(job_to_close)
        if captured_stdout is not None:
            captured_stdout.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_inventory(path: Path, *, strip_source: bool) -> list[tuple[str, str, int, str]]:
    inventory: list[tuple[str, str, int, str]] = []
    seen: set[str] = set()
    entries = 0
    expanded = 0
    try:
        with tarfile.open(path, mode="r|") as archive:
            for member in archive:
                raw_name = member.name.rstrip("/")
                item = PurePosixPath(raw_name)
                if not raw_name or item.is_absolute() or ".." in item.parts:
                    raise CanonicalArchiveError("archive contains an unsafe path")
                if strip_source:
                    if item.parts[0] != "source":
                        raise CanonicalArchiveError("transport archive lacks its source prefix")
                    parts = item.parts[1:]
                    if not parts:
                        if member.isdir():
                            continue
                        raise CanonicalArchiveError("transport archive has an invalid root")
                    relative = PurePosixPath(*parts).as_posix()
                else:
                    relative = item.as_posix()
                entries += 1
                expanded += member.size
                if (
                    entries > MAX_TREE_ENTRIES
                    or expanded > MAX_EXPANDED_BYTES
                    or member.size < 0
                    or member.size > MAX_MEMBER_BYTES
                    or relative in seen
                    or not (member.isdir() or member.isfile())
                ):
                    raise CanonicalArchiveError("archive exceeds the canonical safety contract")
                seen.add(relative)
                if member.isdir():
                    inventory.append((relative, "d", 1, ""))
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise CanonicalArchiveError("archive file cannot be read")
                digest = hashlib.sha256()
                for chunk in iter(lambda: extracted.read(1024 * 1024), b""):
                    digest.update(chunk)
                inventory.append(
                    (relative, "f", int(bool(member.mode & 0o111)), digest.hexdigest())
                )
    except (OSError, tarfile.TarError) as exc:
        raise CanonicalArchiveError("archive cannot be inspected") from exc
    if not inventory:
        raise CanonicalArchiveError("archive is empty")
    inventory.sort(key=lambda row: row[0])
    return inventory


def _inventory_digest(inventory: list[tuple[str, str, int, str]]) -> str:
    payload = json.dumps(inventory, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(payload).hexdigest()


def _created_identity(path: Path) -> tuple[int, int]:
    details = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(details.st_mode):
        raise CanonicalArchiveError("archive output is not a regular file")
    return details.st_dev, details.st_ino


def _remove_created(path: Path, identity: tuple[int, int]) -> None:
    try:
        details = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(details.st_mode) or (details.st_dev, details.st_ino) != identity:
        raise CanonicalArchiveError("archive output identity changed during generation")
    path.unlink()


def _write_archive(
    repository: Path,
    revision: str,
    output: Path,
    *,
    prefix: str | None,
) -> tuple[int, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(output, flags, 0o600)
    except FileExistsError as exc:
        raise CanonicalArchiveError("archive output already exists") from exc
    identity: tuple[int, int] | None = None
    try:
        details = os.fstat(descriptor)
        identity = (details.st_dev, details.st_ino)
        if not stat.S_ISREG(details.st_mode):
            raise CanonicalArchiveError("archive output is not a regular file")
        target = os.fdopen(descriptor, "wb")
        descriptor = -1
        with target:
            arguments = [*ARCHIVE_CONFIG, "-C", str(repository), "archive", "--format=tar"]
            if prefix is not None:
                arguments.append(f"--prefix={prefix}")
            arguments.append(revision)
            _run_git(repository, *arguments, stdout=target)
            target.flush()
            if hasattr(os, "fchmod"):
                os.fchmod(target.fileno(), 0o600)
            os.fsync(target.fileno())
        if _created_identity(output) != identity:
            raise CanonicalArchiveError("archive output identity changed during generation")
        if not hasattr(os, "fchmod"):
            os.chmod(output, 0o600)
            if _created_identity(output) != identity:
                raise CanonicalArchiveError("archive output identity changed during chmod")
        return identity
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        if identity is not None:
            _remove_created(output, identity)
        raise


def _validate_bundle_head(bundle: Path, revision: str) -> None:
    heads = _run_git(None, "bundle", "list-heads", str(bundle)).stdout.decode(
        "ascii", "strict"
    )
    if heads.splitlines() != [f"{revision} HEAD"]:
        raise CanonicalArchiveError("bundle does not expose only the exact HEAD")


def verify_transport_against_bundle(
    transport: Path,
    bundle: Path,
    revision: str,
) -> str:
    if not REVISION.fullmatch(revision):
        raise CanonicalArchiveError("revision must be one lowercase commit ID")
    transport = transport.resolve(strict=True)
    bundle_input = bundle
    if bundle_input.is_symlink() or not bundle_input.is_file():
        raise CanonicalArchiveError("bundle must be one regular non-link file")
    bundle = bundle_input.resolve(strict=True)
    _validate_bundle_head(bundle, revision)

    # Keep the verification repository in the OS temporary root.  Besides
    # providing a freshly created private directory, this avoids legacy Git
    # for Windows' path-length ceiling when the caller's worktree is deeply
    # nested (as Codex and CI worktrees commonly are).
    with tempfile.TemporaryDirectory(
        prefix="lecturesift-canonical-release-"
    ) as temporary_text:
        temporary = Path(temporary_text)
        repository = temporary / "repository.git"
        bundle_archive = temporary / "bundle-tree.tar"
        _run_git(None, "init", "--quiet", "--bare", str(repository))
        _run_git(
            repository,
            "-C",
            str(repository),
            "fetch",
            "--quiet",
            "--no-tags",
            str(bundle),
            revision,
        )
        imported = _run_git(
            repository,
            "-C",
            str(repository),
            "rev-parse",
            "--verify",
            "FETCH_HEAD^{commit}",
        ).stdout.decode("ascii", "strict").strip()
        if imported != revision:
            raise CanonicalArchiveError("bundle import did not resolve to the exact revision")
        _write_archive(repository, revision, bundle_archive, prefix=None)
        transport_inventory = _archive_inventory(transport, strip_source=True)
        bundle_inventory = _archive_inventory(bundle_archive, strip_source=False)
        if transport_inventory != bundle_inventory:
            raise CanonicalArchiveError(
                "transport archive does not equal the bundle exact-revision tree"
            )
        return _inventory_digest(bundle_inventory)


def generate_canonical_release_archive(
    root: Path,
    revision: str,
    bundle: Path,
    output: Path,
) -> CanonicalArchiveResult:
    if not REVISION.fullmatch(revision):
        raise CanonicalArchiveError("revision must be one lowercase commit ID")
    root_input = root
    if root_input.is_symlink() or not root_input.is_dir():
        raise CanonicalArchiveError("repository root must be a non-link directory")
    root = root_input.resolve(strict=True)
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise CanonicalArchiveError("archive output already exists")
    output_parent = output.parent.resolve(strict=True)
    if not output_parent.is_dir():
        raise CanonicalArchiveError("archive output parent is not a directory")
    output = output_parent / output.name

    head = _run_git(
        root, "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"
    ).stdout.decode("ascii", "strict").strip()
    if head != revision:
        raise CanonicalArchiveError("repository HEAD is not the requested exact revision")

    identity = _write_archive(root, revision, output, prefix="source/")
    try:
        source_tree_sha256 = verify_transport_against_bundle(output, bundle, revision)
        return CanonicalArchiveResult(
            revision=revision,
            archive_sha256=_sha256(output),
            source_tree_sha256=source_tree_sha256,
            archive_bytes=output.stat().st_size,
        )
    except BaseException:
        _remove_created(output, identity)
        raise


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(sys.argv[1:] if argv is None else argv)
    try:
        result = generate_canonical_release_archive(
            arguments.root,
            arguments.revision,
            arguments.bundle,
            arguments.output,
        )
    except CanonicalArchiveError as exc:
        print(f"CANONICAL_RELEASE_ARCHIVE_FAILED|{exc}", file=sys.stderr)
        return 1
    print(
        "CANONICAL_RELEASE_ARCHIVE_READY|"
        f"revision={result.revision}|sha256={result.archive_sha256}|"
        f"tree={result.source_tree_sha256}|bytes={result.archive_bytes}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
