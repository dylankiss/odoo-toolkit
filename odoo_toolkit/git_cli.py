"""A thin wrapper around the `git` command line."""

import os
import re
import subprocess
from collections.abc import Callable, Iterator
from functools import cache
from pathlib import Path

try:
    import pty
except ImportError:  # Windows
    pty = None

# Matches the progress lines of `git clone --progress`, like "Receiving objects:  45% (450/1000), 1.20 MiB".
PROGRESS_PATTERN = re.compile(r"(?P<stage>[A-Z][\w ]+):\s+\d+% \((?P<current>\d+)/(?P<total>\d+)\)")

# A clone progress callback, receiving the current stage, like "Receiving objects", and the current and total counts.
CloneProgress = Callable[[str, int, int], None]


@cache
def get_git_version() -> tuple[int, int]:
    """Get the major and minor version of the installed Git, like `(2, 49)`, or `(0, 0)` if it can't be found."""
    try:
        output = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False).stdout
    except OSError:
        return (0, 0)
    match = re.search(r"(\d+)\.(\d+)", output)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


class GitError(Exception):
    """A Git command failed."""

    def __init__(self, args: list[str], returncode: int, stderr: str) -> None:
        """Initialize the error.

        :param args: The arguments passed to `git`.
        :param returncode: The exit code of the command.
        :param stderr: The error output of the command.
        """
        self.git_args = args
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(self.message)

    @property
    def message(self) -> str:
        """The most relevant line of the error output."""
        lines = [line.strip() for line in self.stderr.splitlines() if line.strip()]
        relevant = [line for line in lines if line.startswith(("fatal:", "error:"))]
        fallback = f"`git {self.git_args[0] if self.git_args else ''}` failed with exit code {self.returncode}"
        return (relevant or lines or [fallback])[0]


class Git:
    """Run Git commands in a repository or worktree."""

    def __init__(self, path: Path) -> None:
        """Initialize the wrapper.

        :param path: The repository, worktree or bare repository to run the commands in.
        """
        self.path = path

    def __call__(self, *args: str, stdin: str | None = None) -> str:
        """Run a Git command.

        :param args: The arguments to pass to `git`, like `"checkout", "master"`.
        :param stdin: The input to pass to the command, if any.
        :raises GitError: If the command failed, including when the path is not a Git repository.
        :return: The output of the command, without leading and trailing whitespace.
        """
        result = subprocess.run(
            ["git", "-C", str(self.path), *args],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise GitError(list(args), result.returncode, result.stderr)
        return result.stdout.strip()

    def is_dirty(self) -> bool:
        """Check whether there are uncommitted changes, including untracked files."""
        return bool(self("status", "--porcelain"))

    def head(self) -> str:
        """Get the commit hash of `HEAD`."""
        return self("rev-parse", "HEAD")

    def current_branch(self) -> str | None:
        """Get the checked out branch, or None on a detached `HEAD`."""
        try:
            return self("symbolic-ref", "--quiet", "--short", "HEAD")
        except GitError:
            return None

    def has_branch(self, branch: str) -> bool:
        """Check whether a local branch exists."""
        try:
            self("show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
        except GitError:
            return False
        return True

    def upstream(self) -> tuple[str, str] | None:
        """Get the remote and remote branch the checked out branch tracks, like `("origin", "19.0")`.

        :return: The remote and branch, or None on a detached `HEAD` or a branch without upstream.
        """
        branch = self.current_branch()
        if not branch:
            return None
        try:
            remote = self("config", f"branch.{branch}.remote")
            remote_branch = self("config", f"branch.{branch}.merge").removeprefix("refs/heads/")
        except GitError:
            return None
        return remote, remote_branch

    def remotes(self) -> list[str]:
        """Get the names of the remotes, in the order they are configured."""
        return self("remote").splitlines()

    def remote_url(self, remote: str) -> str:
        """Get the URL of a remote."""
        return self("remote", "get-url", remote)


def clone(url: str, path: Path, *args: str, progress: CloneProgress | None = None) -> Git:
    """Clone a repository.

    :param url: The URL to clone from.
    :param path: The directory to clone into.
    :param args: Extra arguments for `git clone`, like `--bare`.
    :param progress: Called with the progress of the clone.
    :raises GitError: If the clone failed.
    :return: The cloned repository.
    """
    run_with_progress(["clone", "--progress", *args, url, str(path)], progress)
    return Git(path)


def run_with_progress(
    cmd: list[str],
    progress: CloneProgress | None,
    *,
    cwd: Path | None = None,
    tty: bool = False,
) -> None:
    """Run a Git command, reporting the progress it prints.

    :param cmd: The arguments to pass to `git`.
    :param progress: Called with the progress of the command.
    :param cwd: The repository to run it in, or None for the current directory.
    :param tty: Whether to run it in a terminal, which some commands need to print their progress, like `backfill`.
    :raises GitError: If the command failed.
    """
    master_fd = None
    stderr: int = subprocess.PIPE
    if tty and pty:
        master_fd, stderr = pty.openpty()
    with subprocess.Popen(
        ["git", *cmd],
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=stderr,
        text=master_fd is None,
    ) as process:
        if master_fd is not None:
            os.close(stderr)
        output: list[str] = []
        line = ""
        # Progress lines are rewritten in place using carriage returns, so read character by character.
        for char in _read_chars(process, master_fd):
            if char not in "\r\n":
                line += char
                continue
            if progress and (match := PROGRESS_PATTERN.search(line)):
                progress(match.group("stage"), int(match.group("current")), int(match.group("total")))
            elif line.strip():
                output.append(line)
            line = ""
    if master_fd is not None:
        os.close(master_fd)
    if process.returncode:
        raise GitError(cmd, process.returncode, "\n".join([*output, line]))


def _read_chars(process: subprocess.Popen[str], master_fd: int | None) -> Iterator[str]:
    """Read the error output of a process character by character, from its pipe or its terminal."""
    if master_fd is None:
        assert process.stderr  # noqa: S101 (always set with stderr=PIPE)
        while char := process.stderr.read(1):
            yield char
        return
    while True:
        try:
            data = os.read(master_fd, 4096)
        except OSError:
            # The terminal reports its end like this on Linux.
            return
        if not data:
            return
        yield from data.decode(errors="replace")
