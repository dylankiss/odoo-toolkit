import os
import re
import shutil
import subprocess
from enum import Enum
from pathlib import Path
from subprocess import PIPE, CalledProcessError, Popen
from typing import Annotated
from venv import EnvBuilder

from typer import Option

from .common import (
    TransientProgress,
    app,
    print,
    print_command_title,
    print_error,
    print_header,
    print_subheader,
    print_success,
    print_warning,
)


class OdooRepo(str, Enum):
    DESIGN_THEMES = "design-themes"
    DOCUMENTATION = "documentation"
    ENTERPRISE = "enterprise"
    INDUSTRY = "industry"
    INTERNAL = "internal"
    ODOO = "odoo"
    ODOOFIN = "odoofin"
    O_SPREADSHEET = "o-spreadsheet"
    UPGRADE = "upgrade"
    UPGRADE_UTIL = "upgrade-util"


DEFAULT_BRANCHES = ["16.0", "17.0", "saas-17.2", "saas-17.4", "18.0", "master"]
DEFAULT_REPOS = [
    OdooRepo.ODOO,
    OdooRepo.ENTERPRISE,
    OdooRepo.DESIGN_THEMES,
    OdooRepo.UPGRADE,
    OdooRepo.UPGRADE_UTIL,
]
MULTI_BRANCH_REPOS = [
    OdooRepo.ODOO,
    OdooRepo.ENTERPRISE,
    OdooRepo.DESIGN_THEMES,
    OdooRepo.DOCUMENTATION,
    OdooRepo.INDUSTRY,
    OdooRepo.O_SPREADSHEET,
]
SINGLE_BRANCH_REPOS = [
    OdooRepo.ODOOFIN,
    OdooRepo.UPGRADE,
    OdooRepo.UPGRADE_UTIL,
    OdooRepo.INTERNAL,
]
MULTIVERSE_CONFIG_DIR = Path(__file__).parent / "multiverse_config"


