import os
from enum import Enum
from pathlib import Path
import re
import subprocess
from subprocess import CalledProcessError, Popen, PIPE
from typing import Annotated

from rich.panel import Panel
from rich.progress import Progress
from typer import Option

from .common import TransientProgress, app, print, console, PROGRESS_COLUMNS, print_command_title, print_error, print_header


class OdooRepo(str, Enum):
    ODOO = "odoo"
    ENTERPRISE = "enterprise"
    DESIGN_THEMES = "design-themes"
    DOCUMENTATION = "documentation"
    UPGRADE = "upgrade"
    UPGRADE_UTIL = "upgrade-util"
    INTERNAL = "internal"


DEFAULT_BRANCHES = ["16.0", "17.0", "saas-17.2", "saas-17.4", "18.0", "master"]
DEFAULT_REPOS = [
    OdooRepo.ODOO,
    OdooRepo.ENTERPRISE,
    OdooRepo.DESIGN_THEMES,
    OdooRepo.UPGRADE,
    OdooRepo.UPGRADE_UTIL,
]
MULTI_BRANCH_REPOS = [OdooRepo.ODOO, OdooRepo.ENTERPRISE, OdooRepo.DESIGN_THEMES, OdooRepo.DOCUMENTATION]
SINGLE_BRANCH_REPOS = [OdooRepo.UPGRADE, OdooRepo.UPGRADE_UTIL, OdooRepo.INTERNAL]


