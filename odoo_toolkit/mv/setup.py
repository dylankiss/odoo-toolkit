import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from itertools import chain
from multiprocessing import Manager
from pathlib import Path
from subprocess import CalledProcessError
from typing import Annotated, TypeAlias, cast

from git import BadName, BadObject, GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo
from typer import Exit, Option, Typer

from odoo_toolkit.common import (
    ProgressUpdate,
    Status,
    TransientProgress,
    print,
    print_command_title,
    print_error,
    print_header,
    update_remote_progress,
)

from .common import MULTI_BRANCH_REPOS, ODOO_DEV_REPOS, SINGLE_BRANCH_REPOS, OdooRepo

app = Typer()

CWD = Path.cwd()
DEFAULT_BRANCHES = ["17.0", "18.0", "saas-18.2", "saas-18.3", "saas-18.4", "19.0", "saas-19.1", "saas-19.2", "saas-19.3", "master"]
DEFAULT_REPOS = [
    OdooRepo.ODOO,
    OdooRepo.ENTERPRISE,
    OdooRepo.DESIGN_THEMES,
    OdooRepo.UPGRADE,
    OdooRepo.UPGRADE_UTIL,
]
JS_TOOLING_MIN_VERSION = 14.5
JS_TOOLING_NEW_VERSION = 16.1
MULTIVERSE_CONFIG_DIR = Path(__file__).parent.parent / "multiverse_config"
JSONValue: TypeAlias = dict[str, "JSONValue"] | list["JSONValue"] | str | int | float | bool | None