@app.command()
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
    reset_config: Annotated[
        bool,
        Option(
            "--reset-config",
            help="Reset every specified worktree's Ruff config, Python virtual environment and dependencies, and optional Visual Studio Code config.",
        ),
    ] = False,
    vscode: Annotated[
        bool,
        Option("--vscode", help="Copy settings and debug configurations for Visual Studio Code."),
    ] = False,
):
    """
    Set up an Odoo Multiverse environment, having different branches checked out at the same time.
    """
    print_command_title(":earth_africa: Odoo Multiverse")

    # Ensure the multiverse directory exists.
    if multiverse_dir.is_file():
        print_error(f"The provided multiverse path [b]{multiverse_dir}[/b] is not a directory. Aborting ...\n")
        return
    multiverse_dir.mkdir(parents=True, exist_ok=True)
    print(f"Multiverse Directory: [b]{multiverse_dir}[/b]\n")

    # Ensure the worktree source directory exists.
    worktree_src_dir = multiverse_dir / ".worktree-source"
    if worktree_src_dir.is_file():
        print_error(f"The worktree source directory path [b]{worktree_src_dir}[/b] is not a directory. Aborting ...\n")
        return
    worktree_src_dir.mkdir(parents=True, exist_ok=True)

    # Determine which repositories to configure.
    multi_branch_repos = [repo for repo in MULTI_BRANCH_REPOS if repo in repositories]
    single_branch_repos = [repo for repo in SINGLE_BRANCH_REPOS if repo in repositories]

    # Clone all bare repositories of the multi-branch ones.
    if multi_branch_repos:
        print_header(":honey_pot: Configure Bare Multi-Branch Repositories")

    for repo in multi_branch_repos:
        print(f"Setting up bare repository for [b]{repo.value}[/b] ...")

        # Ensure the repo source directory exists.
        repo_src_dir = worktree_src_dir / repo.value
        if repo_src_dir.is_file():
            print_error(f"The [b]{repo.value}[/b] source path [u]{repo_src_dir}[/u] is not a directory. Aborting ...\n")
            return
        repo_src_dir.mkdir(parents=True, exist_ok=True)

        repo_url = f"git@github.com:odoo/{repo.value}.git"
        repo_dev_url = f"git@github.com:odoo-dev/{repo.value}.git"

        bare_dir = repo_src_dir / ".bare"
        with TransientProgress() as progress:
            try:
                if bare_dir.exists():
                    if bare_dir.is_file() or not _is_git_repo(bare_dir, bare=True):
                        print_error(
                            f"The [b]{repo.value}[/b] path [u]{bare_dir}[/u] is not a Git repository. Aborting ...\n"
                        )
                        return
                    else:
                        print_success(f"Bare repository for [b]{repo.value}[/b] already exists\n")
                        continue

                # Clone the bare repository.
                progress_task = progress.add_task(f"Cloning bare repository [b]{repo.value}[/b] ...", total=101)
                with Popen(
                    cmd := ["git", "clone", "--progress", "--bare", str(repo_url), str(bare_dir)],
                    stderr=PIPE,
                    stdout=PIPE,
                    text=True,
                ) as p:
                    while p.poll() is None:
                        log_line = p.stderr.readline()
                        if match := re.search(r"Receiving objects:\s+(\d+)%", log_line):
                            completed = int(match.group(1))
                            progress.update(progress_task, completed=completed)
                progress.update(progress_task, total=1, completed=1)

                # Explicitly set the remote origin fetch so we can fetch remote branches.
                progress_task = progress.add_task(
                    f"Setting up origin fetch configuration for [b]{repo.value}[/b] ...", total=None
                )
                subprocess.run(
                    cmd := [
                        "git",
                        "-C",
                        str(bare_dir),
                        "config",
                        "remote.origin.fetch",
                        "+refs/heads/*:refs/remotes/origin/*",
                    ],
                    capture_output=True,
                    check=True,
                )
                progress.update(progress_task, total=1, completed=1)

                if repo not in (OdooRepo.DOCUMENTATION, OdooRepo.O_SPREADSHEET):
                    # Add the "odoo-dev" repository equivalent as a remote named "dev".
                    progress_task = progress.add_task("Adding [b]odoo-dev[/b] remote as [b]dev[/b] ...", total=2)
                    subprocess.run(
                        cmd := ["git", "-C", str(bare_dir), "remote", "add", "dev", str(repo_dev_url)],
                        capture_output=True,
                        check=True,
                        text=True,
                    )
                    progress.update(progress_task, advance=1)

                    # Make sure people can't push on the "origin" remote when there is a "dev" remote.
                    subprocess.run(
                        cmd := [
                            "git",
                            "-C",
                            str(bare_dir),
                            "remote",
                            "set-url",
                            "--push",
                            "origin",
                            "do_not_push_on_this_repo",
                        ],
                        capture_output=True,
                        check=True,
                        text=True,
                    )
                    progress.update(progress_task, advance=1)

                # Create the ".git" file pointing to the ".bare" directory.
                progress_task = progress.add_task("Finishing Git config ...", total=None)
                with (repo_src_dir / ".git").open("x", encoding="utf-8") as git_file:
                    git_file.write("gitdir: ./.bare")
                progress.update(progress_task, total=1, completed=1)

                # Fetch all remote branches to create the worktrees off later.
                progress_task = progress.add_task("Fetching all branches ...", total=None)
                subprocess.run(
                    cmd := ["git", "-C", str(repo_src_dir), "fetch"],
                    capture_output=True,
                    check=True,
                )
                progress.update(progress_task, total=1, completed=1)

                # Prune worktrees that were manually deleted before, so Git doesn't get confused.
                progress_task = progress.add_task("Pruning non-existing worktrees ...", total=None)
                subprocess.run(
                    cmd := ["git", "-C", str(repo_src_dir), "worktree", "prune"],
                    capture_output=True,
                    check=True,
                    text=True,
                )
                progress.update(progress_task, total=1, completed=1)

            except CalledProcessError as e:
                print_error(
                    f"Setting up the bare repository for [b]{repo.value}[/b] failed. The command that failed was:\n\n[b]{' '.join(cmd)}[/b]",
                    e.stderr.strip(),
                )
                return
            except OSError as e:
                print_error(
                    f"Setting up the bare repository for [b]{repo.value}[/b] failed during file handling.",
                    e.strerror,
                )
                return

        print_success(f"Set up bare repository for [b]{repo.value}[/b]\n")

    # Clone all single-branch repositories.
    if single_branch_repos:
        print_header(":honey_pot: Configure Single-Branch Repositories")

    for repo in single_branch_repos:
        print(f"Setting up repository for [bold]{repo.value}[/bold] ...")
        # Check if the repo source directory already exists.
        repo_src_dir = worktree_src_dir / repo.value
        if repo_src_dir.exists():
            if repo_src_dir.is_file() or not _is_git_repo(repo_src_dir):
                print_error(
                    f"The [b]{repo.value}[/b] path [u]{repo_src_dir}[/u] is not a Git repository. Aborting ...\n"
                )
                return
            else:
                print_success(f"Repository for [b]{repo.value}[/b] already exists\n")
                continue

        repo_url = f"git@github.com:odoo/{repo.value}.git"
        with TransientProgress() as progress:
            try:
                # Clone the repository.
                progress_task = progress.add_task(f"Cloning repository [b]{repo.value}[/b] ...", total=101)
                with Popen(
                    cmd := ["git", "clone", "--progress", str(repo_url), str(repo_src_dir)],
                    stderr=PIPE,
                    stdout=PIPE,
                    text=True,
                ) as p:
                    while p.poll() is None:
                        log_line = p.stderr.readline()
                        if match := re.search(r"Receiving objects:\s+(\d+)%", log_line):
                            completed = int(match.group(1))
                            progress.update(
                                progress_task,
                                completed=completed,
                            )
                progress.update(progress_task, total=1, completed=1)
            except CalledProcessError as e:
                print_error(
                    f"Cloning the repository [b]{repo.value}[/b] failed. The command that failed was:\n\n[b]{' '.join(cmd)}[/b]",
                    e.stderr.strip(),
                )
                return

        print_success(f"Set up repository [b]{repo.value}[/b]\n")

    # Add a git worktree for every branch.
    print_header(":deciduous_tree: Configure Worktrees")

    for branch in branches:
        if not branch:
            continue

        print_subheader(f":gear: Configure Worktrees for [b]{branch}[/b]")

        # Ensure the branch directory exists.
        branch_dir = multiverse_dir / branch
        if branch_dir.is_file():
            print_warning(f"The [b]{branch}[/b] path [u]{branch_dir}[/u] is not a directory. Skipping ...\n")
            continue
        branch_dir.mkdir(parents=True, exist_ok=True)

        for repo in multi_branch_repos:
            print(f"Adding worktree [b]{branch}[/b] for repository [b]{repo.value}[/b] ...")
            bare_repo_dir = worktree_src_dir / repo.value
            branch_repo_dir = branch_dir / repo.value
            if branch_repo_dir.exists():
                if branch_repo_dir.is_file() or not _is_git_repo(branch_repo_dir):
                    print_warning(
                        f"The [b]{repo.value}[/b] worktree [u]{branch_repo_dir}[/u] is not a Git repository. Skipping ...\n"
                    )
                    continue
                else:
                    print_success(f"The [b]{repo.value}[/b] worktree [u]{branch_repo_dir}[/u] already exists.\n")
                    continue

            # Check whether the branch we want to add exists.
            try:
                subprocess.run(
                    ["git", "-C", str(bare_repo_dir), "rev-parse", "--verify", branch], capture_output=True, check=True
                )
            except CalledProcessError:
                print_warning(f"The [b]{repo.value}[/b] branch [b]{branch}[/b] does not exist. Skipping ...\n")
                continue

            with TransientProgress() as progress:
                try:
                    # Checkout the worktree for the specified branch.
                    progress_task = progress.add_task(f"Adding worktree [b]{branch}[/b] ...", total=4)
                    subprocess.run(
                        cmd := ["git", "-C", str(bare_repo_dir), "worktree", "add", str(branch_repo_dir), branch],
                        capture_output=True,
                        check=True,
                        text=True,
                    )
                    progress.update(progress_task, advance=1)

                    # Make sure the worktree references the right upstream branch.
                    subprocess.run(
                        cmd := [
                            "git",
                            "-C",
                            str(branch_repo_dir),
                            "branch",
                            "--set-upstream-to",
                            f"origin/{branch}",
                        ],
                        capture_output=True,
                        check=True,
                    )
                    progress.update(progress_task, advance=1)

                    # Make link in .git to git worktree relative.
                    with (branch_repo_dir / ".git").open("r", encoding="utf-8") as git_file:
                        absolute_gitdir = Path(git_file.read()[len("gitdir: ") :].strip())
                    relative_gitdir = os.path.relpath(absolute_gitdir, branch_repo_dir)
                    with (branch_repo_dir / ".git").open("w", encoding="utf-8") as git_file:
                        git_file.write(f"gitdir: {relative_gitdir}\n")
                    progress.update(progress_task, advance=1)

                    # Make link in git worktree gitdir to checked out repo relative.
                    with (absolute_gitdir / "gitdir").open("r", encoding="utf-8") as gitdir_file:
                        relative_git = os.path.relpath(Path(gitdir_file.read().strip()), absolute_gitdir)
                    with (absolute_gitdir / "gitdir").open("w", encoding="utf-8") as gitdir_file:
                        gitdir_file.write(f"{relative_git}\n")
                    progress.update(progress_task, advance=1)

                except CalledProcessError as e:
                    print_error(
                        f"Adding the worktree [b]{branch}[/b] for repository [b]{repo.value}[/b] failed. The command that failed was:\n\n[b]{' '.join(cmd)}[/b]",
                        e.stderr.strip(),
                    )
                    continue
                except OSError as e:
                    print_error(
                        f"Adding the worktree [b]{branch}[/b] for repository [b]{repo.value}[/b] failed during file handling.",
                        e.strerror,
                    )
                    continue

            print_success(f"Added worktree [b]{branch}[/b] for repository [b]{repo.value}[/b].\n")

        for repo in single_branch_repos:
            print(f"Linking repository [b]{repo.value}[/b] to worktree [b]{branch}[/b] ...")
            branch_repo_dir = branch_dir / repo.value
            repo_src_dir = worktree_src_dir / repo.value
            if branch_repo_dir.exists():
                if branch_repo_dir.is_file() or not _is_git_repo(branch_repo_dir):
                    print_warning(
                        f"The [b]{repo.value}[/b] path [u]{branch_repo_dir}[/u] is not a Git repository. Skipping ...\n"
                    )
                    continue
                else:
                    print_success(f"The [b]{repo.value}[/b] repository link [u]{branch_repo_dir}[/u] already exists.\n")
                    continue
            # Create a symlink in the worktree directory to this single-branch repository.
            branch_repo_dir.symlink_to(repo_src_dir, target_is_directory=True)
            print_success(f"Added symlink for repository [b]{repo.value}[/b] to worktree [b]{branch}[/b].\n")

        print(f"Finishing configuration for worktree [b]{branch}[/b] ...")

        with TransientProgress() as progress:
            # Copy Ruff configuration.
            if not (branch_dir / "pyproject.toml").exists() or reset_config:
                progress_task = progress.add_task(
                    f"Setting up Ruff configuration for worktree [b]{branch}[/b] ...", total=None
                )
                try:
                    shutil.copyfile(MULTIVERSE_CONFIG_DIR / "pyproject.toml", branch_dir / "pyproject.toml")
                except OSError as e:
                    print_error(f"Copying [b]pyproject.toml[/b] to worktree [b]{branch}[/b] failed.", e.strerror)
                progress.update(progress_task, total=1, completed=1)

            # Copy optional Python requirements.
            if not (branch_dir / "requirements.txt").exists() or reset_config:
                progress_task = progress.add_task(
                    f"Copying optional Python requirements to worktree [b]{branch}[/b] ...", total=None
                )
                try:
                    shutil.copyfile(MULTIVERSE_CONFIG_DIR / "requirements.txt", branch_dir / "requirements.txt")
                except OSError as e:
                    print_error(f"Copying [b]requirements.txt[/b] to worktree [b]{branch}[/b] failed.", e.strerror)
                progress.update(progress_task, total=1, completed=1)

            # Copy Visual Studio Code configuration.
            if vscode and (not (branch_dir / ".vscode").exists() or reset_config):
                progress_task = progress.add_task(
                    f"Copying Visual Studio Code configuration to worktree [b]{branch}[/b] ...", total=None
                )
                try:
                    shutil.copytree(MULTIVERSE_CONFIG_DIR / ".vscode", branch_dir / ".vscode")
                except shutil.Error as e:
                    print_error(
                        f"Copying the [b].vscode[/b] settings directory to worktree [b]{branch}[/b] failed.", e.strerror
                    )
                progress.update(progress_task, total=1, completed=1)

            # Configure Python virtual environment.
            venv_path = branch_dir / ".venv"
            if venv_path.is_file():
                print_warning(f"The path [u]{venv_path}[/u] is not a directory. Skipping ...\n")
            else:
                if venv_path.is_dir() and reset_config:
                    progress_task = progress.add_task(
                        f"Removing existing virtual environment for worktree [b]{branch}[/b] ...", total=None
                    )
                    shutil.rmtree(venv_path)
                    progress.update(progress_task, total=1, completed=1)

                progress_task = progress.add_task(
                    f"Configuring Python virtual environment for worktree [b]{branch}[/b] ...", total=None
                )
                EnvBuilder(with_pip=True, upgrade=True).create(venv_path)
                progress.update(progress_task, total=1, completed=1)

                # Locate the Python executable in the virtual environment.
                python_executable = venv_path / "bin" / "python"  # Linux and MacOS
                if not python_executable.exists():
                    python_executable = venv_path / "Scripts" / "python.exe"  # Windows

                # Install Python dependencies using pip.
                try:
                    # Try upgrading pip.
                    subprocess.run(
                        cmd := [
                            str(python_executable),
                            "-m",
                            "pip",
                            "install",
                            "-q",
                            "--upgrade",
                            "pip",
                        ],
                        capture_output=True,
                        check=True,
                    )
                    if (branch_dir / "odoo").is_dir():
                        progress_task = progress.add_task(
                            f"Installing [b]odoo[/b] dependencies for worktree [b]{branch}[/b] ...", total=None
                        )
                        subprocess.run(
                            cmd := [
                                str(python_executable),
                                "-m",
                                "pip",
                                "install",
                                "-q",
                                "-r",
                                str(branch_dir / "odoo" / "requirements.txt"),
                            ],
                            capture_output=True,
                            check=True,
                        )
                        progress.update(progress_task, total=1, completed=1)
                    if (branch_dir / "documentation").is_dir():
                        progress_task = progress.add_task(
                            f"Installing [b]documentation[/b] dependencies for worktree [b]{branch}[/b] ...", total=None
                        )
                        subprocess.run(
                            cmd := [
                                str(python_executable),
                                "-m",
                                "pip",
                                "install",
                                "-q",
                                "-r",
                                str(branch_dir / "documentation" / "requirements.txt"),
                            ],
                            capture_output=True,
                            check=True,
                        )
                        progress.update(progress_task, total=1, completed=1)
                    progress_task = progress.add_task(
                        f"Installing optional dependencies for worktree [b]{branch}[/b] ...", total=None
                    )
                    subprocess.run(
                        cmd := [
                            str(python_executable),
                            "-m",
                            "pip",
                            "install",
                            "-q",
                            "-r",
                            str(branch_dir / "requirements.txt"),
                        ],
                        capture_output=True,
                        check=True,
                    )
                    progress.update(progress_task, total=1, completed=1)

                except CalledProcessError as e:
                    progress.update(progress_task, total=1, completed=1)
                    print_error(
                        f"Installing Python dependencies failed. The command that failed was:\n\n[b]{' '.join(cmd)}[/b]",
                        e.stderr.strip(),
                    )

            # Set up Javascript tooling.
            if _get_version_number(branch) >= 14.5:
                progress_task = progress.add_task(
                    f"Setting up Javascript tooling for worktree [b]{branch}[/b] ...", total=None
                )
                _disable_js_tooling(branch_dir)
                _enable_js_tooling(branch_dir)
                progress.update(progress_task, total=1, completed=1)

        print_success(f"Finished configuration for worktree [b]{branch}[/b]\n")

    print_header(":muscle: Great! You're now ready to work on multiple versions of Odoo")


