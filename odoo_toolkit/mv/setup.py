import os
import re
import shutil
import subprocess
from pathlib import Path
from subprocess import CalledProcessError
from typing import Annotated
from venv import EnvBuilder

from git import BadName, BadObject, GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo
from typer import Exit, Option, Typer

from odoo_toolkit.common import (
    TransientProgress,
    print,
    print_command_title,
    print_error,
    print_header,
    print_subheader,
    print_success,
    print_warning,
)

from .common import MULTI_BRANCH_REPOS, SINGLE_BRANCH_REPOS, OdooRepo

app = Typer()

DEFAULT_BRANCHES = ["16.0", "17.0", "saas-17.4", "18.0", "saas-18.1", "master"]
DEFAULT_REPOS = [
    OdooRepo.ODOO,
    OdooRepo.ENTERPRISE,
    OdooRepo.DESIGN_THEMES,
    OdooRepo.UPGRADE,
    OdooRepo.UPGRADE_UTIL,
]
MULTIVERSE_CONFIG_DIR = Path(__file__).parent / "multiverse_config"
CWD = Path.cwd()
JS_TOOLING_MIN_VERSION = 14.5
JS_TOOLING_NEW_VERSION = 16.1


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
    > If you're using **Visual Studio Code**, you can use the `--vscode` option to have the script copy some default
    configuration to each branch folder. It contains recommended plugins, plugin configurations and debug configurations
    (that also work with the Docker container started via `otk dev start`).
    """
    print_command_title(":ringed_planet: Odoo Multiverse Setup")

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

        # Clone all bare repositories of the multi-branch ones.
        if multi_branch_repos:
            print_header(":honey_pot: Clone Bare Multi-Branch Repositories")
            for repo in multi_branch_repos:
                _clone_bare_multi_branch_repo(repo=repo, repo_src_dir=worktree_src_dir / repo.value)

        # Clone all single-branch repositories.
        if single_branch_repos:
            print_header(":honey_pot: Clone Single-Branch Repositories")
            for repo in single_branch_repos:
                _clone_single_branch_repo(repo=repo, repo_src_dir=multiverse_dir / repo.value)

        # Add a git worktree for every branch.
        print_header(":deciduous_tree: Configure Worktrees")
        for branch in branches:
            if not branch:
                continue

            print_subheader(f":gear: Configure Worktrees for [b]{branch}[/b]")

            # Ensure the branch directory exists.
            branch_dir = multiverse_dir / branch
            branch_dir.mkdir(parents=True, exist_ok=True)

            # Add worktrees for the multi-branch repositories.
            for repo in multi_branch_repos:
                _configure_worktree_for_branch(
                    repo=repo,
                    branch=branch,
                    bare_repo_dir=worktree_src_dir / repo.value,
                    worktree_dir=branch_dir / repo.value,
                )

            # Link single-branch repositories to the branch directories.
            for repo in single_branch_repos:
                _link_repo_to_branch_dir(
                    repo=repo,
                    repo_src_dir=multiverse_dir / repo.value,
                    repo_branch_dir=branch_dir / repo.value,
                )

            # Configure tools and dependencies for the branch directory.
            print(f"Finishing configuration for branch [b]{branch}[/b] ...")
            _setup_config_in_branch_dir(branch_dir=branch_dir, reset_config=reset_config, vscode=vscode)
            _configure_python_env_for_branch(branch_dir=branch_dir, reset_config=reset_config)
            print_success(f"Finished configuration for branch [b]{branch}[/b]\n")

        print_header(":muscle: Great! You're now ready to work on multiple versions of Odoo")

    except OSError as e:
        print_error(
            f"Setting up the multiverse environment failed during file handling ([b]{e.errno}[/b]):\n"
            f"\t{e.filename}\n"
            f"\t{e.filename2}",
            e.strerror,
        )
        raise Exit from e


def _clone_bare_multi_branch_repo(repo: OdooRepo, repo_src_dir: Path) -> None:  # noqa: C901, PLR0915
    """Clone an Odoo repository as a bare repository to create worktrees from later.

    :param repo: The repository name.
    :type repo: :class:`OdooRepo`
    :param repo_src_dir: The source directory for the repository.
    :type repo_src_dir: :class:`pathlib.Path`
    :raises Exit: In case the command needs to be stopped.
    """
    print(f"Setting up bare repository for [b]{repo.value}[/b] ...")

    # Ensure the repo source directory exists.
    if repo_src_dir.is_file():
        print_error(f"The [b]{repo.value}[/b] source path [u]{repo_src_dir}[/u] is not a directory. Aborting ...\n")
        raise Exit
    repo_src_dir.mkdir(parents=True, exist_ok=True)
    bare_dir = repo_src_dir / ".bare"

    with TransientProgress() as progress:
        try:
            bare_repo = Repo(bare_dir)
            bare_repo.git.worktree("prune")
            print_success(f"Bare repository for [b]{repo.value}[/b] already exists.\n")
        except InvalidGitRepositoryError as e:
            print_error(
                f"The [b]{repo.value}[/b] path [u]{bare_dir}[/u] is not a Git repository. Aborting ...\n",
            )
            raise Exit from e
        except NoSuchPathError:
            progress_task = progress.add_task(f"Cloning bare repository [b]{repo.value}[/b] ...", total=None)
            try:
                bare_repo = Repo.clone_from(
                    url=f"git@github.com:odoo/{repo.value}.git",
                    to_path=bare_dir,
                    progress=lambda _op_code, cur_count, max_count, _message: progress.update(
                        progress_task, total=max_count, completed=cur_count,
                    ),
                    bare=True,
                )
            except GitCommandError as e:
                print_error(
                    f"Cloning the bare repository for [b]{repo.value}[/b] failed with [b]{e.status}[/b]. "
                    f"The command that failed was:\n\n[b]{e.command}[/b]",
                    e.stderr.strip(),
                )
                raise Exit from e
            except OSError as e:
                print_error(
                    f"Cloning the bare repository for [b]{repo.value}[/b] failed during file handling.",
                    e.strerror,
                )
                raise Exit from e
        else:
            return

        # Explicitly set the remote origin fetch so we can fetch remote branches.
        progress_task = progress.add_task(
            f"Setting up origin fetch configuration for [b]{repo.value}[/b] ...",
            total=None,
        )
        bare_repo.config_writer().set_value('remote "origin"', "fetch", "+refs/heads/*:refs/remotes/origin/*").release()
        progress.update(progress_task, total=1, completed=1)

        if repo not in (OdooRepo.DOCUMENTATION, OdooRepo.O_SPREADSHEET):
            # Add the "odoo-dev" repository equivalent as a remote named "dev".
            progress_task = progress.add_task("Adding [b]odoo-dev[/b] remote as [b]dev[/b] ...", total=2)
            if not any(remote.name == "dev" for remote in bare_repo.remotes):
                bare_repo.create_remote("dev", f"git@github.com:odoo-dev/{repo.value}.git")
            progress.update(progress_task, advance=1)

            # Make sure people can't push on the "origin" remote when there is a "dev" remote.
            bare_repo.remote("origin").set_url("NO_PUSH_TRY_DEV_REPO", push=True)
            progress.update(progress_task, advance=1)

        # Create the ".git" file pointing to the ".bare" directory.
        progress_task = progress.add_task("Finishing Git config ...", total=None)
        with (repo_src_dir / ".git").open("x", encoding="utf-8") as git_file:
            git_file.write("gitdir: ./.bare")
        progress.update(progress_task, total=1, completed=1)

    with TransientProgress() as progress:
        try:
            # Fetch all remote branches to create the worktrees off later.
            progress_task = progress.add_task("Fetching all branches ...", total=None)
            bare_repo.remote("origin").fetch()
            progress.update(progress_task, total=1, completed=1)

            # Prune worktrees that were manually deleted before, so Git doesn't get confused.
            progress_task = progress.add_task("Pruning non-existing worktrees ...", total=None)
            bare_repo.git.worktree("prune")
            progress.update(progress_task, total=1, completed=1)

        except GitCommandError as e:
            print_error(
                f"Setting up the bare repository for [b]{repo.value}[/b] failed with [b]{e.status}[/b]. "
                f"The command that failed was:\n\n[b]{e.command}[/b]",
                e.stderr.strip(),
            )
            raise Exit from e
        except OSError as e:
            print_error(
                f"Setting up the bare repository for [b]{repo.value}[/b] failed during file handling.",
                e.strerror,
            )
            raise Exit from e

    print_success(f"Set up bare repository for [b]{repo.value}[/b].\n")


def _clone_single_branch_repo(repo: OdooRepo, repo_src_dir: Path) -> None:
    """Clone an Odoo repository to the given directory.

    :param repo: The repository name.
    :type repo: :class:`OdooRepo`
    :param repo_src_dir: The source directory for the repository.
    :type repo_src_dir: :class:`pathlib.Path`
    :raises Exit: In case the command needs to be stopped.
    """
    print(f"Setting up repository for [bold]{repo.value}[/bold] ...")

    # Check if the repo source directory already exists.
    try:
        Repo(repo_src_dir)
        print_success(f"Repository for [b]{repo.value}[/b] already exists.\n")
    except InvalidGitRepositoryError as e:
        print_error(
            f"The [b]{repo.value}[/b] path [u]{repo_src_dir}[/u] is not a Git repository. Aborting ...\n",
        )
        raise Exit from e
    except NoSuchPathError:
        pass
    else:
        return

    with TransientProgress() as progress:
        # Clone the repository.
        progress_task = progress.add_task(f"Cloning repository [b]{repo.value}[/b] ...", total=None)
        try:
            Repo.clone_from(
                url=f"git@github.com:odoo/{repo.value}.git",
                to_path=repo_src_dir,
                progress=lambda _op_code, cur_count, max_count, _message: progress.update(
                    progress_task, total=max_count, completed=cur_count,
                ),
            )
        except GitCommandError as e:
            print_error(
                f"Cloning the repository [b]{repo.value}[/b] failed with [b]{e.status}[/b]. "
                f"The command that failed was:\n\n[b]{e.command}[/b]",
                e.stderr.strip(),
            )
            raise Exit from e
        except OSError as e:
            print_error(
                f"Cloning the repository [b]{repo.value}[/b] failed during file handling.",
                e.strerror,
            )
            raise Exit from e

    print_success(f"Set up repository [b]{repo.value}[/b]\n")


def _configure_worktree_for_branch(repo: OdooRepo, branch: str, bare_repo_dir: Path, worktree_dir: Path) -> None:
    """Add and configure a worktree for a specific branch in the given repository.

    :param repo: The repository for which we need to add a worktree.
    :type repo: :class:`OdooRepo`
    :param branch: The branch we need to add as a worktree.
    :type branch: str
    :param bare_repo_dir: The directory containing the bare repository.
    :type bare_repo_dir: :class:`pathlib.Path`
    :param worktree_dir: The directory to contain the worktree.
    :type worktree_dir: :class:`pathlib.Path`
    """
    print(f"Adding worktree [b]{branch}[/b] for repository [b]{repo.value}[/b] ...")

    # Check if the worktree repo already exists.
    try:
        Repo(worktree_dir)
        print_success(f"The [b]{repo.value}[/b] worktree [u]{worktree_dir}[/u] already exists.\n")
    except InvalidGitRepositoryError:
        print_warning(
            f"The [b]{repo.value}[/b] worktree [u]{worktree_dir}[/u] is not a Git repository. Skipping ...\n",
        )
        return
    except NoSuchPathError:
        pass
    else:
        return

    # Check whether the branch we want to add exists on the remote.
    try:
        bare_repo = Repo(bare_repo_dir)
        bare_repo.rev_parse(f"origin/{branch}")
    except (BadName, BadObject):
        print_warning(f"The [b]{repo.value}[/b] branch [b]{branch}[/b] does not exist. Skipping ...\n")
        return

    with TransientProgress() as progress:
        try:
            # Checkout the worktree for the specified branch.
            progress_task = progress.add_task(f"Adding worktree [b]{branch}[/b] ...", total=4)
            bare_repo.git.worktree("add", str(worktree_dir), branch)
            progress.update(progress_task, advance=1)

            # Make sure the worktree references the right upstream branch.
            worktree_repo = Repo(worktree_dir)
            worktree_repo.git.branch("--set-upstream-to", f"origin/{branch}", branch)
            progress.update(progress_task, advance=1)

            # Make link in .git to git worktree relative.
            with (worktree_dir / ".git").open("r", encoding="utf-8") as git_file:
                absolute_gitdir = Path(git_file.read()[len("gitdir: ") :].strip())
            relative_gitdir = os.path.relpath(absolute_gitdir, worktree_dir)
            with (worktree_dir / ".git").open("w", encoding="utf-8") as git_file:
                git_file.write(f"gitdir: {relative_gitdir}\n")
            progress.update(progress_task, advance=1)

            # Make link in git worktree gitdir to checked out repo relative.
            with (absolute_gitdir / "gitdir").open("r", encoding="utf-8") as gitdir_file:
                relative_git = os.path.relpath(Path(gitdir_file.read().strip()), absolute_gitdir)
            with (absolute_gitdir / "gitdir").open("w", encoding="utf-8") as gitdir_file:
                gitdir_file.write(f"{relative_git}\n")
            progress.update(progress_task, advance=1)

        except GitCommandError as e:
            print_error(
                f"Adding the worktree [b]{branch}[/b] for repository [b]{repo.value}[/b] failed with [b]{e.status}[/b]. "
                f"The command that failed was:\n\n[b]{e.command}[/b]",
                e.stderr.strip(),
            )
            return
        except OSError as e:
            print_error(
                f"Adding the worktree [b]{branch}[/b] for repository [b]{repo.value}[/b] failed "
                "during file handling.",
                e.strerror,
            )
            return

    print_success(f"Added worktree [b]{branch}[/b] for repository [b]{repo.value}[/b].\n")


def _link_repo_to_branch_dir(repo: OdooRepo, repo_src_dir: Path, repo_branch_dir: Path) -> None:
    """Create a symlink from a single-branch repository to a branch directory.

    :param repo: The repository to symlink.
    :type repo: :class:`OdooRepo`
    :param repo_src_dir: The repository's source directory.
    :type repo_src_dir: :class:`pathlib.Path`
    :param repo_branch_dir: The branch directory in which the symlink needs to be created.
    :type repo_branch_dir: :class:`pathlib.Path`
    """
    print(f"Linking repository [b]{repo.value}[/b] to worktree ...")

    try:
        Repo(repo_branch_dir)
        print_success(f"The [b]{repo.value}[/b] repository link [u]{repo_branch_dir}[/u] already exists.\n")
    except InvalidGitRepositoryError as e:
        print_warning(
            f"The [b]{repo.value}[/b] path [u]{repo_branch_dir}[/u] is not a Git repository. Skipping ...\n",
        )
        return
    except NoSuchPathError:
        repo_branch_dir.symlink_to(repo_src_dir, target_is_directory=True)
        print_success(f"Added symlink for repository [b]{repo.value}[/b] to worktree.\n")


def _setup_config_in_branch_dir(branch_dir: Path, reset_config: bool, vscode: bool) -> None:
    """Set up all configuration files in the given branch directory.

    :param branch_dir: The branch directory in which to set up the configurations.
    :type branch_dir: :class:`pathlib.Path`
    :param reset_config: Whether we want the reset existing configuration files.
    :type reset_config: bool
    :param vscode: Whether we want configuration files for Visual Studio Code.
    :type vscode: bool
    """
    branch = branch_dir.name
    with TransientProgress() as progress:
        # Copy Ruff configuration.
        if not (branch_dir / "ruff.toml").exists() or reset_config:
            progress_task = progress.add_task(
                f"Setting up Ruff configuration for worktree [b]{branch}[/b] ...",
                total=None,
            )
            try:
                shutil.copyfile(MULTIVERSE_CONFIG_DIR / "ruff.toml", branch_dir / "ruff.toml")
            except OSError as e:
                print_error(f"Copying [b]ruff.toml[/b] to branch [b]{branch}[/b] failed.", e.strerror)
            progress.update(progress_task, total=1, completed=1)

        # Copy Visual Studio Code configuration.
        if vscode and (not (branch_dir / ".vscode").exists() or reset_config):
            progress_task = progress.add_task(
                f"Copying Visual Studio Code configuration to branch [b]{branch}[/b] ...",
                total=None,
            )
            try:
                shutil.copytree(MULTIVERSE_CONFIG_DIR / ".vscode", branch_dir / ".vscode", dirs_exist_ok=True)
            except shutil.Error as e:
                print_error(
                    f"Copying the [b].vscode[/b] settings directory to branch [b]{branch}[/b] failed.",
                    e.strerror,
                )
            progress.update(progress_task, total=1, completed=1)

        # Copy optional Python requirements.
        if not (branch_dir / "requirements.txt").exists() or reset_config:
            progress_task = progress.add_task(
                f"Copying optional Python requirements to worktree [b]{branch}[/b] ...",
                total=None,
            )
            try:
                shutil.copyfile(MULTIVERSE_CONFIG_DIR / "requirements.txt", branch_dir / "requirements.txt")
            except OSError as e:
                print_error(f"Copying [b]requirements.txt[/b] to branch [b]{branch}[/b] failed.", e.strerror)
            progress.update(progress_task, total=1, completed=1)

        # Set up Javascript tooling.
        if _get_version_number(branch) >= JS_TOOLING_MIN_VERSION:
            progress_task = progress.add_task(
                f"Setting up Javascript tooling for branch [b]{branch}[/b] ...",
                total=None,
            )
            _disable_js_tooling(branch_dir)
            _enable_js_tooling(branch_dir)
            progress.update(progress_task, total=1, completed=1)


def _configure_python_env_for_branch(branch_dir: Path, reset_config: bool) -> None:
    """Configure a virtual Python environment with all dependencies for a branch.

    :param branch_dir: The directory in which to create the virtual environment.
    :type branch_dir: :class:`pathlib.Path`
    :param reset_config: Whether we want to erase the existing virtual environment.
    :type reset_config: bool
    """
    branch = branch_dir.name
    with TransientProgress() as progress:
        # Configure Python virtual environment.
        venv_path = branch_dir / ".venv"
        if venv_path.is_file():
            print_warning(f"The path [u]{venv_path}[/u] is not a directory. Skipping ...\n")
            return

        if venv_path.is_dir() and reset_config:
            progress_task = progress.add_task(
                f"Removing existing virtual environment for branch [b]{branch}[/b] ...",
                total=None,
            )
            shutil.rmtree(venv_path)
            progress.update(progress_task, total=1, completed=1)

        progress_task = progress.add_task(
            f"Configuring Python virtual environment for branch [b]{branch}[/b] ...",
            total=None,
        )
        EnvBuilder(with_pip=True, symlinks=True, upgrade=not reset_config, upgrade_deps=True).create(venv_path)
        progress.update(progress_task, total=1, completed=1)

        # Locate the Python executable in the virtual environment.
        python = venv_path / "bin" / "python"  # Linux and MacOS
        if not python.exists():
            python = venv_path / "Scripts" / "python.exe"  # Windows

        # Install Python dependencies using pip.
        try:
            # Try upgrading pip.
            cmd = [str(python), "-m", "pip", "install", "-q", "--upgrade", "pip"]
            subprocess.run(cmd, capture_output=True, check=True)

            # Install "odoo" requirements.
            requirements = branch_dir / "odoo" / "requirements.txt"
            if requirements.is_file():
                progress_task = progress.add_task(
                    f"Installing [b]odoo[/b] dependencies for branch [b]{branch}[/b] ...",
                    total=None,
                )
                cmd = [str(python), "-m", "pip", "install", "-q", "-r", str(requirements)]
                subprocess.run(cmd, capture_output=True, check=True)
                progress.update(progress_task, total=1, completed=1)

            # Install "documentation" requirements.
            requirements = branch_dir / "documentation" / "requirements.txt"
            if requirements.is_file():
                progress_task = progress.add_task(
                    f"Installing [b]documentation[/b] dependencies for branch [b]{branch}[/b] ...",
                    total=None,
                )
                cmd = [str(python), "-m", "pip", "install", "-q", "-r", str(requirements)]
                subprocess.run(cmd, capture_output=True, check=True)
                progress.update(progress_task, total=1, completed=1)

            # Install optional requirements.
            requirements = branch_dir / "requirements.txt"
            if requirements.is_file():
                progress_task = progress.add_task(
                    f"Installing optional dependencies for branch [b]{branch}[/b] ...",
                    total=None,
                )
                cmd = [str(python), "-m", "pip", "install", "-q", "-r", str(requirements)]
                subprocess.run(cmd, capture_output=True, check=True)
                progress.update(progress_task, total=1, completed=1)

        except CalledProcessError as e:
            progress.update(progress_task, total=1, completed=1)
            print_error(
                f"Installing Python dependencies failed. The command that failed was:\n\n[b]{' '.join(cmd)}[/b]",
                e.stderr.strip(),
            )


def _get_version_number(branch_name: str) -> float:
    """Get the Odoo version number as a float based on the branch name.

    :param branch_name: The Odoo branch name to get the version number from.
    :type branch_name: str
    :return: The version number as a float.
    :rtype: float
    """
    match = re.search(r"(\d+.\d)", branch_name)
    if match:
        return float(match.group(0))
    return 0.0


def _enable_js_tooling(root_dir: Path) -> None:
    """Enable Javascript tooling in the Community and Enterprise repositories.

    :param root_dir: The parent directory of the `odoo` and `enterprise` repositories.
    :type root_dir: :class:`pathlib.Path`
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
    try:
        cmd = ["npm", "install"]
        subprocess.run(cmd, capture_output=True, check=True, cwd=com_dir, text=True)
    except CalledProcessError as e:
        print_error(
            f"Installing Javascript tooling dependencies failed. The command that failed was:\n\n"
            f"[b]{' '.join(cmd)}[/b]",
            e.stderr.strip(),
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
            print_error("Modifying the jsconfig.json file to use relative paths failed.", e.strerror)
            return
    # Copy over node_modules and package-lock.json to avoid "npm install" twice.
    shutil.copyfile(com_dir / "package-lock.json", ent_dir / "package-lock.json")
    shutil.copytree(com_dir / "node_modules", ent_dir / "node_modules", dirs_exist_ok=True)


def _disable_js_tooling(root_dir: Path) -> None:
    """Disable Javascript tooling in the Community and Enterprise repositories.

    :param root_dir: The parent directory of the "odoo" and "enterprise" repositories.
    :type root_dir: :class:`pathlib.Path`
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