@app.command()
def setup(
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
            "--multiverse-dir",
            "-d",
            help="Specify the directory in which you want to install the multiverse setup.",
        ),
    ] = CWD,
    reset_config: Annotated[
        bool,
        Option(
            "--reset-config",
            help="Reset every specified worktree's Ruff config, Python virtual environment and dependencies, "
            "and optional Visual Studio Code config.",
        ),
    ] = False,
    vscode: Annotated[
        bool,
        Option("--vscode", help="Copy settings and debug configurations for Visual Studio Code."),
    ] = False,
) -> None:
    """Set up an :ringed_planet: Odoo Multiverse environment, having different branches checked out at the same time.

    This way you can easily work on tasks in different versions without having to switch branches, or easily compare
    behaviors in different versions.\n
    \n
    The setup makes use of the [`git worktree`](https://git-scm.com/docs/git-worktree) feature to prevent having
    multiple full clones of the repositories for each version. The `git` history is only checked out once, and the only
    extra data you have per branch are the actual source files.\n
    \n
    The easiest way to set this up is by creating a directory for your multiverse setup and then run this command from
    that directory.\n
    \n
    > Make sure you have set up your **GitHub SSH key** beforehand in order to clone the repositories.\n
    \n
    You can run the command as many times as you want. It will skip already existing branches and repositories and only
    renew their configuration (when passed the `--reset-config` option).\n
    \n
    > If you're using **Visual Studio Code**, you can use the `--vscode` option to have the script create multi-root
    workspaces, and copy some default configuration to the multiverse folder. It contains recommended plugins, plugin
    configurations and debug configurations (that also work with the Docker container started via `otk dev start`).
    """
    print_command_title(":ringed_planet: Odoo Multiverse Setup")

    # Filter out empty branch names
    branches = list(filter(bool, branches))

    try:
        # Ensure the multiverse directory exists.
        multiverse_dir.mkdir(parents=True, exist_ok=True)
        print(f"Multiverse Directory: [b]{multiverse_dir}[/b]\n")

        # Ensure the worktree source directory exists.
        worktree_src_dir = multiverse_dir / ".worktree-source"
        worktree_src_dir.mkdir(parents=True, exist_ok=True)

        # Determine which repositories to configure.
        multi_branch_repos = [repo for repo in MULTI_BRANCH_REPOS if repo in repositories]
        single_branch_repos = [repo for repo in SINGLE_BRANCH_REPOS if repo in repositories]

        # Clone all source repositories (as bare ones for the multi-branch repos).
        with ProcessPoolExecutor() as executor, TransientProgress() as progress, Manager() as manager:
            # Set up progress trackers for the clone operations.
            progress_updates = cast("dict[OdooRepo, ProgressUpdate]", manager.dict({
                repo: ProgressUpdate(
                    task_id=progress.add_task(f"Cloning [b]{repo.value}[/b]", total=None),
                    description=f"Cloning [b]{repo.value}[/b]",
                    completed=0,
                    total=None,
                )
                for repo in chain(multi_branch_repos, single_branch_repos)
            }))

            print_header(":honey_pot: Clone Repositories")

            # Run the clone operations in multiple processes simultaneously to speed things up.
            futures = {
                executor.submit(
                    _clone_bare_multi_branch_repo if repo in multi_branch_repos else _clone_single_branch_repo,
                    repo=repo,
                    repo_src_dir=(worktree_src_dir if repo in multi_branch_repos else multiverse_dir) / repo.value,
                    progress_updates=progress_updates,
                ): repo for repo in chain(multi_branch_repos, single_branch_repos)
            }

            # Run the progress updater until everything is finished.
            update_remote_progress(progress=progress, progress_updates=progress_updates, futures=futures)

            # Check for any exceptions.
            for future in as_completed(futures):
                future.result()
            print()

        # Create the folders for the worktrees.
        for branch in branches:
            (multiverse_dir / branch).mkdir(parents=True, exist_ok=True)

        _setup_multiverse_root_config(
            multiverse_dir=multiverse_dir,
            repositories=repositories,
            reset_config=reset_config,
        )

        # Add the worktrees for each multi-branch repo in each branch.
        for branch in branches:
            with ProcessPoolExecutor() as executor, TransientProgress() as progress, Manager() as manager:
                # Set up progress trackers for the worktree operations.
                progress_updates = cast("dict[OdooRepo, ProgressUpdate]", manager.dict({
                    repo: ProgressUpdate(
                        task_id=progress.add_task(f"Adding [b]{repo.value}[/b] worktree [b]{branch}[/b]", total=None),
                        description=f"Adding [b]{repo.value}[/b] worktree [b]{branch}[/b]",
                        completed=0,
                        total=None,
                    )
                    for repo in multi_branch_repos
                }))

                print_header(f":deciduous_tree: Setup Branch {branch}")

                # Run the worktree operations in multiple processes simultaneously to speed things up.
                futures = {
                    executor.submit(
                        _add_worktree_for_branch,
                        repo=repo,
                        branch=branch,
                        repo_src_dir=worktree_src_dir / repo.value,
                        repo_worktree_dir=multiverse_dir / branch / repo.value,
                        progress_updates=progress_updates,
                    ): repo for repo in multi_branch_repos
                }

                # Run the progress updater until everything is finished.
                update_remote_progress(progress=progress, progress_updates=progress_updates, futures=futures)

                # Check for any exceptions.
                for future in as_completed(futures):
                    future.result()
                print()

        # Create a VS Code multi-root workspace for each branch, including selected single-branch repositories.
        if vscode:
            for branch in branches:
                _create_branch_workspace(
                    multiverse_dir=multiverse_dir,
                    branch=branch,
                    single_branch_repos=single_branch_repos,
                    reset_config=reset_config,
                )

        # Configure tools and dependencies for each branch directory.
        with ThreadPoolExecutor() as executor, TransientProgress() as progress, Manager() as manager:
            # Set up progress trackers for the configuration operations.
            progress_updates = cast("dict[str, ProgressUpdate]", manager.dict({
                branch: ProgressUpdate(
                    task_id=progress.add_task(f"Configuring tools and dependencies for [b]{branch}[/b]", total=None),
                    description=f"Configuring tools and dependencies for [b]{branch}[/b]",
                    completed=0,
                    total=None,
                ) for branch in branches
            }))

            print_header(":gear: Configure Tools and Dependencies for Branches")

            # Run the configurations in multiple threads simultaneously to speed things up.
            futures = {
                executor.submit(
                    _setup_tools_and_deps_in_branch_dir,
                    branch_dir=multiverse_dir / branch,
                    multiverse_dir=multiverse_dir,
                    reset_config=reset_config,
                    progress_updates=progress_updates,
                ): branch for branch in branches
            }

            # Run the progress updater until everything is finished.
            update_remote_progress(progress=progress, progress_updates=progress_updates, futures=futures)

            # Check for any exceptions.
            for future in as_completed(futures):
                future.result()
            print()

        print_header(":muscle: Great! You're now ready to work on multiple versions of Odoo")

    except OSError as e:
        print_error(
            f"Setting up the multiverse environment failed during file handling ([b]{e.errno}[/b]):\n"
            f"\t{e.filename}\n"
            f"\t{e.filename2}\n",
            str(e),
        )
        raise Exit from e


