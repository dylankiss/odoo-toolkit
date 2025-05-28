import contextlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import chain
from pathlib import Path
from typing import Annotated

from git import GitCommandError, InvalidGitRepositoryError, Repo
from typer import Argument, Option, Typer

from odoo_toolkit.common import TransientProgress, print, print_command_title, print_error, print_success

from .common import MULTI_BRANCH_REPOS, SINGLE_BRANCH_REPOS, OdooRemote

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
) -> None:
    """Switch branches :twisted_rightwards_arrows: inside an Odoo Multiverse branch directory.

    This will try to pull the given branch for every repository inside the current branch directory and switch to it. If
    the given branch doesn't exist on the remote for a repository, we will just pull the current branch's latest
    changes.\n
    \n
    A common use-case is to switch to a specific task's branches.
    """
    print_command_title(":inbox_tray: Switch Multiverse Branches")

    if branch.startswith("odoo-dev:"):
        remote, branch = OdooRemote.DEV, branch[9:]
    elif branch.startswith("odoo:"):
        remote, branch = OdooRemote.ORIGIN, branch[5:]

    branch_dir = Path.cwd()
    repo_dirs = [
        branch_dir / repo.value
        for repo in chain(MULTI_BRANCH_REPOS, SINGLE_BRANCH_REPOS)
        if (branch_dir / repo.value).is_dir()
    ]
    switched_repos = set[str]()

    with ThreadPoolExecutor() as executor, TransientProgress() as progress:
        progress_task = progress.add_task(f"Switching to branch [b]{branch}[/b]", total=len(repo_dirs))
        future_to_repo = {
            executor.submit(_switch_branch_for_repo, repo_dir, branch, remote and remote.value, progress): repo_dir.name
            for repo_dir in repo_dirs
        }

        for future in as_completed(future_to_repo):
            with contextlib.suppress(Exception):
                if future.result():
                    switched_repos.add(future_to_repo[future])
            progress.advance(progress_task, 1)

    if not switched_repos:
        if remote:
            print_error(
                f"The branch [b]{branch}[/b] could not be found on remote [b]{remote.value}[/b] for any repository.\n",
            )
        else:
            print_error(f"The branch [b]{branch}[/b] could not be found on any remote for any repository.\n")
    elif len(switched_repos) > 1:
        print_success(
            f"Repositories [b]{'[/b], [b]'.join(switched_repos)}[/b] were switched to branch [b]{branch}[/b].\n",
        )
    else:
        print_success(f"Repository [b]{next(iter(switched_repos))}[/b] was switched to branch [b]{branch}[/b].\n")


def _switch_branch_for_repo(repo_dir: Path, branch: str, remote: str | None, progress: TransientProgress) -> bool:
    try:
        repo = Repo(repo_dir)

        if remote and all(r.name != remote for r in repo.remotes):
            # If the remote does not exist for this repo, fallback to the first existing remote.
            remote = repo.remotes[0].name

        if not remote:
            # If no remote is given, try to find the first remote containing the requested branch.
            # If no remote containing the branch can be found, take the first remote of the repo.
            remote = repo.remotes[0].name
            for r in repo.remotes:
                if repo.git.ls_remote("--heads", r, branch):
                    remote = r.name
                    break

        if not repo.git.ls_remote("--heads", remote, branch):
            # Branch does not exist on remote. Just pull the latest changes.
            progress_task = progress.add_task(f"Pulling latest changes for [b]{repo_dir.name}[/b]", total=1)
            repo.git.pull()
            progress.advance(progress_task, 1)
            return False

        progress_task = progress.add_task(f"Switching [b]{repo_dir.name}[/b] to [b]{branch}[/b]", total=3)
        if repo.is_dirty(untracked_files=True):
            # Stash potential changes before switching.
            repo.git.stash("push", "--include-untracked")
            print(f":arrows_counterclockwise: Stashed dirty changes in [u]{repo_dir}[/u] before resetting.")
        # Pull latest refs from the remote.
        repo.remote(remote).fetch(branch)
        progress.advance(progress_task, 1)
        # Check if the branch exists locally
        if branch in repo.heads:
            repo.git.checkout(branch)
        else:
            # Create a new tracking branch from the remote branch
            repo.git.checkout("-b", branch, f"{remote}/{branch}")
        progress.advance(progress_task, 1)
        # Pull the latest changes
        repo.git.pull(remote, branch)
        progress.advance(progress_task, 1)
    except (GitCommandError, InvalidGitRepositoryError):
        return False
    return True
