import contextlib
import os
import re
import shutil
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from rich.table import Table

from odoo_toolkit.common import (
    TransientProgress,
    console,
    print_error,
    print_success,
    print_warning,
)
from odoo_toolkit.git_cli import Git, GitError

# `git ls-remote --exit-code` exits with this code when no matching ref was found.
LS_REMOTE_NO_MATCH = 2


class Outcome(Enum):
    """The outcome of an operation on a single repository."""

    UPDATED = "updated"
    SKIPPED = "skipped"
    # Needs the user's attention, but is not an error (e.g. local commits prevent a fast-forward).
    WARNING = "warning"
    FAILED = "failed"


@dataclass
class RepoResult:
    """The result of an operation on a single repository."""

    name: str
    outcome: Outcome
    message: str
    stash: str | None = None
    # Whether the requested branch exists for this repository, when switching branches.
    branch_found: bool = False


# The files Git keeps while an operation that needs your attention is in progress.
IN_PROGRESS = {
    "rebase-merge": "rebase",
    "rebase-apply": "rebase",
    "MERGE_HEAD": "merge",
    "CHERRY_PICK_HEAD": "cherry-pick",
    "REVERT_HEAD": "revert",
}


def preauthenticate_ssh(repo_dirs: Iterable[Path]) -> None:
    """Make one lightweight remote call before running Git operations in parallel.

    When an SSH agent (e.g. Bitwarden) requires interactive confirmation per session, parallel Git calls would race for
    the prompt and all but one would fail. One call upfront loads the key into the agent.

    :param repo_dirs: The repositories that will be accessed. Only the first one is used.
    """
    if repo_dir := next(iter(repo_dirs), None):
        with contextlib.suppress(GitError):
            Git(repo_dir)("ls-remote", "origin", "HEAD")


def get_operation_in_progress(repo: Git) -> str | None:
    """Get the operation in progress in a working tree, like `rebase` or `merge`, which must be finished first."""
    for path, operation in IN_PROGRESS.items():
        if Path(repo("rev-parse", "--path-format=absolute", "--git-path", path)).exists():
            return operation
    return None


def branch_refspec(remote: str, branch: str) -> str:
    """Get the refspec that fetches one branch (or branch pattern) of a remote into its remote-tracking branch."""
    return f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"


def remote_has_branch(repo: Git, remote: str, branch: str) -> bool:
    """Check whether a branch exists on a remote, matching the exact branch name.

    :raises GitError: If the remote could not be reached.
    """
    try:
        repo("ls-remote", "--exit-code", "--heads", remote, f"refs/heads/{branch}")
    except GitError as e:
        if e.returncode == LS_REMOTE_NO_MATCH:
            return False
        raise
    return True


def get_known_default_branch(repo: Git, remote: str = "origin") -> str | None:
    """Get the default branch of a remote as the repository knows it, without asking GitHub."""
    with contextlib.suppress(GitError):
        return repo("symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD").removeprefix(f"{remote}/")
    # A bare clone has no remote-tracking branches, but its own HEAD is the default branch.
    if repo("rev-parse", "--is-bare-repository") == "true":
        with contextlib.suppress(GitError):
            return repo("symbolic-ref", "--short", "HEAD")
    return None


def get_remote_default_branch(repo: Git, remote: str = "origin") -> str | None:
    """Get the current default branch of a remote on GitHub, or None if it can't be reached."""
    try:
        output = repo("ls-remote", "--symref", remote, "HEAD")
    except GitError:
        return None
    match = re.search(r"^ref: refs/heads/(\S+)\s+HEAD$", output, re.MULTILINE)
    return match.group(1) if match else None


def get_default_branch(repo: Git, remote: str = "origin") -> str:
    """Get the default branch of a remote, falling back to `master`."""
    return get_known_default_branch(repo, remote) or get_remote_default_branch(repo, remote) or "master"


def get_other_worktree_using_branch(repo: Git, branch: str) -> Path | None:
    """Get the path of another worktree that has the given branch checked out, if any.

    Git refuses to check out a branch in two worktrees at the same time.
    """
    own_path = Path(repo("rev-parse", "--show-toplevel")).resolve()
    for block in repo("worktree", "list", "--porcelain").split("\n\n"):
        lines = block.splitlines()
        if f"branch refs/heads/{branch}" not in lines:
            continue
        path = Path(lines[0].removeprefix("worktree ")).resolve()
        if path != own_path:
            return path
    return None


