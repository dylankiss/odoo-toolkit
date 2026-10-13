from pathlib import Path
from typing import Annotated

from typer import Argument, Exit, Option, Typer

from odoo_toolkit.common import print, print_command_title, print_error
from odoo_toolkit.git_cli import Git, GitError

from .config import find_version_dir, get_remote_owner, get_repo_dirs
from .remotes import fetch_on_demand, refresh_fetching
from .repos import (
    Outcome,
    RepoResult,
    checkout_branch,
    fast_forward,
    fetch_branch,
    get_operation_in_progress,
    get_other_worktree_using_branch,
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
            "The branch can be prefixed with its GitHub owner, like `odoo-dev:master-fix-xyz`, to only look for it on "
            "the remotes of that owner.",
        ),
    ],
    include_linked: Annotated[
        bool,
        Option(
            "--all",
            help="Switch all repositories in the version directory, including the linked ones (like `upgrade` or "
            "`odoofin`). Other versions use them too, so this affects them as well.",
        ),
    ] = False,
) -> None:
    """Switch branches :twisted_rightwards_arrows: inside an Odoo Multiverse version directory.

    Run this command anywhere inside one of the multiverse version directories (like `19.0`). Every repository that has
    the given branch on one of its remotes (`origin` first) or locally is switched to it and fast-forwarded. The other
    repositories stay on their current branch, which is fast-forwarded to its upstream.\n
    \n
    A common use-case is to switch to a specific task's branches.\n
    \n
    Uncommitted changes are stashed with a label telling where they came from, so you can restore them later. Local
    commits are never discarded: if a branch has diverged from its remote, it is left as is and reported.
    """
    print_command_title(":twisted_rightwards_arrows: Switch Multiverse Branches")

    owner, _, branch = branch.rpartition(":")

    version_dir = find_version_dir()
    if not version_dir:
        print_error("Run this command inside an Odoo Multiverse version directory (like [u]~/code/odoo-mv/19.0[/u]).\n")
        raise Exit(1)

    repo_dirs = get_repo_dirs(version_dir, include_linked=include_linked)
    print(f"Switching the repositories in [u]{version_dir}[/u] to [b]{branch}[/b]\n")

    preauthenticate_ssh(repo_dirs)
    results = run_on_repos(
        repo_dirs,
        lambda repo_dir: switch_repo(repo_dir, branch=branch, owner=owner or None, version=version_dir.name),
        f"Switching to {branch}",
    )

    success = print_results(results, version_dir)
    if not any(result.branch_found for result in results):
        where = f"the remotes of [b]{owner}[/b]" if owner else "any remote or locally"
        print_error(f"The branch [b]{branch}[/b] could not be found on {where} for any repository.\n")
        success = False
    if not success:
        raise Exit(1)


def switch_repo(repo_dir: Path, *, branch: str, owner: str | None = None, version: str) -> RepoResult:
    """Switch a repository to the given branch, or fast-forward its current branch if the branch doesn't exist.

    :param repo_dir: The repository directory.
    :param branch: The branch to switch to.
    :param owner: Only look on the remotes of this GitHub owner, like `odoo-dev`, or None to look on every remote.
    :param version: The multiverse version, used to label stashed changes.
    :return: The result of the switch.
    """
    repo = Git(repo_dir)
    name = repo_dir.name
    if operation := get_operation_in_progress(repo):
        return RepoResult(name, Outcome.FAILED, f"a {operation} is in progress: finish or abort it first")

    found_remote, reason = _find_remote(repo, branch, owner)
    if not found_remote and not (repo.has_branch(branch) and not owner):
        return _update_current_branch(repo, name, reason=reason)

    if found_remote:
        # Make sure the branch can be checked out before touching any uncommitted changes, and keep fetching it from
        # now on, so Git can tell how far ahead or behind it is. Git only tracks branches its remotes fetch.
        fetch_branch(repo, found_remote, branch)
        fetch_on_demand(repo, found_remote, branch)
    stash = None
    if repo.current_branch() != branch:
        if other_worktree := get_other_worktree_using_branch(repo, branch):
            return RepoResult(
                name, Outcome.FAILED, f"[b]{branch}[/b] is checked out in [u]{other_worktree}[/u]", branch_found=True,
            )
        stash = stash_if_dirty(repo, f"otk: {version}/{name} before switch to {branch}")

    if not found_remote:
        checkout_branch(repo, "", branch)
        message = f"switched to [b]{branch}[/b], which only exists locally"
        return RepoResult(name, Outcome.WARNING, message, stash, branch_found=True)
    created = checkout_branch(repo, found_remote, branch)
    # Stop fetching branches that were deleted on GitHub.
    refresh_fetching(repo)
    if created:
        outcome, message = Outcome.UPDATED, f"checked out [b]{found_remote}/{branch}[/b] ({repo.head()[:8]})"
    else:
        outcome, message = fast_forward(repo, f"{found_remote}/{branch}")
    return RepoResult(name, outcome, message, stash, branch_found=True)


def _find_remote(repo: Git, branch: str, owner: str | None) -> tuple[str | None, str]:
    """Find the first remote having a branch, looking on `origin` first: the `dev` fork can have outdated copies.

    :param owner: Only look on the remotes of this GitHub owner, or None to look on every remote.
    :return: The remote, or None if none has it, and why not.
    """
    candidates = sorted(repo.remotes(), key=lambda remote: remote != "origin")
    if owner:
        candidates = [r for r in candidates if get_remote_owner(repo.remote_url(r)) == owner]
        if not candidates:
            return None, f"no remote of [b]{owner}[/b]"
    unreachable: list[str] = []
    for candidate in candidates:
        try:
            if remote_has_branch(repo, candidate, branch):
                return candidate, ""
        except GitError:
            unreachable.append(candidate)
    reason = f"[b]{branch}[/b] not found on {', '.join(candidates)}"
    return None, reason + (f" (couldn't reach {', '.join(unreachable)})" if unreachable else "")


def _update_current_branch(repo: Git, name: str, reason: str) -> RepoResult:
    """Fast-forward the current branch to its upstream, for repositories that don't have the requested branch."""
    upstream = repo.upstream()
    if not upstream:
        return RepoResult(name, Outcome.SKIPPED, f"{reason}; current branch has no upstream to update from")

    remote, remote_branch = upstream
    if not remote_has_branch(repo, remote, remote_branch):
        refresh_fetching(repo)
        return RepoResult(
            name, Outcome.SKIPPED, f"{reason}; the upstream [b]{remote}/{remote_branch}[/b] was deleted on GitHub",
        )
    fetch_branch(repo, remote, remote_branch)
    outcome, message = fast_forward(repo, f"{remote}/{remote_branch}")
    return RepoResult(name, Outcome.SKIPPED if outcome == Outcome.UPDATED else outcome, f"{reason}; {message}")
