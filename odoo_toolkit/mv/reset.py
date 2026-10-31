from pathlib import Path
from typing import Annotated

from typer import Exit, Option, Typer, confirm

from odoo_toolkit.common import print, print_command_title, print_error, print_warning
from odoo_toolkit.git_cli import Git

from .config import NOT_IN_VERSION_DIR, find_version_dir, get_repo_dirs, is_worktree
from .remotes import refresh_fetching
from .repos import (
    Outcome,
    RepoResult,
    checkout_and_hard_reset,
    count_local_commits,
    fetch_branch,
    get_default_branch,
    get_operation_in_progress,
    get_other_worktree_using_branch,
    preauthenticate_ssh,
    print_results,
    remove_python_leftovers,
    run_on_repos,
    stash_if_dirty,
)

app = Typer()


@app.command()
def reset(
    include_linked: Annotated[
        bool,
        Option(
            "--all",
            help="Reset all repositories in the version directory, including the linked ones (like `upgrade` or "
            "`odoofin`), which are reset to their default branch. Other versions use them too, so this affects them "
            "as well.",
        ),
    ] = False,
    yes: Annotated[
        bool, Option("--yes", "-y", help="Don't ask before discarding commits that only exist on your machine."),
    ] = False,
) -> None:
    """Reset :arrows_counterclockwise: the repositories inside an Odoo Multiverse version directory.

    Run this command anywhere inside one of the multiverse version directories (like `19.0`). Every versioned
    repository is checked out on the version's branch and reset to the latest state on `origin`, so you start with a
    clean slate.\n
    \n
    Uncommitted changes are stashed with a label telling where they came from, so you can restore them later. Commits
    on the version's branch that only exist on your machine are discarded after asking you, and reported with the
    commit to recover them from. Leftover `__pycache__` folders of deleted or moved modules are removed as well.
    """
    print_command_title(":arrows_counterclockwise: Reset Multiverse Repositories")

    version_dir = find_version_dir()
    if not version_dir:
        print_error(NOT_IN_VERSION_DIR)
        raise Exit(1)

    repo_dirs = get_repo_dirs(version_dir, include_linked=include_linked)
    print(f"Resetting the repositories in [u]{version_dir}[/u]\n")

    preauthenticate_ssh(repo_dirs)
    # Linked repositories are reset to their default branch, worktrees to the version's branch.
    branches = {
        repo_dir: version_dir.name if is_worktree(repo_dir) else get_default_branch(Git(repo_dir))
        for repo_dir in repo_dirs
    }
    checks = run_on_repos(repo_dirs, lambda repo_dir: check_repo(repo_dir, branches[repo_dir]), "Fetching")
    if any(check.outcome == Outcome.FAILED for check in checks):
        print_results(checks, version_dir)
        raise Exit(1)
    if (local_work := [check for check in checks if check.outcome == Outcome.WARNING]) and not yes:
        print_warning("These commits only exist on your machine, and would be discarded:")
        for check in local_work:
            print(f"  [b]{check.name}[/b]: {check.message}")
        print()
        if not confirm("Reset anyway? You can recover them afterwards with the commands shown"):
            raise Exit(1)

    results = run_on_repos(
        repo_dirs,
        lambda repo_dir: reset_repo(repo_dir, branch=branches[repo_dir], version=version_dir.name),
        "Resetting repositories",
    )

    if not print_results(results, version_dir):
        raise Exit(1)


def check_repo(repo_dir: Path, branch: str) -> RepoResult:
    """Fetch the branch a repository will be reset to, and check whether resetting it would discard any work.

    :return: A failed result if the repository can't be reset, a warning listing the commits that only exist on your
        machine, or else a successful result.
    """
    repo = Git(repo_dir)
    name = repo_dir.name
    if operation := get_operation_in_progress(repo):
        return RepoResult(name, Outcome.FAILED, f"a {operation} is in progress: finish or abort it first")
    fetch_branch(repo, "origin", branch)
    if other_worktree := get_other_worktree_using_branch(repo, branch):
        return RepoResult(name, Outcome.FAILED, f"[b]{branch}[/b] is checked out in [u]{other_worktree}[/u]")
    if count := count_local_commits(repo, branch, f"origin/{branch}"):
        log = repo("log", "--format=%h %s", f"origin/{branch}..refs/heads/{branch}").splitlines()
        shown = "".join(f"\n    [d]{line}[/d]" for line in log[:5]) + ("\n    [d]…[/d]" if count > len(log[:5]) else "")
        return RepoResult(name, Outcome.WARNING, f"{count} commit(s) on [b]{branch}[/b]{shown}")
    return RepoResult(name, Outcome.UPDATED, "")


def reset_repo(repo_dir: Path, *, branch: str, version: str) -> RepoResult:
    """Check out a repository on the given branch, reset it to `origin` and remove leftover module folders.

    The branch must have been fetched, with `check_repo`.

    :param repo_dir: The repository directory.
    :param branch: The branch to reset to.
    :param version: The multiverse version, used to label stashed changes.
    :return: The result of the reset.
    """
    repo = Git(repo_dir)
    name = repo_dir.name
    stash = stash_if_dirty(repo, f"otk: {version}/{name} before reset to {branch}")
    outcome, message = checkout_and_hard_reset(repo, "origin", branch)
    # Stop fetching branches that were deleted on GitHub.
    refresh_fetching(repo)
    if removed := remove_python_leftovers(repo_dir):
        message += f"; removed {removed} leftover module folder(s)"
    return RepoResult(name, outcome, message, stash)