def _clone_bare_multi_branch_repo(  # noqa: C901, PLR0915
    repo: OdooRepo,
    repo_src_dir: Path,
    progress_updates: dict[OdooRepo, ProgressUpdate],
) -> None:
    """Clone an Odoo repository as a bare repository to create worktrees from later.

    :param repo: The repository name.
    :param repo_src_dir: The source directory for the repository.
    :param progress_updates: The progress update information per repository.
    :raises Exit: In case the command needs to be stopped.
    """
    # Ensure the repo source directory exists.
    if repo_src_dir.is_file():
        ProgressUpdate.update_in_dict(
            progress_updates,
            repo,
            completed=1,
            total=1,
            status=Status.FAILURE,
            message=f"The [b]{repo.value}[/b] source path [u]{repo_src_dir}[/u] is not a directory. Aborting ...",
        )
        raise Exit
    repo_src_dir.mkdir(parents=True, exist_ok=True)
    bare_dir = repo_src_dir / ".bare"

    try:
        bare_repo = Repo(bare_dir)
        bare_repo.git.worktree("prune")
        ProgressUpdate.update_in_dict(
            progress_updates,
            repo,
            completed=1,
            total=1,
            status=Status.SUCCESS,
            message=f"Bare repository for [b]{repo.value}[/b] already exists.",
        )
    except InvalidGitRepositoryError as e:
        ProgressUpdate.update_in_dict(
            progress_updates,
            repo,
            completed=1,
            total=1,
            status=Status.FAILURE,
            message=f"The [b]{repo.value}[/b] path [u]{bare_dir}[/u] is not a Git repository. Aborting ...",
        )
        raise Exit from e
    except NoSuchPathError:
        try:
            bare_repo = Repo.clone_from(
                url=f"git@github.com:odoo/{repo.value}.git",
                to_path=bare_dir,
                progress=lambda _op_code, cur_count, max_count, _message: ProgressUpdate.update_in_dict(
                    progress_updates,
                    repo,
                    description=f"Cloning bare repository [b]{repo.value}[/b]",
                    completed=float(cur_count),
                    total=float(max_count) if max_count else None,
                ),
                bare=True,
            )
        except GitCommandError as e:
            ProgressUpdate.update_in_dict(
                progress_updates,
                repo,
                completed=1,
                total=1,
                status=Status.FAILURE,
                message=f"Cloning the bare repository for [b]{repo.value}[/b] failed with [b]{e.status}[/b].\n"
                    f"The command that failed was:\n[b]{' '.join(e.command)}[/b]\n",
                stacktrace=e.stderr.strip(),
            )
            raise Exit from e
        except OSError as e:
            ProgressUpdate.update_in_dict(
                progress_updates,
                repo,
                completed=1,
                total=1,
                status=Status.FAILURE,
                message=f"Cloning the bare repository for [b]{repo.value}[/b] failed during file handling.",
                stacktrace=str(e),
            )
            raise Exit from e
    else:
        return

    # Explicitly set the remote origin fetch so we can fetch remote branches.
    ProgressUpdate.update_in_dict(
        progress_updates,
        repo,
        description=f"Setting up origin fetch configuration for [b]{repo.value}[/b]",
        completed=0,
        total=1,
    )
    bare_repo.config_writer().set_value('remote "origin"', "fetch", "+refs/heads/*:refs/remotes/origin/*").release()
    ProgressUpdate.update_in_dict(progress_updates, repo, advance=1)

    if repo in ODOO_DEV_REPOS:
        # Add the "odoo-dev" repository equivalent as a remote named "dev".
        ProgressUpdate.update_in_dict(
            progress_updates,
            repo,
            description=f"Adding [b]odoo-dev[/b] remote as [b]dev[/b] to [b]{repo.value}[/b]",
            completed=0,
            total=2,
        )
        if not any(remote.name == "dev" for remote in bare_repo.remotes):
            bare_repo.create_remote("dev", f"git@github.com:odoo-dev/{repo.value}.git")
        ProgressUpdate.update_in_dict(progress_updates, repo, advance=1)

        # Make sure people can't push on the "origin" remote when there is a "dev" remote.
        bare_repo.remote("origin").set_url("NO_PUSH_TRY_DEV_REPO", push=True)
        ProgressUpdate.update_in_dict(progress_updates, repo, advance=1)

    # Create the ".git" file pointing to the ".bare" directory.
    ProgressUpdate.update_in_dict(
        progress_updates,
        repo,
        description=f"Finishing git config for [b]{repo.value}[/b]",
        completed=0,
        total=1,
    )
    with (repo_src_dir / ".git").open("x", encoding="utf-8") as git_file:
        git_file.write("gitdir: ./.bare")
    ProgressUpdate.update_in_dict(progress_updates, repo, advance=1)

    try:
        # Fetch all remote branches to create the worktrees off later.
        ProgressUpdate.update_in_dict(
            progress_updates,
            repo,
            description=f"Fetching all branches for [b]{repo.value}[/b]",
            completed=0,
            total=1,
        )
        bare_repo.remote("origin").fetch()
        ProgressUpdate.update_in_dict(progress_updates, repo, advance=1)

        # Prune worktrees that were manually deleted before, so git doesn't get confused.
        ProgressUpdate.update_in_dict(
            progress_updates,
            repo,
            description=f"Pruning non-existing worktrees for [b]{repo.value}[/b]",
            completed=0,
            total=1,
        )
        bare_repo.git.worktree("prune")
        ProgressUpdate.update_in_dict(progress_updates, repo, advance=1)

    except GitCommandError as e:
        ProgressUpdate.update_in_dict(
            progress_updates,
            repo,
            completed=1,
            total=1,
            status=Status.FAILURE,
            message=f"Setting up the bare repository for [b]{repo.value}[/b] failed with [b]{e.status}[/b].\n"
                f"The command that failed was:\n[b]{' '.join(e.command)}[/b]\n",
            stacktrace=e.stderr.strip(),
        )
        raise Exit from e
    except OSError as e:
        ProgressUpdate.update_in_dict(
            progress_updates,
            repo,
            completed=1,
            total=1,
            status=Status.FAILURE,
            message=f"Setting up the bare repository for [b]{repo.value}[/b] failed during file handling.",
            stacktrace=str(e),
        )
        raise Exit from e

    ProgressUpdate.update_in_dict(
        progress_updates,
        repo,
        completed=1,
        total=1,
        status=Status.SUCCESS,
        message=f"Set up bare repository for [b]{repo.value}[/b]",
    )


