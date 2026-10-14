import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from subprocess import CalledProcessError
from typing import TYPE_CHECKING, Annotated, Any

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
from odoo_toolkit.git_cli import CloneProgress, Git, GitError, clone
from odoo_toolkit.versions import get_versions

from .config import (
    CONFIG_FILE,
    DEFAULT_REPOS,
    ODOO_COM,
    VERSION_PATTERN,
    WORKTREE_SOURCE_DIR,
    ConfigError,
    RepoKind,
    RepoSpec,
    get_configured_repos,
    get_current_dir,
    get_linked_repos,
    get_runs_on,
    load_config,
    resolve_repo,
)
from .remotes import configure_fetching, fetch_on_demand, get_branch_suffix
from .repos import (
    Outcome,
    RepoResult,
    checkout_branch,
    fetch_branch,
    get_known_default_branch,
    get_remote_default_branch,
    print_result_table,
    remote_has_branch,
    run_safely,
)

if TYPE_CHECKING:
    from collections.abc import Callable

app = Typer()

MULTIVERSE_CONFIG_DIR = Path(__file__).parent.parent / "multiverse_config"
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
            help="The Odoo versions to set up, like `19.0` or `saas-19.2`. Defaults to the ones already set up, or "
            "every supported version for a new multiverse.",
        ),
    ] = EMPTY_LIST,
    repositories: Annotated[
        list[str],
        Option(
            "--repositories",
            "-r",
            help="The repositories to set up, like `odoo`, or `odoo-ps/psbe-internal` for another GitHub owner. "
            f"Defaults to the ones already set up, or {', '.join(DEFAULT_REPOS)} for a new multiverse, plus the "
            f"ones in `{CONFIG_FILE}`.",
        ),
    ] = EMPTY_LIST,
    multiverse_dir: Annotated[
        Path | None,
        Option("--multiverse-dir", "-d", help="The directory to set up the multiverse in. Defaults to this directory."),
    ] = None,
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
    branch_suffix: Annotated[
        str | None,
        Option(
            "--branch-suffix",
            help="The suffix of your own branches on the `dev` remote, like `abcd` for `master-fix-xyz-abcd`. It's "
            "remembered for later commands. Defaults to the remembered one, or else the part of your Git email before "
            "the `@`.",
        ),
    ] = None,
) -> None:
    """Set up an :ringed_planet: Odoo Multiverse environment, having different versions checked out at the same time.

    Every version gets its own directory (like `19.0`), which is a complete workspace you can open in your editor. It
    contains:\n
    - a [`git worktree`](https://git-scm.com/docs/git-worktree) of every versioned repository (like `odoo` and
    `enterprise`), sharing the Git history with the other versions,\n
    - a link to every shared repository (like `upgrade`), which all versions use,\n
    - a link to the pinned repositories that run on this version (like `odoofin`),\n
    - a Python virtual environment with the same Python version as Runbot and the version's dependencies,\n
    - the JavaScript tooling, and the Visual Studio Code configuration, where `F5` runs Odoo with the debugger.\n
    \n
    > Make sure you have set up your **GitHub SSH key**, and installed [`uv`](https://docs.astral.sh/uv/) and `npm`.\n
    \n
    To keep the repositories small and fast, every remote only fetches the branches your local branches track (the
    versions you set up and the branches you switched to), the default branch, and your own `dev` branches (ending
    with `-<branch suffix>`). Other branches are fetched on demand by `otk mv switch`.\n
    \n
    Repositories that aren't known Odoo repositories are detected: versioned if they have branches for several Odoo
    versions, shared otherwise. Describe pinned repositories and exceptions in a `multiverse.toml` file in the multiverse directory.\n
    \n
    You can run the command as many times as you want. It only adds what's missing, unless you pass `--reset-config`.
    Shared, pinned and standalone repositories follow their default branch on GitHub.
    """
    print_command_title(":ringed_planet: Odoo Multiverse Setup")

    if not shutil.which("uv"):
        print_error("This command needs [b]uv[/b]: https://docs.astral.sh/uv/getting-started/installation/\n")
        raise Exit(1)

    versions = get_versions()
    multiverse_dir = (multiverse_dir or get_current_dir()).expanduser().resolve()
    branches = normalize_list_option(branches) or get_existing_versions(multiverse_dir) or list(versions)
    if unsupported := [branch for branch in branches if branch not in versions]:
        print_warning(
            f"Not supported by Runbot anymore: [b]{'[/b], [b]'.join(unsupported)}[/b]. "
            "Their Python environments use uv's default Python version.\n",
        )
    multiverse_dir.mkdir(parents=True, exist_ok=True)
    print(f"Multiverse directory: [u]{multiverse_dir}[/u]\n")
    try:
        known_repos = load_config(multiverse_dir)
        repos = [
            resolve_repo(name, known_repos, multiverse_dir)
            for name in normalize_list_option(repositories) or _get_default_repos(multiverse_dir, known_repos)
        ]
    except ConfigError as e:
        print_error(f"{e}\n")
        raise Exit(1) from e
    versioned_repos = [repo for repo in repos if repo.kind == RepoKind.VERSIONED]
    _copy_root_config(multiverse_dir, reset_config=reset_config)

    branch_suffix = branch_suffix or get_branch_suffix()
    if not branch_suffix:
        print_warning("No Git email configured, so the [b]dev[/b] remotes won't fetch any of your branches.\n")

    print_header(":honey_pot: Clone Repositories")
    clone_results = _clone_repos(multiverse_dir, repos, branch_suffix=branch_suffix)
    cloned = [repo for repo in repos if repo.name in {r.name for r in clone_results if r.outcome != Outcome.FAILED}]
    links, link_results = _plan_links(multiverse_dir, cloned, branches)
    print_result_table(clone_results + link_results)

    print_header(":deciduous_tree: Add Worktrees")
    worktree_results = _add_worktrees(multiverse_dir, branches, [r for r in versioned_repos if r in cloned])
    print_result_table(worktree_results)

    print_header(":gear: Configure Versions")
    version_results = _configure_versions(multiverse_dir, branches, links=links, reset_config=reset_config, vscode=vscode)
    for branch, results in version_results.items():
        print(f"[b]{branch}[/b]")
        print_result_table(results)

    all_results = [
        *clone_results, *link_results, *worktree_results, *(r for rs in version_results.values() for r in rs),
    ]
    failed = [result for result in all_results if result.outcome == Outcome.FAILED]
    if failed:
        print_error(f"{len(failed)} step(s) failed. Fix the problems above and run the command again.\n")
        raise Exit(1)
    print_success("Your Odoo Multiverse is ready! Open a version directory in your editor to start working.\n")


