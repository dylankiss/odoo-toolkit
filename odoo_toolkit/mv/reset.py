from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import chain
from pathlib import Path

from git import GitCommandError, InvalidGitRepositoryError, Repo
from typer import Typer

from odoo_toolkit.common import (
    Status,
    StickyProgress,
    print,
    print_command_title,
    print_error,
    print_success,
    print_warning,
)

from .common import MULTI_BRANCH_REPOS, SINGLE_BRANCH_REPOS

app = Typer()


@app.command()
def reset() -> None:
    """Reset :arrows_counterclockwise: the repositories inside an Odoo Multiverse branch directory.

    You can run this command inside one of the multiverse directories (corresponding to a branch). It will go through
    all repositories inside the directory and reset them to their original branch.\n
    \n
    Meanwhile, it will pull the latest changes from `origin` so you're ready to start with a clean slate.
    """
    print_command_title(":arrows_counterclockwise: Reset Multiverse Branch Repositories")

    branch_dir = Path.cwd()
    branch = branch_dir.name
    repo_dirs = [
        (branch_dir / repo.value, branch if repo in MULTI_BRANCH_REPOS else None)
        for repo in chain(MULTI_BRANCH_REPOS, SINGLE_BRANCH_REPOS)
        if (branch_dir / repo.value).is_dir()
    ]

    status = None

    # Run the Git operations in parallel to speed things up.
    with ThreadPoolExecutor() as executor, StickyProgress() as progress:
        future_to_repo = {
            executor.submit(_reset_repo, repo_dir=repo_dir, branch=repo_branch, progress=progress): repo_dir
            for repo_dir, repo_branch in repo_dirs
        }

        for future in as_completed(future_to_repo):
            repo_dir = future_to_repo[future]
            try:
                reset_status = future.result()
            except Exception as e:  # noqa: BLE001
                reset_status = Status.FAILURE
                print_warning(f"Resetting the repository [u]{repo_dir}[/u] failed: {e}")

            status = Status.PARTIAL if status and status != reset_status else reset_status

    print()
    match status:
        case Status.SUCCESS:
            print_success("All repositories have been correctly reset!\n")
        case Status.PARTIAL:
            print_warning("Some repositories have been correctly reset, while others haven't!\n")
        case _:
            print_error("No repositories were reset!\n")


def _reset_repo(*, repo_dir: Path, branch: str | None = None, progress: StickyProgress) -> Status:
    status = Status.FAILURE
    progress_task = progress.add_task(f"Resetting [u]{repo_dir}[/u]", total=3)

    try:
        repo = Repo(repo_dir)

        if repo.is_dirty(untracked_files=True):
            # Stash potential changes before resetting.
            repo.git.stash("push", "--include-untracked")
            print(f":arrows_counterclockwise: Stashed dirty changes in [u]{repo_dir}[/u] before resetting.")

        progress.advance(progress_task, 1)

        if not branch:
            # If no branch is given, try to find the default branch or else default to "master".
            try:
                branch = repo.git.remote("show", "origin").split("HEAD branch:")[-1].split()[0]
            except GitCommandError:
                branch = "master"

        progress.update(progress_task, description=f"Resetting [u]{repo_dir}[/u] to [b]{branch}[/b]", advance=1)

        # Checkout the branch and pull the latest changes.
        repo.git.checkout(branch)
        repo.git.pull("origin", branch)

        progress.advance(progress_task, 1)
        status = Status.SUCCESS

    except InvalidGitRepositoryError:
        print_warning(f"The directory [u]{repo_dir}[/u] is not a Git repository. Skipping ...\n")

    except GitCommandError as e:
        print_error(
            f"Resetting the repository [u]{repo_dir}[/u] failed with [b]{e.status}[/b]. "
            f"The command that failed was:\n\n[b]{e.command}[/b]",
            e.stderr.strip(),
        )

    return status