def _clone_single_branch_repo(
    repo: OdooRepo,
    repo_src_dir: Path,
    progress_updates: dict[OdooRepo, ProgressUpdate],
) -> None:
    """Clone an Odoo repository to the given directory.

    :param repo: The repository name.
    :param repo_src_dir: The source directory for the repository.
    :param progress_updates: The progress update information per repository.
    :raises Exit: In case the command needs to be stopped.
    """
    # Check if the repo source directory already exists.
    try:
        Repo(repo_src_dir)
        ProgressUpdate.update_in_dict(
            progress_updates,
            repo,
            completed=1,
            total=1,
            status=Status.SUCCESS,
            message=f"Repository for [b]{repo.value}[/b] already exists.",
        )
    except InvalidGitRepositoryError as e:
        ProgressUpdate.update_in_dict(
            progress_updates,
            repo,
            completed=1,
            total=1,
            status=Status.FAILURE,
            message=f"The [b]{repo.value}[/b] path [u]{repo_src_dir}[/u] is not a Git repository. Aborting ...",
        )
        raise Exit from e
    except NoSuchPathError:
        pass
    else:
        return

    # Clone the repository.
    try:
        Repo.clone_from(
            url=f"git@github.com:odoo/{repo.value}.git",
            to_path=repo_src_dir,
            progress=lambda _op_code, cur_count, max_count, _message: ProgressUpdate.update_in_dict(
                progress_updates,
                repo,
                description=f"Cloning repository [b]{repo.value}[/b]",
                completed=float(cur_count),
                total=float(max_count) if max_count else None,
            ),
        )
    except GitCommandError as e:
        ProgressUpdate.update_in_dict(
            progress_updates,
            repo,
            completed=1,
            total=1,
            status=Status.FAILURE,
            message=f"Cloning the repository [b]{repo.value}[/b] failed with [b]{e.status}[/b].\n"
                f"The command that failed was:\n[b]{' '.join(e.command)}[/b]\n",
            stacktrace=e.stderr.strip(),
        )
        raise Exit from e
    except OSError as e:
        ProgressUpdate.update_in_dict(
            progress_updates,
            repo,
            completed=1,
            total=1,
            status=Status.FAILURE,
            message=f"Cloning the repository [b]{repo.value}[/b] failed during file handling.",
            stacktrace=str(e),
        )
        raise Exit from e

    ProgressUpdate.update_in_dict(
        progress_updates,
        repo,
        completed=1,
        total=1,
        status=Status.SUCCESS,
        message=f"Set up repository [b]{repo.value}[/b].",
    )