def _get_default_repos(multiverse_dir: Path, known_repos: dict[str, RepoSpec]) -> list[str]:
    """Get the repositories to set up when none are given.

    A new multiverse gets the default repositories. An existing one keeps the repositories it has. Both get the ones
    in `multiverse.toml`.
    """
    existing = {path.parent.name for path in (multiverse_dir / WORKTREE_SOURCE_DIR).glob("*/.bare")}
    existing |= {name for name in known_repos if (multiverse_dir / name / ".git").is_dir()}
    base = sorted(existing) if existing else DEFAULT_REPOS
    return list(dict.fromkeys([*base, *get_configured_repos(multiverse_dir)]))


def get_existing_versions(multiverse_dir: Path) -> list[str]:
    """Get the versions already set up in a multiverse, oldest first."""
    if not multiverse_dir.is_dir():
        return []
    names = [path.name for path in multiverse_dir.iterdir() if path.is_dir() and VERSION_PATTERN.fullmatch(path.name)]
    supported = list(get_versions())
    return sorted(names, key=lambda name: (supported.index(name) if name in supported else len(supported), name))


def _copy_root_config(multiverse_dir: Path, *, reset_config: bool) -> None:
    """Copy the configuration shared by all versions to the multiverse directory."""
    for filename in ("ruff.toml", "requirements.txt"):
        if reset_config or not (multiverse_dir / filename).exists():
            shutil.copyfile(MULTIVERSE_CONFIG_DIR / filename, multiverse_dir / filename)


# Cloning


