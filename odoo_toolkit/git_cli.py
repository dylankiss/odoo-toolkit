"""A thin wrapper around the `git` command line."""

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

# Matches the progress lines of `git clone --progress`, like "Receiving objects:  45% (450/1000), 1.20 MiB".
PROGRESS_PATTERN = re.compile(r"(?P<stage>[A-Z][\w ]+):\s+\d+% \((?P<current>\d+)/(?P<total>\d+)\)")

# A clone progress callback, receiving the current stage, like "Receiving objects", and the current and total counts.
CloneProgress = Callable[[str, int, int], None]


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

    def __call__(self, *args: str) -> str:
        """Run a Git command.

        :param args: The arguments to pass to `git`, like `"checkout", "master"`.
        :raises GitError: If the command failed, including when the path is not a Git repository.
        :return: The output of the command, without leading and trailing whitespace.
        """
        result = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True, check=False)
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
    cmd = ["clone", "--progress", *args, url, str(path)]
    with subprocess.Popen(
        ["git", *cmd], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    ) as process:
        assert process.stderr  # noqa: S101 (always set with stderr=PIPE)
        output: list[str] = []
        line = ""
        # Progress lines are rewritten in place using carriage returns, so read character by character.
        while char := process.stderr.read(1):
            if char not in "\r\n":
                line += char
                continue
            if progress and (match := PROGRESS_PATTERN.search(line)):
                progress(match.group("stage"), int(match.group("current")), int(match.group("total")))
            elif line.strip():
                output.append(line)
            line = ""
    if process.returncode:
        raise GitError(cmd, process.returncode, "\n".join([*output, line]))
    return Git(path)
