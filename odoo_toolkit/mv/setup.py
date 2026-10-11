import json
import os
import shutil
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from subprocess import CalledProcessError
from typing import Annotated, Any

from git import GitCommandError, Repo
from rich.progress import TaskID
from typer import Exit, Option, Typer

from odoo_toolkit.common import (
    EMPTY_LIST,
    TransientProgress,
    normalize_list_option,
    print,
    print_command_title,
    print_error,
    print_header,
    print_success,
    print_warning,
)
from odoo_toolkit.versions import get_versions

from .common import MULTI_BRANCH_REPOS, ODOO_DEV_REPOS, SINGLE_BRANCH_REPOS, OdooRepo
from .repos import (
    Outcome,
    RepoResult,
    fetch_branch,
    git_error_message,
    print_result_table,
    run_safely,
)

app = Typer()

DEFAULT_REPOS = [
    OdooRepo.ODOO,
    OdooRepo.ENTERPRISE,
    OdooRepo.DESIGN_THEMES,
    OdooRepo.UPGRADE,
    OdooRepo.UPGRADE_UTIL,
]
# The URL to clone a repository from, given its GitHub owner and name.
GIT_URL = "git@github.com:{owner}/{repo}.git"
# A Git clone progress callback, receiving the operation code, current count, maximum count and message.
CloneProgress = Callable[[int, str | float, str | float | None, str], None]
MULTIVERSE_CONFIG_DIR = Path(__file__).parent.parent / "multiverse_config"
WORKTREE_SOURCE_DIR = ".worktree-source"
# The requirement files to install in each version's virtual environment, relative to the version directory.
VERSION_REQUIREMENTS = [
    Path("odoo/requirements.txt"),
    Path("documentation/requirements.txt"),
    Path("documentation/tests/requirements.txt"),
]
# Files copied into the Community and Enterprise repositories to enable the JavaScript tooling.
JS_TOOLING_FILES = {
    "_eslintignore": ".eslintignore",
    "_eslintrc.json": ".eslintrc.json",
    "_jsconfig.json": "jsconfig.json",
    "_package.json": "package.json",
}