def _add_worktree_for_branch(
    repo: OdooRepo,
    branch: str,
    repo_src_dir: Path,
    repo_worktree_dir: Path,
    progress_updates: dict[OdooRepo, ProgressUpdate],
) -> None:
    """Add and configure a worktree for a specific branch in the given repository.

    :param repo: The repository for which we need to add a worktree.
    :param branch: The branch we need to add as a worktree.
    :param repo_src_dir: The directory containing the bare repository.
    :param repo_worktree_dir: The directory to contain the worktree.
    :param progress_updates: The progress update information per repository.
    """
    # Check if the worktree repo already exists.
    try:
        Repo(repo_worktree_dir)
        ProgressUpdate.update_in_dict(
            progress_updates,
            repo,
            completed=1,
            total=1,
            status=Status.SUCCESS,
            message=f"The worktree at [u]{repo_worktree_dir}[/u] already exists.",
        )
    except InvalidGitRepositoryError:
        ProgressUpdate.update_in_dict(
            progress_updates,
            repo,
            completed=1,
            total=1,
            status=Status.PARTIAL,
            message=f"The path [u]{repo_worktree_dir}[/u] is not a Git repository. Skipping ...",
        )
        return
    except NoSuchPathError:
        pass
    else:
        return

    # Check whether the branch we want to add exists on the remote.
    try:
        bare_repo = Repo(repo_src_dir)
        bare_repo.remote("origin").fetch(branch)
    except (BadName, BadObject, GitCommandError):
        ProgressUpdate.update_in_dict(
            progress_updates,
            repo,
            completed=1,
            total=1,
            status=Status.PARTIAL,
            message=f"The [b]{repo.value}[/b] branch [b]{branch}[/b] does not exist. Skipping ...",
        )
        return

    try:
        # Checkout the worktree for the specified branch.
        ProgressUpdate.update_in_dict(progress_updates, repo, total=2)
        bare_repo.git.worktree("add", str(repo_worktree_dir), branch)
        ProgressUpdate.update_in_dict(progress_updates, repo, advance=1)

        # Make sure the worktree references the right upstream branch.
        worktree_repo = Repo(repo_worktree_dir)
        worktree_repo.git.branch("--set-upstream-to", f"origin/{branch}", branch)
        ProgressUpdate.update_in_dict(progress_updates, repo, advance=1)

    except GitCommandError as e:
        ProgressUpdate.update_in_dict(
            progress_updates,
            repo,
            completed=1,
            total=1,
            status=Status.FAILURE,
            message=f"Adding the worktree [b]{branch}[/b] for repository [b]{repo.value}[/b] failed: [b]{e.status}[/b].\n"
            f"The command that failed was:\n[b]{' '.join(e.command)}[/b]\n",
            stacktrace=e.stderr.strip(),
        )
        return
    except OSError as e:
        ProgressUpdate.update_in_dict(
            progress_updates,
            repo,
            completed=1,
            total=1,
            status=Status.FAILURE,
            message=f"Adding the worktree [b]{branch}[/b] for repository [b]{repo.value}[/b] failed during "
                "file handling.",
            stacktrace=str(e),
        )
        return

    ProgressUpdate.update_in_dict(
        progress_updates,
        repo,
        completed=1,
        total=1,
        status=Status.SUCCESS,
        message=f"Added [b]{repo.value}[/b] worktree [b]{branch}[/b]",
    )


def _create_branch_workspace(multiverse_dir: Path, branch: str, single_branch_repos: list[OdooRepo], reset_config: bool = False) -> None:
    """Create a VS Code multi-root workspace file for a branch.

    :param multiverse_dir: The root directory of the multiverse setup.
    :param branch: The branch name used for the worktree folder and workspace name.
    :param single_branch_repos: Selected single-branch repositories to include as additional roots.
    :param reset_config: Whether to overwrite existing workspace file.
    """
    workspace_file = multiverse_dir / f"{branch}.code-workspace"
    if workspace_file.exists() and not reset_config:
        return

    workspace_content: dict[str, JSONValue] = {
        "folders": [
            {"path": branch},
            *[{"path": repo.value} for repo in single_branch_repos],
        ],
    }

    vscode_config_dir = MULTIVERSE_CONFIG_DIR / ".vscode"
    with (vscode_config_dir / "settings.json").open("r", encoding="utf-8") as settings_fp:
        workspace_content["settings"] = json.load(settings_fp)

    with (vscode_config_dir / "launch.json").open("r", encoding="utf-8") as launch_fp:
        workspace_content["launch"] = _replace_workspace_folder_reference(json.load(launch_fp), branch)

    with (vscode_config_dir / "extensions.json").open("r", encoding="utf-8") as extensions_fp:
        workspace_content["extensions"] = json.load(extensions_fp)

    with workspace_file.open("w", encoding="utf-8") as workspace_fp:
        json.dump(workspace_content, workspace_fp, indent=2)
        workspace_fp.write("\n")