def stash_if_dirty(repo: Git, label: str) -> str | None:
    """Stash all uncommitted changes, including untracked files, under a label.

    The stash list is shared by all worktrees of a repository, so the label tells where the changes came from.

    :return: The label if anything was stashed, None otherwise.
    """
    if not repo.is_dirty():
        return None
    repo("stash", "push", "--include-untracked", "--message", label)
    return label


def fetch_branch(repo: Git, remote: str, branch: str) -> None:
    """Fetch a single branch into its remote-tracking ref."""
    repo("fetch", remote, branch_refspec(remote, branch))


def checkout_branch(repo: Git, remote: str, branch: str) -> bool:
    """Check out a branch, creating it to track the remote branch if it doesn't exist locally.

    :return: True if the branch was newly created, False if it already existed.
    """
    if not repo.has_branch(branch):
        repo("checkout", "-b", branch, "--track", f"{remote}/{branch}")
        return True
    if repo.current_branch() != branch:
        repo("checkout", branch)
    return False


def checkout_and_hard_reset(repo: Git, remote: str, branch: str) -> tuple[Outcome, str]:
    """Check out a branch and make it identical to the remote branch, discarding any local commits.

    Discarded commits stay recoverable through the reflog, so the message mentions the previous commit.

    :return: The outcome and a message describing what happened.
    """
    upstream = f"{remote}/{branch}"
    checkout_branch(repo, remote, branch)
    before = repo.head()
    discarded = int(repo("rev-list", "--count", f"{upstream}..HEAD"))
    repo("reset", "--hard", upstream)
    after = repo.head()

    if discarded:
        return Outcome.WARNING, (
            f"[b]{branch}[/b] reset to [b]{upstream}[/b] ({after[:8]}), discarding {discarded} local commit(s). "
            f"Recover them with [b]git reset --hard {before[:8]}[/b]"
        )
    if before == after:
        return Outcome.UPDATED, f"[b]{branch}[/b] is up to date with [b]{upstream}[/b] ({after[:8]})"
    return Outcome.UPDATED, f"[b]{branch}[/b] updated to [b]{upstream}[/b] ({before[:8]} → {after[:8]})"


def remove_python_leftovers(work_dir: Path) -> int:
    """Remove `__pycache__` directories, and the directories left empty by removing them.

    These are what's left of modules that were deleted or moved, which Git doesn't remove because they are ignored.
    Other ignored files (like local configuration or JS tooling) are kept.

    :param work_dir: The working tree to clean.
    :return: The number of removed directories, not counting the `__pycache__` ones.
    """
    work_dir = work_dir.resolve()
    pycache_dirs: list[Path] = []
    for root, dirs, _ in os.walk(work_dir):
        if "__pycache__" in dirs:
            pycache_dirs.append(Path(root) / "__pycache__")
        # Don't descend into Git metadata, dependencies or the caches themselves.
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__"}]

    removed = 0
    for pycache_dir in pycache_dirs:
        shutil.rmtree(pycache_dir, ignore_errors=True)
        parent = pycache_dir.parent
        while parent != work_dir and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            removed += 1
            parent = parent.parent
    return removed


def fast_forward(repo: Git, upstream: str) -> tuple[Outcome, str]:
    """Fast-forward the current branch to the given upstream, without ever creating a merge commit.

    :return: The outcome and a message describing what happened.
    """
    branch = repo.current_branch()
    before = repo.head()
    try:
        repo("merge", "--ff-only", upstream)
    except GitError:
        if int(repo("rev-list", "--count", f"{upstream}..HEAD")):
            return Outcome.WARNING, (
                f"[b]{branch}[/b] has diverged from [b]{upstream}[/b], left it as is. "
                "Rebase or reset it yourself if you want to drop your local commits."
            )
        return Outcome.WARNING, (
            f"[b]{branch}[/b] can't be fast-forwarded to [b]{upstream}[/b], because your uncommitted changes touch the "
            "same files. Commit or stash them, and run the command again."
        )
    after = repo.head()
    if before == after:
        return Outcome.UPDATED, f"[b]{branch}[/b] is up to date with [b]{upstream}[/b] ({after[:8]})"
    return Outcome.UPDATED, f"[b]{branch}[/b] fast-forwarded to [b]{upstream}[/b] ({before[:8]} → {after[:8]})"