@app.command()
def setup(
    branches: Annotated[
        list[str],
        Option(
            "--branches",
            "-b",
            help="The Odoo versions to set up, like `19.0` or `saas-19.2`. Sets up every supported version if none "
            "are specified.",
        ),
    ] = EMPTY_LIST,
    repositories: Annotated[
        list[OdooRepo],
        Option("--repositories", "-r", help="The Odoo repositories to set up."),
    ] = DEFAULT_REPOS,
    multiverse_dir: Annotated[
        Path,
        Option("--multiverse-dir", "-d", help="The directory to set up the multiverse in."),
    ] = Path.cwd(),  # noqa: B008
    reset_config: Annotated[
        bool,
        Option(
            "--reset-config",
            help="Recreate the Python virtual environments and overwrite the configuration files of every version.",
        ),
    ] = False,
    vscode: Annotated[
        bool,
        Option("--vscode/--no-vscode", help="Write the Visual Studio Code configuration in each version directory."),
    ] = True,
) -> None:
    """Set up an :ringed_planet: Odoo Multiverse environment, having different versions checked out at the same time.

    Every version gets its own directory (like `19.0`), which is a complete workspace you can open in your editor. It
    contains:\n
    - a [`git worktree`](https://git-scm.com/docs/git-worktree) of every multi-branch repository (like `odoo` and
    `enterprise`), sharing the Git history with the other versions,\n
    - a link to every single-branch repository (like `upgrade`), which all versions share,\n
    - a Python virtual environment with the same Python version as Runbot and the version's dependencies,\n
    - the JavaScript tooling, and the Visual Studio Code configuration.\n
    \n
    > Make sure you have set up your **GitHub SSH key**, and installed [`uv`](https://docs.astral.sh/uv/) and `npm`.\n
    \n
    You can run the command as many times as you want. It only adds what's missing, unless you pass `--reset-config`.
    """
    print_command_title(":ringed_planet: Odoo Multiverse Setup")

    if not shutil.which("uv"):
        print_error("This command needs [b]uv[/b]: https://docs.astral.sh/uv/getting-started/installation/\n")
        raise Exit(1)

    versions = get_versions()
    branches = normalize_list_option(branches) or list(versions)
    if unsupported := [branch for branch in branches if branch not in versions]:
        print_warning(
            f"Not supported by Runbot anymore: [b]{'[/b], [b]'.join(unsupported)}[/b]. "
            "Their Python environments use uv's default Python version.\n",
        )
    multi_branch_repos = [repo for repo in MULTI_BRANCH_REPOS if repo in repositories]
    single_branch_repos = [repo for repo in SINGLE_BRANCH_REPOS if repo in repositories]

    multiverse_dir = multiverse_dir.expanduser().resolve()
    multiverse_dir.mkdir(parents=True, exist_ok=True)
    print(f"Multiverse directory: [u]{multiverse_dir}[/u]\n")
    _copy_root_config(multiverse_dir, reset_config=reset_config)

    print_header(":honey_pot: Clone Repositories")
    clone_results = _clone_repos(multiverse_dir, multi_branch_repos, single_branch_repos)
    print_result_table(clone_results)
    cloned = {result.name for result in clone_results if result.outcome != Outcome.FAILED}

    print_header(":deciduous_tree: Add Worktrees")
    worktree_results = _add_worktrees(multiverse_dir, branches, [r for r in multi_branch_repos if r.value in cloned])
    print_result_table(worktree_results)

    print_header(":gear: Configure Versions")
    version_results = _configure_versions(
        multiverse_dir,
        branches,
        shared_repos=[r for r in single_branch_repos if r.value in cloned],
        reset_config=reset_config,
        vscode=vscode,
    )
    for branch, results in version_results.items():
        print(f"[b]{branch}[/b]")
        print_result_table(results)

    all_results = [*clone_results, *worktree_results, *(r for rs in version_results.values() for r in rs)]
    failed = [result for result in all_results if result.outcome == Outcome.FAILED]
    if failed:
        print_error(f"{len(failed)} step(s) failed. Fix the problems above and run the command again.\n")
        raise Exit(1)
    print_success("Your Odoo Multiverse is ready! Open a version directory in your editor to start working.\n")


def _copy_root_config(multiverse_dir: Path, *, reset_config: bool) -> None:
    """Copy the configuration shared by all versions to the multiverse directory."""
    for filename in ("ruff.toml", "requirements.txt"):
        if reset_config or not (multiverse_dir / filename).exists():
            shutil.copyfile(MULTIVERSE_CONFIG_DIR / filename, multiverse_dir / filename)


# Cloning


def _clone_repos(
    multiverse_dir: Path,
    multi_branch_repos: list[OdooRepo],
    single_branch_repos: list[OdooRepo],
) -> list[RepoResult]:
    """Clone the multi-branch repositories as bare repositories, and the single-branch ones as regular ones."""
    with ThreadPoolExecutor() as executor, TransientProgress() as progress:

        def clone(repo: OdooRepo) -> RepoResult:
            task = progress.add_task(f"Cloning [b]{repo.value}[/b]", total=None)

            def update_progress(_op_code: int, cur_count: str | float, max_count: str | float | None, _msg: str) -> None:
                progress.update(task, completed=float(cur_count), total=float(max_count) if max_count else None)

            if repo in multi_branch_repos:
                return clone_bare_repo(repo, multiverse_dir / WORKTREE_SOURCE_DIR / repo.value, update_progress)
            return clone_repo(repo, multiverse_dir / repo.value, update_progress)

        futures = [
            executor.submit(run_safely, repo.value, lambda repo=repo: clone(repo))
            for repo in [*multi_branch_repos, *single_branch_repos]
        ]
        return [future.result() for future in futures]