def _replace_workspace_folder_reference(value: JSONValue, branch: str) -> JSONValue:
    """Replace ${workspaceFolder} references with the branch-scoped workspace folder."""
    source = "${workspaceFolder}"
    target = f"${{workspaceFolder:{branch}}}"

    if isinstance(value, str):
        return value.replace(source, target)
    if isinstance(value, list):
        return [_replace_workspace_folder_reference(item, branch) for item in value]
    if isinstance(value, dict):
        return {key: _replace_workspace_folder_reference(item, branch) for key, item in value.items()}
    return value


def _setup_multiverse_root_config(
    multiverse_dir: Path,
    repositories: list[OdooRepo],
    reset_config: bool,
) -> None:
    """Set up configuration files in the multiverse root directory.

    :param multiverse_dir: The root directory of the multiverse setup.
    :param repositories: Selected repositories for this multiverse setup.
    :param reset_config: Whether existing configuration should be overwritten.
    """
    if not (multiverse_dir / "ruff.toml").exists() or reset_config:
        shutil.copyfile(MULTIVERSE_CONFIG_DIR / "ruff.toml", multiverse_dir / "ruff.toml")

    if not (multiverse_dir / "requirements.txt").exists() or reset_config:
        shutil.copyfile(MULTIVERSE_CONFIG_DIR / "requirements.txt", multiverse_dir / "requirements.txt")

    _write_odools_toml(multiverse_dir / "odools.toml", repositories=repositories)


def _get_odools_addons_paths(repositories: set[OdooRepo]) -> list[str]:
    """Return odools addon path entries for the selected repositories."""
    addons_paths: list[str] = []

    if OdooRepo.DESIGN_THEMES in repositories:
        addons_paths.append("${workspaceFolder}/design-themes")
    if OdooRepo.ENTERPRISE in repositories:
        addons_paths.append("${workspaceFolder}/enterprise")
    if OdooRepo.IAP_APPS in repositories:
        addons_paths.extend([
            "${workspaceFolder:iap-apps}/iap_common",
            "${workspaceFolder:iap-apps}/iap_extract",
            "${workspaceFolder:iap-apps}/iap_odoo",
            "${workspaceFolder:iap-apps}/iap_services",
            "${workspaceFolder:iap-apps}/iap_website_scraper",
        ])
    if OdooRepo.INDUSTRY in repositories:
        addons_paths.append("${workspaceFolder}/industry")
    if OdooRepo.INTERNAL in repositories:
        addons_paths.extend([
            "${workspaceFolder:internal}/default",
            "${workspaceFolder:internal}/demo",
            "${workspaceFolder:internal}/i18n",
            "${workspaceFolder:internal}/private",
            "${workspaceFolder:internal}/runbot",
            "${workspaceFolder:internal}/test",
            "${workspaceFolder:internal}/tools",
            "${workspaceFolder:internal}/trial",
        ])
    if OdooRepo.ODOOFIN in repositories:
        addons_paths.append("${workspaceFolder:odoofin}")

    return addons_paths


def _write_odools_toml(odools_path: Path, repositories: list[OdooRepo] | None = None, python_path: str | None = None) -> None:
    """Write an odools.toml file for a workspace.

    If repositories are provided, writes full config with odoo_path, addons_paths, and python_path.
    If repositories are None, writes minimal config with only python_path.

    :param odools_path: Path to the odools.toml file to write.
    :param repositories: Selected repositories for this multiverse setup (None for minimal config).
    :param python_path: Optional python_path override.
    """
    lines = ["[[config]]"]

    if repositories is not None:
        selected_repositories = set(repositories)
        odools_python_path = python_path or "${workspaceFolder}/.venv/bin/python"

        lines.append('odoo_path = "${workspaceFolder}/odoo"')
        lines.append("addons_paths = [")

        lines.extend(
            f'    "{addon_path}",'
            for addon_path in _get_odools_addons_paths(selected_repositories)
        )

        lines.extend([
            "]",
            f'python_path = "{odools_python_path}"',
        ])
    elif python_path:
        lines.append(f'python_path = "{python_path}"')

    lines.append("")
    odools_path.write_text("\n".join(lines), encoding="utf-8")