def _clone_repos(multiverse_dir: Path, repos: list[RepoSpec], *, branch_suffix: str | None) -> list[RepoResult]:
    """Clone the versioned repositories as bare repositories, and the other ones as regular ones."""
    with ThreadPoolExecutor() as executor, TransientProgress() as progress:

        def clone_one(repo: RepoSpec) -> RepoResult:
            task = progress.add_task(f"Cloning [b]{repo.name}[/b]", total=None)

            def update_progress(stage: str, current: int, total: int) -> None:
                progress.update(
                    task, description=f"Cloning [b]{repo.name}[/b]: {stage.lower()}", completed=current, total=total,
                )

            if repo.kind == RepoKind.VERSIONED:
                return clone_bare_repo(
                    repo, multiverse_dir / WORKTREE_SOURCE_DIR / repo.name, update_progress, branch_suffix=branch_suffix,
                )
            return clone_repo(repo, multiverse_dir / repo.name, update_progress, branch_suffix=branch_suffix)

        futures = [executor.submit(run_safely, repo.name, lambda repo=repo: clone_one(repo)) for repo in repos]
        return [future.result() for future in futures]


def clone_bare_repo(
    repo: RepoSpec,
    repo_dir: Path,
    progress: CloneProgress | None = None,
    *,
    branch_suffix: str | None = None,
) -> RepoResult:
    """Clone a versioned repository as a bare repository to add worktrees from, and configure its remotes.

    The clone is partial: file contents are only downloaded when a worktree needs them, which saves gigabytes. Only the
    default branch is cloned; the version branches are fetched when adding their worktrees.

    :param repo: The repository to clone.
    :param repo_dir: The directory to clone into, which gets the bare repository in its `.bare` subdirectory.
    :param progress: Called with the progress of the clone.
    :param branch_suffix: The suffix of your own branches, which the `dev` remote fetches.
    :return: The result of the clone.
    """
    bare_dir = repo_dir / ".bare"
    cloned = not bare_dir.exists()
    if cloned:
        bare_repo = clone(repo.url, bare_dir, "--bare", "--single-branch", "--filter=blob:none", progress=progress)
    else:
        bare_repo = Git(bare_dir)

    _configure_remotes(bare_repo, repo)
    fetching_changed = configure_fetching(bare_repo, branch_suffix=branch_suffix)
    # Allow running Git commands from the repository directory itself.
    if not (repo_dir / ".git").exists():
        (repo_dir / ".git").write_text("gitdir: ./.bare\n", encoding="utf-8")
    # Forget the worktrees whose directories were deleted.
    bare_repo("worktree", "prune")

    if cloned:
        return RepoResult(repo.name, Outcome.UPDATED, "cloned")
    if fetching_changed:
        return RepoResult(
            repo.name,
            Outcome.UPDATED,
            "now only fetches the branches you use and your own dev branches "
            "(run [b]otk mv cleanup[/b] to remove the others)",
        )
    return RepoResult(repo.name, Outcome.SKIPPED, "already cloned")


def clone_repo(
    repo: RepoSpec,
    repo_dir: Path,
    progress: CloneProgress | None = None,
    *,
    branch_suffix: str | None = None,
) -> RepoResult:
    """Clone a shared, pinned or standalone repository, and keep it on its default branch on GitHub.

    :param repo: The repository to clone.
    :param repo_dir: The directory to clone into.
    :param progress: Called with the progress of the clone.
    :param branch_suffix: The suffix of your own branches, which the `dev` remote fetches.
    :return: The result of the clone.
    """
    if not (repo_dir / ".git").exists():
        if repo_dir.exists() and any(repo_dir.iterdir()):
            return RepoResult(repo.name, Outcome.FAILED, f"[u]{repo_dir}[/u] exists but is not a Git repository")
        git_repo = clone(repo.url, repo_dir, progress=progress)
        _configure_remotes(git_repo, repo)
        configure_fetching(git_repo, branch_suffix=branch_suffix)
        return RepoResult(repo.name, Outcome.UPDATED, f"cloned on [b]{git_repo.current_branch()}[/b]")

    git_repo = Git(repo_dir)
    _configure_remotes(git_repo, repo)
    configure_fetching(git_repo, branch_suffix=branch_suffix)
    return follow_default_branch(git_repo, repo.name)