# @app.command()
def multiverse(
    branches: Annotated[
        list[str],
        Option(
            "--branches",
            "-b",
            help="Specify the Odoo branches you want to add.",
        ),
    ] = DEFAULT_BRANCHES,
    repositories: Annotated[
        list[OdooRepo],
        Option(
            "--repositories",
            "-r",
            help="Specify the Odoo repositories you want to sync.",
        ),
    ] = DEFAULT_REPOS,
    multiverse_dir: Annotated[
        Path,
        Option(
            "--multiverse-dir", "-d", help="Specify the directory in which you want to install the multiverse setup."
        ),
    ] = Path(os.getcwd()),
):
    print_command_title(":earth_africa: Odoo Multiverse")

    # Ensure the multiverse directory exists.
    if multiverse_dir.is_file():
        print_error(f"The provided multiverse path [b]{multiverse_dir}[/b] is not a directory. Aborting ...\n")
        return
    multiverse_dir.mkdir(parents=True, exist_ok=True)

    # Ensure the worktree source directory exists.
    worktree_src_dir = multiverse_dir / ".worktree-source"
    if worktree_src_dir.is_file():
        print_error(f"The worktree source directory [b]{worktree_src_dir}[/b] is not a directory. Aborting ...\n")
        return
    worktree_src_dir.mkdir(parents=True, exist_ok=True)

    # Checkout all bare repositories of the multibranch ones.
    print_header(":jar: Configure Bare Multi-Branch Repositories")

    for repo in MULTI_BRANCH_REPOS:
        print(f"Setting up bare repository for [b]{repo.value}[/b] ...")

        # Ensure the repo source directory exists.
        repo_src_dir = worktree_src_dir / repo.value
        if repo_src_dir.is_file():
            print_error(f"The [b]{repo.value}[/b] source directory [u]{repo_src_dir}[/u] is not a directory. Aborting ...\n")
            return
        repo_src_dir.mkdir(parents=True, exist_ok=True)

        repo_url = f"git@github.com:odoo/{repo.value}.git"
        repo_dev_url = f"git@github.com:odoo-dev/{repo.value}.git"

        # Check whether the bare directory already exists.
        bare_dir = repo_src_dir / ".bare"
        with TransientProgress() as progress:
            try:
                if bare_dir.exists():
                    if not bare_dir.is_dir():
                        print_error(f"The [b]{repo.value}[/b] bare directory [u]{bare_dir}[/u] is not a directory. Aborting ...\n")
                        return
                    else:
                        print(f"Bare repository for [b]{repo.value}[/b] already exists :white_check_mark:")
                else:
                    progress_task = progress.add_task("Cloning bare repository ...", total=101)
                    with Popen(
                        cmd := ["git", "clone", "--progress", "--bare", str(repo_url), str(bare_dir)],
                        stderr=PIPE,
                        stdout=PIPE,
                        text=True,
                    ) as p:
                        while p.poll() is None:
                            log_line = p.stderr.readline()
                            if match := re.search(r"Receiving objects: (\d+)%\]", log_line):
                                completed = int(match.group(1))
                                progress.update(
                                    progress_task,
                                    completed=completed,
                                )
                    progress.update(progress_task, total=1, completed=1)
                    progress_task = progress.add_task("Adjusting origin fetch locations ...", total=1)
                    subprocess.run(
                        cmd := ["git", "-C", str(bare_dir), "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"],
                        capture_output=True,
                        check=True,
                        text=True,
                    )
                    progress.update(progress_task, advance=1)
                    if repo != OdooRepo.DOCUMENTATION:
                        progress_task = progress.add_task("Adding [b]odoo-dev[/b] remote as [b]dev[/b] ...", total=2)
                        subprocess.run(
                            cmd := ["git", "-C", str(bare_dir), "remote", "add", "dev", str(repo_dev_url)],
                            capture_output=True,
                            check=True,
                            text=True,
                        )
                        progress.update(progress_task, advance=1)
                        subprocess.run(
                            cmd := ["git", "-C", str(bare_dir), "remote", "set-url", "--push", "origin", "do_not_push_on_this_repo"],
                            capture_output=True,
                            check=True,
                            text=True,
                        )
                        progress.update(progress_task, advance=1)
                    progress_task = progress.add_task("Setting .git file contents ...", total=1)
                    with open(repo_src_dir / ".git", "w", encoding="utf-8") as git_file:
                        git_file.write("gitdir: ./.bare")
                    progress.update(progress_task, advance=1)
                    progress_task = progress.add_task("Fetching all branches ...", total=1)
                    subprocess.run(cmd := ["git", "-C", str(repo_src_dir), "fetch"], capture_output=True, check=True, text=True)
                    progress.update(progress_task, advance=1)

                progress_task = progress.add_task("Pruning non-existing worktrees ...", total=1)
                subprocess.run(cmd := ["git", "-C", str(repo_src_dir), "worktree", "prune"], capture_output=True, check=True, text=True)
                progress.update(progress_task, advance=1)
            except CalledProcessError as e:
                print_error(
                    f"Setting up the bare repository for [b]{repo.value}[/b] failed. The command that failed was:\n\n[b]{' '.join(cmd)}[/b]",
                    e.stderr.strip(),
                )
        print(f"Set up bare repository for [b]{repo.value}[/b] :white_check_mark:")

    # Checkout all unibranch repositories.
    print(
        Panel.fit(":jar: [bold]Configure Single-Branch Repositories[/bold]"),
        "",
    )
    for repo in SINGLE_BRANCH_REPOS:
        print(f"Setting up repository for [bold]{repo.value}[/bold] ...")
        # Check if the repo source directory already exists.
        repo_src_dir = worktree_src_dir / repo.value
        if repo_src_dir.exists():
            if not repo_src_dir.is_dir():
                print(
                    f":exclamation_mark: [red]The [b]{repo.value}[/b] directory [u]{repo_src_dir}[/u] is not a directory. Aborting ...\n"
                )
                return
            else:
                print(f"Repository for [b]{repo.value}[/b] already exists :white_check_mark:")
        else:
            repo_url = f"git@github.com:odoo/{repo.value}.git"
            with Progress(*PROGRESS_COLUMNS, console=console, transient=True) as progress:
                try:
                    progress_task = progress.add_task(description="Cloning repository ...", total=101)
                    with Popen(
                        cmd := ["git", "clone", "--progress", str(repo_url), str(repo_src_dir)],
                        stderr=PIPE,
                        stdout=PIPE,
                        text=True,
                    ) as p:
                        while p.poll() is None:
                            log_line = p.stderr.readline()
                            if match := re.search(r"Receiving objects: (\d+)%\]", log_line):
                                completed = int(match.group(1))
                                progress.update(
                                    progress_task,
                                    completed=completed,
                                )
                    progress.update(progress_task, total=1, completed=1)
                except CalledProcessError as error:
                    print(
                        f":exclamation_mark: [red]Setting up the repository [b]{repo.value}[/b] failed. The command that failed was:\n",
                        "\t[bold red]" + " ".join(cmd) + "\n",
                    )
                    print(
                        Panel(
                            error.stderr.strip(),
                            title="Error Log",
                            title_align="left",
                            style="red",
                            border_style="bold red",
                        ),
                    )
        print(f"Set up repository for [bold]{repo.value}[/bold] :white_check_mark:")

    # Add a git worktree for every branch.
    print(
        Panel.fit(":deciduous_tree: [bold]Configure Worktrees[/bold]"),
        "",
    )
    for branch in branches:
        if not branch:
            continue
        print(
            Panel.fit(f":wood: Configure Worktrees for [bold]{branch}[/bold]"),
            "",
        )
        # Ensure the branch directory exists.
        branch_dir = multiverse_dir / branch
        if branch_dir.exists() and not branch_dir.is_dir():
            print(
                f":exclamation_mark: [red]The [b]{branch}[/b] directory [u]{branch_dir}[/u] is not a directory. Aborting ...\n"
            )
            return
        branch_dir.mkdir(parents=True, exist_ok=True)

        for repo in MULTI_BRANCH_REPOS:
            print(f"Adding worktree [b]{branch}[/b] for repository [b]{repo.value}[/b] ...")
            bare_repo_dir = worktree_src_dir / repo.value
            with Progress(*PROGRESS_COLUMNS, console=console, transient=True) as progress:
                try:
                    progress_task = progress.add_task(description="Adding worktree ...", total=None)
                    subprocess.run(
                        cmd := ["git", "-C", str(bare_repo_dir), "worktree", "add", ""],
                        capture_output=True,
                        check=True,
                        text=True,
                    )
                    progress.update(progress_task, advance=1)
                except CalledProcessError as error:
                    print(
                        ":exclamation_mark: [red]Adding a worktree failed. The command that failed was:\n",
                        "\t[bold red]" + " ".join(cmd) + "\n",
                    )
                    print(
                        Panel(
                            error.stderr.strip(),
                            title="Error Log",
                            title_align="left",
                            style="red",
                            border_style="bold red",
                        ),
                    )

            # TODO: Check that git commands are executed with the right folder