def find_stash(repo: Git, label: str) -> str | None:
    """Find the stash with the given label, like `stash@{2}`. Stashes shift when new ones are added."""
    with contextlib.suppress(GitError):
        for line in repo("stash", "list", "--format=%gd %gs").splitlines():
            ref, _, subject = line.partition(" ")
            # Git prefixes the label with the branch, like `On 19.0: otk: ...`.
            if subject.endswith(f": {label}"):
                return ref
    return None


def run_on_repos(
    repo_dirs: list[Path],
    operation: Callable[[Path], RepoResult],
    description: str,
    *,
    names: dict[Path, str] | None = None,
) -> list[RepoResult]:
    """Run an operation on every repository in parallel, turning unexpected errors into failed results.

    :param repo_dirs: The repositories to run the operation on.
    :param operation: The operation to run, given the repository directory.
    :param description: The progress bar description.
    :param names: The names to report the repositories under, if not their directory name, like for bare ones.
    :return: The results, in the order of the given repositories.
    """
    names = names or {}
    results: dict[Path, RepoResult] = {}
    with ThreadPoolExecutor() as executor, TransientProgress() as progress:
        task = progress.add_task(description, total=len(repo_dirs))
        futures = {
            executor.submit(
                run_safely,
                names.get(repo_dir, repo_dir.name),
                lambda repo_dir=repo_dir: operation(repo_dir),
            ): repo_dir
            for repo_dir in repo_dirs
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
            progress.advance(task)
    return [results[repo_dir] for repo_dir in repo_dirs]


def run_safely(name: str, operation: Callable[[], RepoResult]) -> RepoResult:
    """Run an operation, turning unexpected errors into a failed result.

    :param name: The name to report a failure under.
    :param operation: The operation to run.
    :return: The operation's result, or a failed result if it raised an error.
    """
    try:
        return operation()
    except GitError as e:
        return RepoResult(name, Outcome.FAILED, e.message)
    except Exception as e:  # noqa: BLE001
        return RepoResult(name, Outcome.FAILED, f"unexpected error: {e}")


def print_result_table(results: list[RepoResult]) -> None:
    """Print a table with one row per result."""
    icons = {
        Outcome.UPDATED: ":white_check_mark:",
        Outcome.SKIPPED: ":heavy_minus_sign:",
        Outcome.WARNING: ":warning:",
        Outcome.FAILED: ":x:",
    }
    styles = {Outcome.WARNING: "yellow", Outcome.FAILED: "red"}
    table = Table(box=None, show_header=False, pad_edge=False)
    for result in results:
        table.add_row(
            icons[result.outcome],
            f"[b]{result.name}[/b]",
            result.message,
            style=styles.get(result.outcome),
        )
    console.print(table, "")


def print_results(results: list[RepoResult], version_dir: Path, *, error: str | None = None) -> bool:
    """Print a summary table of the results, followed by any stashed changes and how to restore them.

    :param results: The results of the operation on each repository.
    :param version_dir: The version directory the repositories are in.
    :param error: Why the operation as a whole failed, even if no repository did, printed instead of "Done.".
    :return: True if every operation succeeded or was skipped, False if any failed.
    """
    print_result_table(results)

    if stashed := [result for result in results if result.stash]:
        print_warning("Uncommitted changes were stashed. Restore them with:")
        for result in stashed:
            repo_dir = version_dir / result.name
            ref = find_stash(Git(repo_dir), result.stash or "") or "stash@{0}"
            # Don't wrap the command, so it can be copied as is.
            console.print(
                f"  [b]git -C {repo_dir} stash pop {ref}[/b]  [d]# {result.stash}[/d]", highlight=False, soft_wrap=True
            )
        console.print()

    failed = [result for result in results if result.outcome == Outcome.FAILED]
    if failed:
        print_error(f"{len(failed)} of {len(results)} repositories failed.\n")
    if error:
        print_error(f"{error}\n")
    elif not failed:
        print_success("Done.\n")
    return not failed and not error