def clone_bare_repo(repo: OdooRepo, repo_dir: Path, progress: CloneProgress | None = None) -> RepoResult:
    """Clone a repository as a bare repository to add worktrees from, and configure its remotes.

    The clone is partial: file contents are only downloaded when a worktree needs them, which saves gigabytes.
    """
    bare_dir = repo_dir / ".bare"
    if bare_dir.exists():
        bare_repo = Repo(bare_dir)
        message = "already cloned"
    else:
        bare_repo = Repo.clone_from(
            GIT_URL.format(owner="odoo", repo=repo.value),
            bare_dir,
            progress=progress,
            bare=True,
            multi_options=["--filter=blob:none"],
        )
        message = "cloned"

    # Fetch every remote branch into a remote-tracking branch, which a bare clone doesn't do by default.
    bare_repo.git.config("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    if repo in ODOO_DEV_REPOS:
        if "dev" not in {remote.name for remote in bare_repo.remotes}:
            bare_repo.create_remote("dev", GIT_URL.format(owner="odoo-dev", repo=repo.value))
        # Pushing goes to the "dev" remote, never to "origin".
        bare_repo.git.remote("set-url", "--push", "origin", "NO_PUSH_TRY_DEV_REPO")
    # Allow running Git commands from the repository directory itself.
    if not (repo_dir / ".git").exists():
        (repo_dir / ".git").write_text("gitdir: ./.bare\n", encoding="utf-8")
    # Forget worktrees whose directories were deleted.
    bare_repo.git.worktree("prune")
    return RepoResult(repo.value, Outcome.UPDATED if message == "cloned" else Outcome.SKIPPED, message)


def clone_repo(repo: OdooRepo, repo_dir: Path, progress: CloneProgress | None = None) -> RepoResult:
    """Clone a single-branch repository, which all versions share."""
    if (repo_dir / ".git").exists():
        return RepoResult(repo.value, Outcome.SKIPPED, "already cloned")
    if repo_dir.exists() and any(repo_dir.iterdir()):
        return RepoResult(repo.value, Outcome.FAILED, f"[u]{repo_dir}[/u] exists but is not a Git repository")
    Repo.clone_from(GIT_URL.format(owner="odoo", repo=repo.value), repo_dir, progress=progress)
    return RepoResult(repo.value, Outcome.UPDATED, "cloned")


# Worktrees


def _add_worktrees(multiverse_dir: Path, branches: list[str], repos: list[OdooRepo]) -> list[RepoResult]:
    """Add a worktree for every version of every multi-branch repository.

    The repositories are handled in parallel, but the versions of one repository one after the other, since Git locks
    a repository while adding a worktree to it.
    """

    def add_for_repo(repo: OdooRepo, task: TaskID) -> list[RepoResult]:
        results: list[RepoResult] = []
        for branch in branches:
            results.append(
                run_safely(
                    f"{branch}/{repo.value}",
                    lambda branch=branch: add_worktree(
                        multiverse_dir / WORKTREE_SOURCE_DIR / repo.value / ".bare",
                        multiverse_dir / branch / repo.value,
                        branch,
                    ),
                ),
            )
            progress.advance(task)
        return results

    with ThreadPoolExecutor() as executor, TransientProgress() as progress:
        futures = [
            executor.submit(
                add_for_repo, repo, progress.add_task(f"Adding [b]{repo.value}[/b] worktrees", total=len(branches)),
            )
            for repo in repos
        ]
        results = [result for future in futures for result in future.result()]
    # Group the results per version.
    return sorted(results, key=lambda result: branches.index(result.name.split("/")[0]))


def add_worktree(bare_dir: Path, worktree_dir: Path, branch: str) -> RepoResult:
    """Add a worktree for a branch, tracking the branch on `origin`.

    :param bare_dir: The bare repository to add the worktree from.
    :param worktree_dir: The directory of the new worktree.
    :param branch: The branch to check out.
    :return: The result of adding the worktree.
    """
    name = f"{worktree_dir.parent.name}/{worktree_dir.name}"
    if (worktree_dir / ".git").exists():
        return RepoResult(name, Outcome.SKIPPED, "already set up")
    if worktree_dir.exists() and any(worktree_dir.iterdir()):
        return RepoResult(name, Outcome.FAILED, f"[u]{worktree_dir}[/u] exists but is not a Git worktree")

    bare_repo = Repo(bare_dir)
    try:
        fetch_branch(bare_repo, "origin", branch)
    except GitCommandError as e:
        if "couldn't find remote ref" in git_error_message(e):
            return RepoResult(name, Outcome.SKIPPED, f"no [b]{branch}[/b] branch in this repository")
        raise

    upstream = f"origin/{branch}"
    if branch in bare_repo.heads:
        # The bare clone created a local branch, which may be behind the remote one.
        bare_repo.git.worktree("add", str(worktree_dir), branch)
        worktree_repo = Repo(worktree_dir)
        worktree_repo.git.branch("--set-upstream-to", upstream, branch)
        try:
            worktree_repo.git.merge("--ff-only", upstream)
        except GitCommandError:
            return RepoResult(name, Outcome.WARNING, f"added, but [b]{branch}[/b] has diverged from [b]{upstream}[/b]")
    else:
        bare_repo.git.worktree("add", "--track", "-b", branch, str(worktree_dir), upstream)
    return RepoResult(name, Outcome.UPDATED, "added")


# Version configuration


def _configure_versions(
    multiverse_dir: Path,
    branches: list[str],
    *,
    shared_repos: list[OdooRepo],
    reset_config: bool,
    vscode: bool,
) -> dict[str, list[RepoResult]]:
    """Configure every version directory in parallel."""
    versions = get_versions()

    def configure(branch: str, task: TaskID) -> list[RepoResult]:
        version_dir = multiverse_dir / branch
        if not version_dir.is_dir():
            return [RepoResult("version", Outcome.SKIPPED, "no repositories for this version")]
        steps: list[tuple[str, Callable[[], RepoResult]]] = [
            ("shared repositories", lambda: link_shared_repos(version_dir, shared_repos)),
            (
                "python",
                lambda: setup_python_env(
                    version_dir,
                    python=versions[branch].python if branch in versions else None,
                    requirements=[version_dir / path for path in VERSION_REQUIREMENTS]
                    + [multiverse_dir / "requirements.txt"],
                    reset=reset_config,
                ),
            ),
            ("javascript", lambda: setup_js_tooling(version_dir)),
        ]
        if vscode:
            steps.append(
                ("vscode", lambda: write_vscode_config(version_dir, shared_repos=shared_repos, reset=reset_config)),
            )
        results: list[RepoResult] = []
        for name, step in steps:
            progress.update(task, description=f"Configuring [b]{branch}[/b]: {name}")
            results.append(run_safely(name, step))
            progress.advance(task)
        return results

    with ThreadPoolExecutor() as executor, TransientProgress() as progress:
        futures = {
            branch: executor.submit(
                configure, branch, progress.add_task(f"Configuring [b]{branch}[/b]", total=4 if vscode else 3),
            )
            for branch in branches
        }
        return {branch: future.result() for branch, future in futures.items()}


def link_shared_repos(version_dir: Path, shared_repos: list[OdooRepo]) -> RepoResult:
    """Link the single-branch repositories, which all versions share, into a version directory.

    The links are relative, so the multiverse directory can be moved.
    """
    linked: list[str] = []
    blocked: list[str] = []
    for repo in shared_repos:
        link = version_dir / repo.value
        target = Path("..") / repo.value
        if link.is_symlink():
            if link.readlink() != target:
                link.unlink()
                link.symlink_to(target, target_is_directory=True)
            linked.append(repo.value)
        elif link.exists():
            blocked.append(repo.value)
        else:
            link.symlink_to(target, target_is_directory=True)
            linked.append(repo.value)

    if blocked:
        return RepoResult(
            "shared repositories",
            Outcome.WARNING,
            f"[b]{'[/b], [b]'.join(blocked)}[/b] already exist as regular folders, so they aren't linked to the "
            "shared repositories. Remove them to link them.",
        )
    if not linked:
        return RepoResult("shared repositories", Outcome.SKIPPED, "none selected")
    return RepoResult("shared repositories", Outcome.UPDATED, f"linked [b]{'[/b], [b]'.join(linked)}[/b]")


def setup_python_env(version_dir: Path, *, python: str | None, requirements: list[Path], reset: bool) -> RepoResult:
    """Create a virtual environment for a version and install its dependencies, using uv.

    The environment serves the editor (code completion, linting, ...). Odoo itself runs in Docker, so packages that
    fail to build on this machine are reported, but don't fail the setup.

    :param version_dir: The version directory to create the `.venv` in.
    :param python: The Python version to use, like `3.12`, or None for uv's default.
    :param requirements: The requirement files to install, if they exist.
    :param reset: Whether to recreate an existing environment.
    :return: The result of the setup.
    """
    venv_dir = version_dir / ".venv"
    if venv_dir.exists() and (reset or (python and get_venv_python(venv_dir) != python)):
        shutil.rmtree(venv_dir)

    venv_cmd = ["uv", "venv", "--seed", "--allow-existing", "--quiet"]
    if python:
        venv_cmd += ["--python", python]
    try:
        _run([*venv_cmd, str(venv_dir)])
    except CalledProcessError as e:
        error = (e.stderr or "").strip().splitlines()
        return RepoResult("python", Outcome.FAILED, f"creating the environment failed: {error[-1] if error else e}")

    # The Odoo language server doesn't follow the venv's symlinked interpreter, so point it to the real path.
    python_path = (venv_dir / "bin" / "python").absolute()
    (version_dir / "odools.toml").write_text(f'[[config]]\npython_path = "{python_path}"\n', encoding="utf-8")

    failed = install_requirements(venv_dir / "bin" / "python", [path for path in requirements if path.is_file()])
    version = get_venv_python(venv_dir) or "?"
    if failed:
        return RepoResult(
            "python",
            Outcome.WARNING,
            f"Python {version}; could not install [b]{'[/b], [b]'.join(failed)}[/b]. Only code completion for these "
            "is affected, since Odoo runs in Docker.",
        )
    return RepoResult("python", Outcome.UPDATED, f"Python {version} with all requirements")


def install_requirements(python: Path, requirement_files: list[Path]) -> list[str]:
    """Install requirement files into a virtual environment, skipping the requirements that fail to install.

    :param python: The Python interpreter of the virtual environment.
    :param requirement_files: The requirement files to install.
    :return: The requirements that failed to install.
    """
    if not requirement_files:
        return []
    install_cmd = ["uv", "pip", "install", "--quiet", "--python", str(python)]
    try:
        _run([*install_cmd, *(arg for path in requirement_files for arg in ("-r", str(path)))])
    except CalledProcessError:
        pass
    else:
        return []

    # Installing everything at once failed, so find out which requirements are to blame.
    failed: list[str] = []
    for requirement in parse_requirements(requirement_files):
        try:
            _run([*install_cmd, requirement])
        except CalledProcessError:  # noqa: PERF203 (each requirement must be tried separately)
            failed.append(requirement.split(";")[0].strip())
    return failed


def parse_requirements(requirement_files: list[Path]) -> list[str]:
    """Get the requirements in requirement files, without comments, options and duplicates.

    :param requirement_files: The requirement files to read.
    :return: The requirements, including their version specifiers and environment markers.
    """
    requirements: dict[str, None] = {}
    for path in requirement_files:
        for line in path.read_text(encoding="utf-8").splitlines():
            requirement = line.split(" #")[0].strip()
            if requirement and not requirement.startswith(("#", "-")):
                requirements[requirement] = None
    return list(requirements)


def get_venv_python(venv_dir: Path) -> str | None:
    """Get the `major.minor` Python version of a virtual environment."""
    config = venv_dir / "pyvenv.cfg"
    if not config.is_file():
        return None
    for line in config.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() in {"version_info", "version"}:
            return ".".join(value.strip().split(".")[:2])
    return None


def setup_js_tooling(version_dir: Path) -> RepoResult:
    """Set up the JavaScript tooling (ESLint, type checking) in the Community and Enterprise repositories."""
    com_dir = version_dir / "odoo"
    ent_dir = version_dir / "enterprise"
    tooling_dir = com_dir / "addons" / "web" / "tooling"
    if not tooling_dir.is_dir():
        return RepoResult("javascript", Outcome.SKIPPED, "no Community repository")
    if not shutil.which("npm"):
        return RepoResult("javascript", Outcome.WARNING, "[b]npm[/b] not found, so the tooling isn't installed")

    for source, target in JS_TOOLING_FILES.items():
        shutil.copyfile(tooling_dir / source, com_dir / target)
    try:
        _run(["npm", "install", "--silent"], cwd=com_dir)
    except CalledProcessError as e:
        error = (e.stderr or "").strip().splitlines()
        return RepoResult("javascript", Outcome.FAILED, f"npm install failed: {error[-1] if error else e}")

    if ent_dir.is_dir():
        for source, target in JS_TOOLING_FILES.items():
            shutil.copyfile(tooling_dir / source, ent_dir / target)
        # Enterprise refers to the Community addons, so make the paths relative to Enterprise.
        jsconfig = ent_dir / "jsconfig.json"
        jsconfig.write_text(
            jsconfig.read_text(encoding="utf-8").replace("addons", f"{os.path.relpath(com_dir, ent_dir)}/addons"),
            encoding="utf-8",
        )
        # Reuse the Community dependencies instead of installing them twice.
        shutil.copyfile(com_dir / "package-lock.json", ent_dir / "package-lock.json")
        shutil.copytree(com_dir / "node_modules", ent_dir / "node_modules", dirs_exist_ok=True, symlinks=True)
    return RepoResult("javascript", Outcome.UPDATED, "ESLint and type checking set up")


def write_vscode_config(version_dir: Path, *, shared_repos: list[OdooRepo], reset: bool) -> RepoResult:
    """Write the Visual Studio Code configuration of a version directory.

    :param version_dir: The version directory.
    :param shared_repos: The single-branch repositories linked into the version directory.
    :param reset: Whether to overwrite existing configuration files.
    :return: The result of writing the configuration.
    """
    vscode_dir = version_dir / ".vscode"
    vscode_dir.mkdir(exist_ok=True)
    source_dir = MULTIVERSE_CONFIG_DIR / ".vscode"
    linked_repos = [repo.value for repo in shared_repos if (version_dir / repo.value).is_symlink()]

    settings = _read_json(source_dir / "settings.json")
    settings["python.defaultInterpreterPath"] = "${workspaceFolder}/.venv/bin/python"
    # Source Control doesn't look inside linked folders by itself.
    settings["git.scanRepositories"] = linked_repos

    launch = _read_json(source_dir / "launch.json")
    # Map the paths of the current development containers, which mount the multiverse in `/code`.
    for configuration in launch["configurations"]:
        if configuration.get("request") == "attach":
            configuration["pathMappings"] = [
                *({"localRoot": f"${{workspaceFolder}}/{repo}", "remoteRoot": f"/code/{repo}"} for repo in linked_repos),
                {"localRoot": "${workspaceFolder}", "remoteRoot": f"/code/{version_dir.name}"},
            ]

    files = {
        vscode_dir / "settings.json": settings,
        vscode_dir / "launch.json": launch,
        vscode_dir / "extensions.json": _read_json(source_dir / "extensions.json"),
    }
    written = [path.name for path, content in files.items() if _write_json(path, content, overwrite=reset)]

    if not written:
        return RepoResult("vscode", Outcome.SKIPPED, "already configured (use [b]--reset-config[/b] to overwrite)")
    return RepoResult("vscode", Outcome.UPDATED, f"wrote [b]{'[/b], [b]'.join(written)}[/b]")


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _write_json(path: Path, content: dict[str, Any], *, overwrite: bool) -> bool:
    """Write a JSON file, unless it exists and shouldn't be overwritten.

    :return: Whether the file was written.
    """
    if path.exists() and not overwrite:
        return False
    path.write_text(json.dumps(content, indent=4) + "\n", encoding="utf-8")
    return True


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    """Run a command, capturing its output.

    :raises CalledProcessError: If the command failed.
    """
    subprocess.run(cmd, cwd=cwd, capture_output=True, check=True, text=True)