def _setup_tools_and_deps_in_branch_dir(
    branch_dir: Path,
    multiverse_dir: Path,
    reset_config: bool,
    progress_updates: dict[str, ProgressUpdate],
) -> None:
    """Set up all tooling and dependencies in the given branch directory.

    :param branch_dir: The branch directory in which to set up the tooling and dependencies.
    :param multiverse_dir: The root directory of the multiverse setup.
    :param reset_config: Whether we want the reset existing configuration files.
    :param progress_updates: The progress update information per branch.
    """
    branch = branch_dir.name
    ProgressUpdate.update_in_dict(progress_updates, branch, total=3)

    # Set up Javascript tooling.
    if _get_version_number(branch) >= JS_TOOLING_MIN_VERSION:
        _disable_js_tooling(branch_dir)
        _enable_js_tooling(branch_dir, progress_updates=progress_updates, branch=branch)

    ProgressUpdate.update_in_dict(progress_updates, branch, advance=1)

    _configure_python_env_for_branch(
        branch_dir,
        multiverse_dir=multiverse_dir,
        reset_config=reset_config,
        progress_updates=progress_updates,
    )

    # Override python_path due to a bug with virtual environments and symlinks.
    python_path = (
        (branch_dir / ".venv" / "Scripts" / "python.exe")
        if os.name == "nt"
        else (branch_dir / ".venv" / "bin" / "python")
    )
    _write_odools_toml(
        branch_dir / "odools.toml",
        python_path=str(python_path.absolute()),
    )

    ProgressUpdate.update_in_dict(
        progress_updates,
        branch,
        completed=1,
        total=1,
        status=Status.SUCCESS,
        message=f"Configured tools and dependencies for [b]{branch}[/b].",
    )


def _pip_install_requirements(uv: str | None, python: Path, requirements: Path) -> None:
    """Run pip install for the given requirements file if it exists."""
    if not requirements.is_file():
        return
    cmd: list[str | Path]
    if uv:
        cmd = [uv, "pip", "install", "--python", python, "-q", "-r", requirements]
    else:
        cmd = [python, "-m", "pip", "install", "-q", "-r", requirements]
    subprocess.run(cmd, capture_output=True, check=True)


def _configure_python_env_for_branch(
    branch_dir: Path,
    multiverse_dir: Path,
    reset_config: bool,
    progress_updates: dict[str, ProgressUpdate],
) -> None:
    """Configure a virtual Python environment with all dependencies for a branch.

    :param branch_dir: The directory in which to create the virtual environment.
    :param reset_config: Whether we want to erase the existing virtual environment.
    """
    branch = branch_dir.name
    # Configure Python virtual environment.
    venv_path = branch_dir / ".venv"
    if venv_path.is_file():
        ProgressUpdate.update_in_dict(
            progress_updates,
            branch,
            completed=1,
            total=1,
            status=Status.PARTIAL,
            message=f"The path [u]{venv_path}[/u] is not a directory. Skipping ...",
        )
        return

    if venv_path.is_dir() and reset_config:
        shutil.rmtree(venv_path)

    # Find the system Python interpreter to create the virtual environment with.
    python = shutil.which("python3") or "python3"

    uv = shutil.which("uv")
    try:
        # Try creating the virtual environment.
        cmd: list[str] = [uv, "venv", str(venv_path)] if uv else [python, "-m", "venv", str(venv_path)]
        subprocess.run(cmd, capture_output=True, check=True)

        # Locate the Python executable in the virtual environment.
        python = venv_path / "bin" / "python"  # Linux and MacOS
        if not python.exists():
            python = venv_path / "Scripts" / "python.exe"  # Windows

        # Try upgrading pip.
        if uv:
            cmd = [uv, "pip", "install", "--python", str(python), "-q", "--upgrade", "pip"]
        else:
            cmd = [str(python), "-m", "pip", "install", "-q", "--upgrade", "pip"]
        subprocess.run(cmd, capture_output=True, check=True)

        for req_path in [
            branch_dir / "odoo" / "requirements.txt",
            branch_dir / "documentation" / "requirements.txt",
            branch_dir / "documentation" / "tests" / "requirements.txt",
            multiverse_dir / "requirements.txt",
        ]:
            _pip_install_requirements(uv, python, req_path)

    except CalledProcessError as e:
        ProgressUpdate.update_in_dict(
            progress_updates,
            branch,
            completed=1,
            total=1,
            status=Status.FAILURE,
            message=f"Installing Python dependencies for [b]{branch}[/b] failed.\nThe command that failed was:\n"
                f"[b]{' '.join(str(p) for p in e.cmd)}[/b]\n",
            stacktrace=e.stderr.strip(),
        )