def _configure_remotes(repo: Git, spec: RepoSpec) -> None:
    """Add the `odoo-dev` fork as the `dev` remote, if the repository has one, and only push there."""
    if not spec.dev_remote:
        return
    if "dev" not in repo.remotes():
        repo("remote", "add", "dev", spec.dev_url())
    repo("remote", "set-url", "--push", "origin", "NO_PUSH_TRY_DEV_REPO")


def follow_default_branch(repo: Git, name: str) -> RepoResult:
    """Switch a shared, pinned or standalone repository to its default branch on GitHub, if that changed.

    It only switches when you're still on the previous default branch without uncommitted changes, so your own work is
    never touched.

    :param repo: The clone.
    :param name: The repository name, to report the result under.
    :return: The result.
    """
    previous = get_known_default_branch(repo)
    current = get_remote_default_branch(repo)
    branch = repo.current_branch()
    if current and current != previous:
        fetch_branch(repo, "origin", current)
        fetch_on_demand(repo, "origin", current)
        repo("remote", "set-head", "origin", current)
    if not current or previous in {None, current} or branch == current:
        return RepoResult(name, Outcome.SKIPPED, f"already cloned, on [b]{branch or 'a detached HEAD'}[/b]")
    if branch != previous:
        return RepoResult(
            name, Outcome.WARNING, f"the default branch is now [b]{current}[/b], but you're on [b]{branch}[/b]",
        )
    if repo.is_dirty():
        return RepoResult(
            name,
            Outcome.WARNING,
            f"the default branch is now [b]{current}[/b], but [b]{branch}[/b] has uncommitted changes, so it stays",
        )
    checkout_branch(repo, "origin", current)
    return RepoResult(name, Outcome.UPDATED, f"switched to the new default branch [b]{current}[/b]")


def _plan_links(
    multiverse_dir: Path,
    repos: list[RepoSpec],
    branches: list[str],
) -> tuple[dict[str, list[str]], list[RepoResult]]:
    """Decide which shared and pinned repositories to link into which version directories.

    Shared repositories go into every version. Pinned repositories go into the version they run on, and the ones they
    are configured to be in as well. Standalone repositories aren't linked anywhere.

    :return: The repository names to link per version, and the problems found.
    """
    links: dict[str, list[str]] = {branch: [] for branch in branches}
    problems: list[RepoResult] = []
    for repo in repos:
        if repo.kind == RepoKind.SHARED:
            for names in links.values():
                names.append(repo.name)
        elif repo.kind == RepoKind.PINNED:
            runs_on = get_runs_on(repo, multiverse_dir / repo.name, Git(multiverse_dir / repo.name).current_branch())
            if not runs_on:
                reason = (
                    "can't reach odoo.com to find out which Odoo version it runs on"
                    if repo.runs_on == ODOO_COM
                    else f"can't tell which Odoo version it runs on: set [b]runs_on[/b] in [b]{CONFIG_FILE}[/b]"
                )
                problems.append(RepoResult(repo.name, Outcome.WARNING, reason))
            elif runs_on not in links:
                problems.append(
                    RepoResult(
                        repo.name,
                        Outcome.WARNING,
                        f"runs on [b]{runs_on}[/b], which isn't set up: add [b]-b {runs_on}[/b] to use it",
                    ),
                )
            wanted = {version for version in (runs_on, *repo.also_in) if version}
            for version in wanted & set(links):
                links[version].append(repo.name)
            # The version it runs on changes over time, like when odoo.com moves to a new version.
            if runs_on and (stale := sorted(set(_get_versions_linking(multiverse_dir, repo.name)) - wanted)):
                problems.append(
                    RepoResult(
                        repo.name,
                        Outcome.WARNING,
                        f"now runs on [b]{runs_on}[/b], but is still linked into [b]{'[/b], [b]'.join(stale)}[/b]: run "
                        f"[b]otk mv unlink {repo.name}[/b] there if you don't need it anymore",
                    ),
                )
    return links, problems


def _get_versions_linking(multiverse_dir: Path, name: str) -> list[str]:
    """Get the version directories a repository is linked into."""
    return [path.name for path in multiverse_dir.iterdir() if (path / name).is_symlink()]


