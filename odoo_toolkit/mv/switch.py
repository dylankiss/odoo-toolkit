from pathlib import Path
from typing import Annotated

from typer import Argument, Exit, Option, Typer

from odoo_toolkit.common import print, print_command_title, print_error
from odoo_toolkit.git_cli import Git

from .common import OdooRemote
from .remotes import fetch_on_demand
from .repos import (
    Outcome,
    RepoResult,
    checkout_and_fast_forward,
    fast_forward,
    fetch_branch,
    find_version_dir,
    get_other_worktree_using_branch,
    get_repo_dirs,
    preauthenticate_ssh,
    print_results,
    remote_has_branch,
    run_on_repos,
    stash_if_dirty,
)

app = Typer()


@app.command()
def switch(
    branch: Annotated[
        str,
        Argument(
            help="Switch to this branch for all repositories having the branch on their remote. "
            "The branch can be prefixed with its GitHub owner, like `odoo-dev:master-fix-abc`.",
        ),
    ],
    remote: Annotated[
        OdooRemote | None, Option("--remote", "-r", help="Use this local remote for finding the given branch."),
    ] = None,
    include_shared: Annotated[
        bool,
        Option(
            "--shared",
            help="Also switch the single-branch repositories (like `upgrade`). They are shared by all versions, so "
            "this affects every version directory.",
        ),
    ] = False,
) -> None:
    """Switch branches :twisted_rightwards_arrows: inside an Odoo Multiverse version directory.

    Run this command anywhere inside one of the multiverse version directories (like `19.0`). Every repository that has
    the given branch on one of its remotes is switched to it and fast-forwarded. The other repositories stay on their
    current branch, which is fast-forwarded to its upstream.\n
    \n
    A common use-case is to switch to a specific task's branches.\n
    \n
    Uncommitted changes are stashed with a label telling where they came from, so you can restore them later. Local
    commits are never discarded: if a branch has diverged from its remote, it is left as is and reported.
    """
    print_command_title(":twisted_rightwards_arrows: Switch Multiverse Branches")

    if branch.startswith("odoo-dev:"):
        remote, branch = OdooRemote.DEV, branch.removeprefix("odoo-dev:")
    elif branch.startswith("odoo:"):
        remote, branch = OdooRemote.ORIGIN, branch.removeprefix("odoo:")

    version_dir = find_version_dir(Path.cwd())
    if not version_dir:
        print_error("Run this command inside an Odoo Multiverse version directory (like [u]~/code/odoo-mv/19.0[/u]).\n")
        raise Exit(1)

    repo_dirs = get_repo_dirs(version_dir, include_shared=include_shared)
    print(f"Switching the repositories in [u]{version_dir}[/u] to [b]{branch}[/b]\n")

    preauthenticate_ssh(repo_dir for _, repo_dir in repo_dirs)
    results = run_on_repos(
        repo_dirs,
        lambda repo_dir: switch_repo(repo_dir, branch=branch, remote=remote and remote.value, version=version_dir.name),
        f"Switching to {branch}",
    )

    success = print_results(results, version_dir)
    if not any(result.branch_found for result in results):
        where = f"remote [b]{remote.value}[/b]" if remote else "any remote"
        print_error(f"The branch [b]{branch}[/b] could not be found on {where} for any repository.\n")
        success = False
    if not success:
        raise Exit(1)


def switch_repo(repo_dir: Path, *, branch: str, remote: str | None, version: str) -> RepoResult:
    """Switch a repository to the given branch, or fast-forward its current branch if the branch doesn't exist.

    :param repo_dir: The repository directory.
    :param branch: The branch to switch to.
    :param remote: The remote to look for the branch on, or None to look on every remote.
    :param version: The multiverse version, used to label stashed changes.
    :return: The result of the switch.
    """
    repo = Git(repo_dir)
    name = repo_dir.name
    remote_names = repo.remotes()
    candidates = [remote] if remote in remote_names else remote_names
    found_remote = next((r for r in candidates if remote_has_branch(repo, r, branch)), None)

    if not found_remote:
        return _update_current_branch(repo, name, reason=f"[b]{branch}[/b] not found on {', '.join(candidates)}")

    # Make sure the branch can be checked out before touching any uncommitted changes.
    fetch_branch(repo, found_remote, branch)
    # Keep fetching the branch from now on, so Git can tell how far ahead or behind it is.
    fetch_on_demand(repo, found_remote, branch)
    stash = None
    if repo.current_branch() != branch:
        if other_worktree := get_other_worktree_using_branch(repo, branch):
            return RepoResult(
                name, Outcome.FAILED, f"[b]{branch}[/b] is checked out in [u]{other_worktree}[/u]", branch_found=True,
            )
        stash = stash_if_dirty(repo, f"otk: {version}/{name} before switch to {branch}")

    outcome, message = checkout_and_fast_forward(repo, found_remote, branch)
    return RepoResult(name, outcome, message, stash, branch_found=True)


def _update_current_branch(repo: Git, name: str, reason: str) -> RepoResult:
    """Fast-forward the current branch to its upstream, for repositories that don't have the requested branch."""
    upstream = repo.upstream()
    if not upstream:
        return RepoResult(name, Outcome.SKIPPED, f"{reason}; current branch has no upstream to update from")

    remote, remote_branch = upstream
    fetch_branch(repo, remote, remote_branch)
    outcome, message = fast_forward(repo, f"{remote}/{remote_branch}")
    return RepoResult(name, Outcome.SKIPPED if outcome == Outcome.UPDATED else outcome, f"{reason}; {message}")
