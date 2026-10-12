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

from odoo_toolkit.common import TransientProgress, print, print_error, print_success, print_warning
from odoo_toolkit.git_cli import Git, GitError

from .common import MULTI_BRANCH_REPOS, SINGLE_BRANCH_REPOS, OdooRepo

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


def find_version_dir(start: Path) -> Path | None:
    """Find the multiverse version directory (like `19.0`) containing the given path.

    A version directory is recognized by having at least one multi-branch repository worktree in it.

    :param start: The path to start searching from, usually the current working directory.
    :return: The version directory, or None if the path is not inside one.
    """
    start = start.resolve()
    for directory in (start, *start.parents):
        if any((directory / repo.value / ".git").exists() for repo in MULTI_BRANCH_REPOS):
            return directory
    return None


def get_repo_dirs(version_dir: Path, *, include_shared: bool) -> list[tuple[OdooRepo, Path]]:
    """Get the repositories inside a version directory.

    :param version_dir: The multiverse version directory.
    :param include_shared: Whether to include the single-branch repositories, which are shared by all versions.
    :return: The repositories with their directories.
    """
    repos = [*MULTI_BRANCH_REPOS, *(SINGLE_BRANCH_REPOS if include_shared else [])]
    return [(repo, version_dir / repo.value) for repo in repos if (version_dir / repo.value / ".git").exists()]


def preauthenticate_ssh(repo_dirs: Iterable[Path]) -> None:
    """Make one lightweight remote call before running Git operations in parallel.

    When an SSH agent (e.g. Bitwarden) requires interactive confirmation per session, parallel Git calls would race for
    the prompt and all but one would fail. One call upfront loads the key into the agent.

    :param repo_dirs: The repositories that will be accessed. Only the first one is used.
    """
    for repo_dir in repo_dirs:
        with contextlib.suppress(GitError):
            Git(repo_dir)("ls-remote", "origin", "HEAD")
        return


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


def get_default_branch(repo: Git, remote: str = "origin") -> str:
    """Get the default branch of a remote, falling back to `master`."""
    with contextlib.suppress(GitError):
        return repo("symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD").removeprefix(f"{remote}/")
    with contextlib.suppress(GitError):
        match = re.search(r"^ref: refs/heads/(\S+)\s+HEAD$", repo("ls-remote", "--symref", remote, "HEAD"), re.MULTILINE)
        if match:
            return match.group(1)
    return "master"


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
    repo("fetch", remote, f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}")


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


def checkout_and_fast_forward(repo: Git, remote: str, branch: str) -> tuple[Outcome, str]:
    """Check out a branch, creating it to track the remote branch if needed, and fast-forward it.

    Local commits are never discarded: if the local branch has diverged from the remote one, it is left as is.

    :return: The outcome and a message describing what happened.
    """
    upstream = f"{remote}/{branch}"
    if checkout_branch(repo, remote, branch):
        return Outcome.UPDATED, f"checked out [b]{upstream}[/b] ({repo.head()[:8]})"
    return fast_forward(repo, upstream)


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
        return Outcome.WARNING, (
            f"[b]{branch}[/b] has diverged from [b]{upstream}[/b], left it as is. "
            "Rebase or reset it yourself if you want to drop your local commits."
        )
    after = repo.head()
    if before == after:
        return Outcome.UPDATED, f"[b]{branch}[/b] is up to date with [b]{upstream}[/b] ({after[:8]})"
    return Outcome.UPDATED, f"[b]{branch}[/b] fast-forwarded to [b]{upstream}[/b] ({before[:8]} → {after[:8]})"


def run_on_repos(
    repo_dirs: list[tuple[OdooRepo, Path]],
    operation: Callable[[Path], RepoResult],
    description: str,
) -> list[RepoResult]:
    """Run an operation on every repository in parallel, turning unexpected errors into failed results.

    :param repo_dirs: The repositories to run the operation on.
    :param operation: The operation to run, given the repository directory.
    :param description: The progress bar description.
    :return: The results, in the order of the given repositories.
    """
    results: dict[Path, RepoResult] = {}
    with ThreadPoolExecutor() as executor, TransientProgress() as progress:
        task = progress.add_task(description, total=len(repo_dirs))
        futures = {
            executor.submit(run_safely, repo_dir.name, lambda repo_dir=repo_dir: operation(repo_dir)): repo_dir
            for _, repo_dir in repo_dirs
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
            progress.advance(task)
    return [results[repo_dir] for _, repo_dir in repo_dirs]


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
            icons[result.outcome], f"[b]{result.name}[/b]", result.message, style=styles.get(result.outcome),
        )
    print(table, "")


def print_results(results: list[RepoResult], version_dir: Path) -> bool:
    """Print a summary table of the results, followed by any stashed changes.

    :return: True if every operation succeeded or was skipped, False if any failed.
    """
    print_result_table(results)

    if stashed := [result for result in results if result.stash]:
        print_warning("Uncommitted changes were stashed:")
        for result in stashed:
            print(f"  [b]{result.name}[/b]: {result.stash}")
        print(
            f"  Restore them with [b]git -C {version_dir}/<repo> stash pop stash@{{N}}[/b], "
            "finding [b]N[/b] with [b]git stash list[/b].\n",
        )

    failed = [result for result in results if result.outcome == Outcome.FAILED]
    if failed:
        print_error(f"{len(failed)} of {len(results)} repositories failed.\n")
    else:
        print_success("Done.\n")
    return not failed