# Worktrees


def _add_worktrees(multiverse_dir: Path, branches: list[str], repos: list[RepoSpec]) -> list[RepoResult]:
    """Add a worktree for every version of every versioned repository.

    The repositories are handled in parallel, but the versions of one repository one after the other, since Git locks
    a repository while adding a worktree to it.
    """

    def add_for_repo(repo: RepoSpec, task: TaskID) -> list[RepoResult]:
        results: list[RepoResult] = []
        for branch in branches:
            if repo.versions is not None and branch not in repo.versions:
                progress.advance(task)
                continue
            results.append(
                run_safely(
                    f"{branch}/{repo.name}",
                    lambda branch=branch: add_worktree(
                        multiverse_dir / WORKTREE_SOURCE_DIR / repo.name / ".bare",
                        multiverse_dir / branch / repo.name,
                        branch,
                    ),
                ),
            )
            progress.advance(task)
        return results

    with ThreadPoolExecutor() as executor, TransientProgress() as progress:
        futures = [
            executor.submit(
                add_for_repo, repo, progress.add_task(f"Adding [b]{repo.name}[/b] worktrees", total=len(branches)),
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

    bare_repo = Git(bare_dir)
    if not remote_has_branch(bare_repo, "origin", branch):
        return RepoResult(name, Outcome.SKIPPED, f"no [b]{branch}[/b] branch in this repository")
    fetch_branch(bare_repo, "origin", branch)

    fetch_on_demand(bare_repo, "origin", branch)
    upstream = f"origin/{branch}"
    if bare_repo.has_branch(branch):
        # The bare clone created a local branch, which may be behind the remote one.
        bare_repo("worktree", "add", str(worktree_dir), branch)
        worktree_repo = Git(worktree_dir)
        worktree_repo("branch", "--set-upstream-to", upstream, branch)
        try:
            worktree_repo("merge", "--ff-only", upstream)
        except GitError:
            return RepoResult(name, Outcome.WARNING, f"added, but [b]{branch}[/b] has diverged from [b]{upstream}[/b]")
    else:
        bare_repo("worktree", "add", "--track", "-b", branch, str(worktree_dir), upstream)
    return RepoResult(name, Outcome.UPDATED, "added")


# Version configuration


def _configure_versions(
    multiverse_dir: Path,
    branches: list[str],
    *,
    links: dict[str, list[str]],
    reset_config: bool,
    vscode: bool,
) -> dict[str, list[RepoResult]]:
    """Configure every version directory in parallel.

    :param multiverse_dir: The multiverse directory.
    :param branches: The versions to configure.
    :param links: The shared and pinned repositories to link into each version directory.
    :param reset_config: Whether to recreate the Python environments and overwrite the configuration files.
    :param vscode: Whether to write the Visual Studio Code configuration.
    :return: The results per version.
    """
    versions = get_versions()

    def configure(branch: str, task: TaskID) -> list[RepoResult]:
        version_dir = multiverse_dir / branch
        if not version_dir.is_dir():
            return [RepoResult("version", Outcome.SKIPPED, "no repositories for this version")]
        steps: list[tuple[str, Callable[[], RepoResult]]] = [
            ("links", lambda: link_repos(version_dir, links.get(branch, []))),
            (
                "python",
                lambda: setup_python_env(
                    version_dir,
                    python=versions[branch].python if branch in versions else None,
                    requirements=[version_dir / path for path in VERSION_REQUIREMENTS]
                    # The requirements of the linked pinned repositories, like `odoofin`.
                    + [version_dir / name / "requirements.txt" for name in links.get(branch, [])]
                    + [multiverse_dir / "requirements.txt"],
                    reset=reset_config,
                ),
            ),
            ("javascript", lambda: setup_js_tooling(version_dir)),
        ]
        if vscode:
            steps.append(
                ("vscode", lambda: write_vscode_config(version_dir, reset=reset_config)),
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


def link_repos(version_dir: Path, names: list[str]) -> RepoResult:
    """Link shared and pinned repositories into a version directory.

    Existing links are kept, including the ones added with `otk mv link`.

    :param version_dir: The version directory.
    :param names: The repositories to link, which are cloned in the multiverse directory.
    :return: The result of linking.
    """
    linked: list[str] = []
    blocked: list[str] = []
    for name in dict.fromkeys(names):
        if link_repo(version_dir, name):
            linked.append(name)
        else:
            blocked.append(name)

    if blocked:
        return RepoResult(
            "links",
            Outcome.WARNING,
            f"[b]{'[/b], [b]'.join(blocked)}[/b] already exist as regular folders, so they aren't linked. "
            "Remove them to link them.",
        )
    if not linked:
        return RepoResult("links", Outcome.SKIPPED, "none")
    return RepoResult("links", Outcome.UPDATED, f"linked [b]{'[/b], [b]'.join(linked)}[/b]")


def link_repo(version_dir: Path, name: str) -> bool:
    """Link a repository cloned in the multiverse directory into a version directory, using a relative link.

    :return: Whether the repository is linked, which is not the case if a regular folder has its name.
    """
    link = version_dir / name
    target = Path("..") / name
    if link.is_symlink():
        if link.readlink() != target:
            link.unlink()
            link.symlink_to(target, target_is_directory=True)
        return True
    if link.exists():
        return False
    link.symlink_to(target, target_is_directory=True)
    return True


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
        except CalledProcessError:
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
        node_modules = ent_dir / "node_modules"
        if node_modules.is_dir() and not node_modules.is_symlink():
            # Earlier setups copied them.
            shutil.rmtree(node_modules)
        if not node_modules.is_symlink():
            node_modules.symlink_to(Path(os.path.relpath(com_dir, ent_dir)) / "node_modules", target_is_directory=True)
    return RepoResult("javascript", Outcome.UPDATED, "ESLint and type checking set up")


def write_vscode_config(version_dir: Path, *, reset: bool) -> RepoResult:
    """Write the Visual Studio Code configuration of a version directory.

    The configuration covers every repository linked into the version directory, including the ones linked by an
    earlier setup with other repositories selected, or with `otk mv link`.

    :param version_dir: The version directory.
    :param reset: Whether to overwrite existing configuration files.
    :return: The result of writing the configuration.
    """
    vscode_dir = version_dir / ".vscode"
    vscode_dir.mkdir(exist_ok=True)
    source_dir = MULTIVERSE_CONFIG_DIR / ".vscode"
    linked_repos = get_linked_repos(version_dir)

    settings = _read_json(source_dir / "settings.json")
    settings["python.defaultInterpreterPath"] = "${workspaceFolder}/.venv/bin/python"
    # Source Control doesn't look inside linked folders by itself.
    settings["git.scanRepositories"] = linked_repos

    # The debug configurations need no path mappings: `otk dev run` mounts the multiverse at the same path.
    files = {
        vscode_dir / "settings.json": settings,
        vscode_dir / "launch.json": _read_json(source_dir / "launch.json"),
        vscode_dir / "tasks.json": _read_json(source_dir / "tasks.json"),
        vscode_dir / "extensions.json": _read_json(source_dir / "extensions.json"),
    }
    written = [path.name for path, content in files.items() if _write_json(path, content, overwrite=reset)]
    # An existing settings file still needs the repositories linked since.
    update_scanned_repos(version_dir)

    if not written:
        return RepoResult("vscode", Outcome.SKIPPED, "already configured (use [b]--reset-config[/b] to overwrite)")
    return RepoResult("vscode", Outcome.UPDATED, f"wrote [b]{'[/b], [b]'.join(written)}[/b]")


def update_scanned_repos(version_dir: Path) -> None:
    """Make Visual Studio Code's Source Control show the repositories currently linked into a version directory.

    Only that setting is changed, so the rest of your settings stay as they are.
    """
    settings_file = version_dir / ".vscode" / "settings.json"
    if not settings_file.is_file():
        return
    try:
        settings = _read_json(settings_file)
    except json.JSONDecodeError:
        # Visual Studio Code allows comments in its settings, which Python can't read. Leave the file alone.
        return
    linked_repos = get_linked_repos(version_dir)
    if settings.get("git.scanRepositories") != linked_repos:
        settings["git.scanRepositories"] = linked_repos
        _write_json(settings_file, settings, overwrite=True)


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
