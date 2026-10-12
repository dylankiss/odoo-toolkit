from pathlib import Path
from typing import Annotated

from typer import Exit, Option, Typer

from odoo_toolkit.common import print, print_command_title, print_error
from odoo_toolkit.git_cli import Git

from .config import find_version_dir, get_repo_dirs
from .repos import (
    Outcome,
    RepoResult,
    checkout_and_hard_reset,
    fetch_branch,
    get_default_branch,
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
) -> None:
    """Reset :arrows_counterclockwise: the repositories inside an Odoo Multiverse version directory.

    Run this command anywhere inside one of the multiverse version directories (like `19.0`). Every versioned
    repository is checked out on the version's branch and reset to the latest state on `origin`, so you start with a
    clean slate.\n
    \n
    Uncommitted changes are stashed with a label telling where they came from, so you can restore them later. Local
    commits on the version's branch are discarded, but reported with the commit to recover them from. Leftover
    `__pycache__` folders of deleted or moved modules are removed as well.
    """
    print_command_title(":arrows_counterclockwise: Reset Multiverse Repositories")

    version_dir = find_version_dir(Path.cwd())
    if not version_dir:
        print_error("Run this command inside an Odoo Multiverse version directory (like [u]~/code/odoo-mv/19.0[/u]).\n")
        raise Exit(1)

    repo_dirs = get_repo_dirs(version_dir, include_linked=include_linked)
    print(f"Resetting the repositories in [u]{version_dir}[/u]\n")

    preauthenticate_ssh(repo_dirs)
    results = run_on_repos(
        repo_dirs,
        lambda repo_dir: reset_repo(
            repo_dir,
            # Linked repositories are reset to their default branch, worktrees to the version's branch.
            branch=None if repo_dir.is_symlink() else version_dir.name,
            version=version_dir.name,
        ),
        "Resetting repositories",
    )

    if not print_results(results, version_dir):
        raise Exit(1)


def reset_repo(repo_dir: Path, *, branch: str | None, version: str) -> RepoResult:
    """Check out a repository on the given branch, reset it to `origin` and remove leftover module folders.

    :param repo_dir: The repository directory.
    :param branch: The branch to reset to, or None to use the remote's default branch.
    :param version: The multiverse version, used to label stashed changes.
    :return: The result of the reset.
    """
    repo = Git(repo_dir)
    name = repo_dir.name
    branch = branch or get_default_branch(repo)

    # Make sure the branch can be checked out before touching any uncommitted changes.
    fetch_branch(repo, "origin", branch)
    if other_worktree := get_other_worktree_using_branch(repo, branch):
        return RepoResult(name, Outcome.FAILED, f"[b]{branch}[/b] is checked out in [u]{other_worktree}[/u]")

    stash = stash_if_dirty(repo, f"otk: {version}/{name} before reset to {branch}")
    outcome, message = checkout_and_hard_reset(repo, "origin", branch)
    if removed := remove_python_leftovers(repo_dir):
        message += f"; removed {removed} leftover module folder(s)"
    return RepoResult(name, outcome, message, stash)