def _get_version_number(branch_name: str) -> float:
    """Get the Odoo version number as a float based on the branch name.

    :param branch_name: The Odoo branch name to get the version number from.
    :return: The version number as a float.
    """
    if branch_name == "master":
        return 1000.0
    match = re.search(r"(\d+.\d)", branch_name)
    if match:
        return float(match.group(0))
    return 0.0


def _enable_js_tooling(root_dir: Path, progress_updates: dict[str, ProgressUpdate], branch: str) -> None:
    """Enable Javascript tooling in the Community and Enterprise repositories.

    :param root_dir: The parent directory of the `odoo` and `enterprise` repositories.
    """
    com_dir = root_dir / "odoo"
    ent_dir = root_dir / "enterprise"
    tooling_dir = com_dir / "addons" / "web" / "tooling"

    if not com_dir.is_dir():
        return

    # Set up tools in Community.
    shutil.copyfile(tooling_dir / "_eslintignore", com_dir / ".eslintignore")
    shutil.copyfile(tooling_dir / "_eslintrc.json", com_dir / ".eslintrc.json")
    if _get_version_number(root_dir.name) >= JS_TOOLING_NEW_VERSION:
        shutil.copyfile(tooling_dir / "_jsconfig.json", com_dir / "jsconfig.json")
    shutil.copyfile(tooling_dir / "_package.json", com_dir / "package.json")
    cmd = ["npm", "install"]
    try:
        subprocess.run(cmd, capture_output=True, check=True, cwd=com_dir, text=True)
    except CalledProcessError as e:
        ProgressUpdate.update_in_dict(
            progress_updates,
            branch,
            status=Status.FAILURE,
            message=f"Installing Javascript tooling dependencies failed in [b]{branch}[/b].\n"
                f"The command that failed was:\n\n[b]{' '.join(cmd)}[/b]",
            stacktrace=e.stderr.strip(),
        )
        return

    if not ent_dir.is_dir():
        return

    # Set up tools in Enterprise.
    shutil.copyfile(tooling_dir / "_eslintignore", ent_dir / ".eslintignore")
    shutil.copyfile(tooling_dir / "_eslintrc.json", ent_dir / ".eslintrc.json")
    shutil.copyfile(tooling_dir / "_package.json", ent_dir / "package.json")
    if _get_version_number(root_dir.name) >= JS_TOOLING_NEW_VERSION:
        shutil.copyfile(tooling_dir / "_jsconfig.json", ent_dir / "jsconfig.json")
        try:
            # Replace "addons" path with relative path from Enterprise in jsconfig.json.
            with (ent_dir / "jsconfig.json").open("r", encoding="utf-8") as jsconfig_file:
                jsconfig_content = jsconfig_file.read().replace(
                    "addons",
                    f"{os.path.relpath(com_dir, ent_dir)}/addons",
                )
            with (ent_dir / "jsconfig.json").open("w", encoding="utf-8") as jsconfig_file:
                jsconfig_file.write(jsconfig_content)
        except OSError as e:
            ProgressUpdate.update_in_dict(
                progress_updates,
                branch,
                status=Status.FAILURE,
                message=f"Modifying the jsconfig.json file in [b]{branch}[/b] to use relative paths failed.",
                stacktrace=str(e),
            )
            return
    # Copy over node_modules and package-lock.json to avoid "npm install" twice.
    shutil.copyfile(com_dir / "package-lock.json", ent_dir / "package-lock.json")
    shutil.copytree(com_dir / "node_modules", ent_dir / "node_modules", dirs_exist_ok=True)


def _disable_js_tooling(root_dir: Path) -> None:
    """Disable Javascript tooling in the Community and Enterprise repositories.

    :param root_dir: The parent directory of the "odoo" and "enterprise" repositories.
    """
    com_dir = root_dir / "odoo"
    ent_dir = root_dir / "enterprise"

    for odoo_dir in (com_dir, ent_dir):
        if odoo_dir.is_dir():
            (odoo_dir / ".eslintignore").unlink(missing_ok=True)
            (odoo_dir / ".eslintrc.json").unlink(missing_ok=True)
            (odoo_dir / "jsconfig.json").unlink(missing_ok=True)
            (odoo_dir / "package.json").unlink(missing_ok=True)
            (odoo_dir / "package-lock.json").unlink(missing_ok=True)
            shutil.rmtree(odoo_dir / "node_modules", ignore_errors=True)
            # Support old versions
            (odoo_dir / ".prettierignore").unlink(missing_ok=True)
            (odoo_dir / ".prettierrc.json").unlink(missing_ok=True)