def _is_git_repo(path: str | Path, bare: bool = False) -> bool:
    """
    Checks whether the given path is a Git repository.

    :param path: The path to the potential Git repository
    :type path: str | Path
    :param bare: Should we check for a bare repository instead
    :type bare: bool
    :return: True if it is a Git repository, False if not
    :rtype: bool
    """
    option = "--is-bare-repository" if bare else "--is-inside-work-tree"
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", option],
            capture_output=True,
            check=True,
            text=True,
        )
        return result.stdout.strip() == "true"
    except CalledProcessError:
        return False


def _get_version_number(branch_name: str) -> float:
    """
    Get the Odoo version number as a float based on the branch name.

    :param branch_name: The Odoo branch name to get the version number from
    :type branch_name: str
    :return: The version number as a float
    :rtype: float
    """
    if match := re.search(r"(\d+.\d)", branch_name):
        return float(match.group(0))
    return 0.0


def _enable_js_tooling(root_dir: Path):
    """
    Enables Javascript tooling in the Community and Enterprise repositories.

    :param root_dir: The parent directory of the "odoo" and "enterprise" repositories
    :type root_dir: Path
    """
    com_dir = root_dir / "odoo"
    ent_dir = root_dir / "enterprise"
    tooling_dir = com_dir / "addons" / "web" / "tooling"

    if com_dir.is_dir():
        # Setup tools in Community
        shutil.copyfile(tooling_dir / "_eslintignore", com_dir / ".eslintignore")
        shutil.copyfile(tooling_dir / "_eslintrc.json", com_dir / ".eslintrc.json")
        if _get_version_number(root_dir.name) > 16.0:
            shutil.copyfile(tooling_dir / "_jsconfig.json", com_dir / "jsconfig.json")
        shutil.copyfile(tooling_dir / "_package.json", com_dir / "package.json")
        try:
            subprocess.run(
                cmd := ["npm", "install"],
                capture_output=True,
                check=True,
                cwd=com_dir,
                text=True,
            )
        except CalledProcessError as e:
            print_error(
                f"Installing Javascript tooling dependencies failed. The command that failed was:\n\n[b]{' '.join(cmd)}[/b]",
                e.stderr.strip(),
            )
            return

    if ent_dir.is_dir():
        # Setup tools in Enterprise
        shutil.copyfile(tooling_dir / "_eslintignore", ent_dir / ".eslintignore")
        shutil.copyfile(tooling_dir / "_eslintrc.json", ent_dir / ".eslintrc.json")
        shutil.copyfile(tooling_dir / "_package.json", ent_dir / "package.json")
        if _get_version_number(root_dir.name) > 16.0:
            shutil.copyfile(tooling_dir / "_jsconfig.json", ent_dir / "jsconfig.json")
            try:
                # Replace "addons" path with relative path from Enterprise in jsconfig.json
                with (ent_dir / "jsconfig.json").open("r", encoding="utf-8") as jsconfig_file:
                    jsconfig_content = jsconfig_file.read().replace(
                        "addons", f"{os.path.relpath(com_dir, ent_dir)}/addons"
                    )
                with (ent_dir / "jsconfig.json").open("w", encoding="utf-8") as jsconfig_file:
                    jsconfig_file.write(jsconfig_content)
            except OSError as e:
                print_error("Modifying the jsconfig.json file to use relative paths failed.", e.strerror)
                return
        # Copy over node_modules and package-lock.json to avoid "npm install" twice.
        shutil.copyfile(com_dir / "package-lock.json", ent_dir / "package-lock.json")
        shutil.copytree(com_dir / "node_modules", ent_dir / "node_modules")


def _disable_js_tooling(root_dir: Path):
    """
    Disables Javascript tooling in the Community and Enterprise repositories.

    :param root_dir: The parent directory of the "odoo" and "enterprise" repositories.
    :type root_dir: Path
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
