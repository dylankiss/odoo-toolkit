import contextlib
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

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
from odoo_toolkit.git_cli import Git, GitError
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
    get_runs_on,
    load_config,
    resolve_repo,
)
from .remotes import fetch_on_demand, get_branch_suffix
from .repos import Outcome, RepoResult, fetch_branch, print_result_table, remote_has_branch, run_on_repos, run_safely

if TYPE_CHECKING:
    from collections.abc import Callable
from .clone import clone_bare_repo, clone_repo, write_commit_graph
from .configure import (
    COMPAT_REQUIREMENTS_FILE,
    VERSION_REQUIREMENTS,
    configure_language_server,
    link_repos,
    link_skills,
    place_pinned_repo,
    print_devcontainer_notes,
    setup_js_tooling,
    setup_python_env,
    write_devcontainer_config,
    write_root_config,
    write_ruff_config,
    write_vscode_config,
)

app = Typer()


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
            f"Defaults to the ones already set up, or {', '.join(f'`{r}`' for r in DEFAULT_REPOS[:-1])} and "
            f"`{DEFAULT_REPOS[-1]}` for a "
            f"new multiverse, plus the ones in `{CONFIG_FILE}`. A repository the toolkit doesn't know, like the one "
            "above, is only set up again later if it has a `[repos.<name>]` table there.",
        ),
    ] = EMPTY_LIST,
    multiverse_dir: Annotated[
        Path | None,
        Option("--multiverse-dir", "-d", help="The directory to set up the multiverse in. Defaults to this directory."),
    ] = None,
    update_config: Annotated[
        bool,
        Option(
            "--update-config",
            help="Overwrite the configuration files of every version, like the Visual Studio Code and dev container "
            "ones, to get the latest from the toolkit. The Python virtual environments are kept.",
        ),
    ] = False,
    reset_config: Annotated[
        bool,
        Option(
            "--reset-config",
            help="Recreate the Python virtual environments as well as overwriting the configuration files of every "
            "version.",
        ),
    ] = False,
    vscode: Annotated[
        bool,
        Option("--vscode/--no-vscode", help="Write the Visual Studio Code configuration in each version directory."),
    ] = True,
    devcontainer: Annotated[
        bool | None,
        Option(
            "--devcontainer/--no-devcontainer",
            help="Write a dev container configuration in each version directory, to open it in Visual Studio Code in a "
            "container that only sees your multiverse, for example to keep AI agents away from the rest of your "
            "machine. Versions that already have one keep it up to date, unless you pass `--no-devcontainer`.",
        ),
    ] = None,
    branch_suffix: Annotated[
        str | None,
        Option(
            "--branch-suffix",
            help="The suffix of your own branches on the `dev` remote, like `abcd` for `master-fix-xyz-abcd`. It's "
            "remembered for later commands. Defaults to the remembered one, or else the part of your Git email before "
            "the `@`.",
        ),
    ] = None,
    full_history: Annotated[
        bool,
        Option(
            "--full-history",
            help="Download the file contents of the whole history of the versioned repositories, so `git blame` works "
            "offline, like in a dev container. It needs several gigabytes more disk space and time than the default "
            "partial clones. Repositories that are partial already are completed, which only downloads what they miss with Git "
            "2.49 or newer, and everything again with older versions.",
        ),
    ] = False,
) -> None:
    """Set up an :ringed_planet: Odoo Multiverse environment, having different versions checked out at the same time.

    Every version gets its own directory (like `19.0`), which is a complete workspace you can open in your editor. It
    contains:\n
    - a [`git worktree`](https://git-scm.com/docs/git-worktree) of every versioned repository (like `odoo` and
    `enterprise`), sharing the Git history with the other versions,\n
    - a link to every shared repository (like `upgrade`), which all versions use,\n
    - the pinned repositories that run on this version (like `odoofin`), linked from the multiverse directory,\n
    - a Python virtual environment with the same Python version as Runbot and the version's dependencies,\n
    - the JavaScript tooling, and the Visual Studio Code configuration, where `F5` runs Odoo with the debugger.\n
    \n
    With `--devcontainer`, every version also gets a [dev container](https://containers.dev) configuration. Opening a
    version in it only gives Visual Studio Code, and any AI agent in it, access to your multiverse directory: not your
    SSH keys, Git credentials or the rest of your files.\n
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
    You can run the command as many times as you want. It only adds what's missing, unless you pass `--update-config`
    to overwrite the configuration files, or `--reset-config` to recreate the Python environments as well. Shared,
    pinned and standalone repositories follow their default branch on GitHub.
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
    overwrite_config = update_config or reset_config
    write_root_config(multiverse_dir, reset_config=overwrite_config)

    # The suffix is only passed on when given, so the one a repository remembers isn't replaced by the default.
    if not branch_suffix and not get_branch_suffix():
        print_warning("No Git email configured, so the [b]dev[/b] remotes won't fetch any of your branches.\n")

    print_header(":honey_pot: Clone Repositories")
    clone_results = _clone_repos(multiverse_dir, repos, branch_suffix=branch_suffix, full_history=full_history)
    cloned = [repo for repo in repos if repo.name in {r.name for r in clone_results if r.outcome != Outcome.FAILED}]
    links, link_results = _plan_links(multiverse_dir, cloned, branches)
    print_result_table(clone_results + link_results)

    print_header(":deciduous_tree: Add Worktrees")
    worktree_results = _add_worktrees(multiverse_dir, branches, [r for r in versioned_repos if r in cloned])
    print_result_table(worktree_results)

    print_header(":zap: Speed Up Git History")
    commit_graph_results = _write_commit_graphs(multiverse_dir, cloned)
    print_result_table(commit_graph_results)

    print_header(":gear: Configure Versions")
    version_results = _configure_versions(
        multiverse_dir,
        branches,
        links=links,
        reset_config=reset_config,
        overwrite_config=overwrite_config,
        vscode=vscode,
        devcontainer=devcontainer,
    )
    for branch, results in version_results.items():
        print(f"[b]{branch}[/b]")
        print_result_table(results)
    if any(result.name == "devcontainer" for results in version_results.values() for result in results):
        print_devcontainer_notes()

    all_results = [
        *clone_results,
        *link_results,
        *worktree_results,
        *commit_graph_results,
        *(r for rs in version_results.values() for r in rs),
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


def _clone_repos(
    multiverse_dir: Path,
    repos: list[RepoSpec],
    *,
    branch_suffix: str | None,
    full_history: bool,
) -> list[RepoResult]:
    """Clone the versioned repositories as bare repositories, and the other ones as regular ones."""
    # Like `preauthenticate_ssh`: an SSH agent that confirms each session gets one request, instead of a race of them.
    if repos:
        with contextlib.suppress(GitError):
            Git(multiverse_dir)("ls-remote", repos[0].url, "HEAD")
    with ThreadPoolExecutor() as executor, TransientProgress() as progress:

        def clone_one(repo: RepoSpec) -> RepoResult:
            task = progress.add_task(f"Cloning [b]{repo.name}[/b]", total=None)

            def update_progress(stage: str, current: int, total: int) -> None:
                progress.update(
                    task,
                    description=f"Cloning [b]{repo.name}[/b]: {stage.lower()}",
                    completed=current,
                    total=total,
                )

            if repo.kind == RepoKind.VERSIONED:
                return clone_bare_repo(
                    repo,
                    multiverse_dir / WORKTREE_SOURCE_DIR / repo.name,
                    update_progress,
                    branch_suffix=branch_suffix,
                    full_history=full_history,
                )
            return clone_repo(repo, multiverse_dir / repo.name, update_progress, branch_suffix=branch_suffix)

        futures = [executor.submit(run_safely, repo.name, lambda repo=repo: clone_one(repo)) for repo in repos]
        return [future.result() for future in futures]


def _plan_links(
    multiverse_dir: Path,
    repos: list[RepoSpec],
    branches: list[str],
) -> tuple[dict[str, list[str]], list[RepoResult]]:
    """Decide which shared and pinned repositories to link into which version directories.

    Shared repositories go into every version. Pinned repositories move into the version they run on, and are linked
    into the ones they are configured to be in as well. Standalone repositories aren't linked anywhere.

    :return: The repository names to link per version, and the moves and problems.
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
            else:
                placed = run_safely(
                    repo.name,
                    lambda name=repo.name, version=runs_on: place_pinned_repo(multiverse_dir, name, version),
                )
                if placed.outcome != Outcome.SKIPPED:
                    problems.append(placed)
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
                add_for_repo,
                repo,
                progress.add_task(f"Adding [b]{repo.name}[/b] worktrees", total=len(branches)),
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


def _write_commit_graphs(multiverse_dir: Path, repos: list[RepoSpec]) -> list[RepoResult]:
    """Write the commit graph of every repository, in parallel."""
    # The worktrees of a versioned repository share the history of its bare repository.
    names = {
        (
            multiverse_dir / WORKTREE_SOURCE_DIR / repo.name / ".bare"
            if repo.kind == RepoKind.VERSIONED
            else multiverse_dir / repo.name
        ): repo.name
        for repo in repos
    }
    return run_on_repos(
        list(names),
        lambda repo_dir: write_commit_graph(Git(repo_dir), names[repo_dir]),
        "Writing commit graphs, which takes a while the first time",
        names=names,
    )


def _configure_versions(
    multiverse_dir: Path,
    branches: list[str],
    *,
    links: dict[str, list[str]],
    reset_config: bool,
    overwrite_config: bool,
    vscode: bool,
    devcontainer: bool | None,
) -> dict[str, list[RepoResult]]:
    """Configure every version directory in parallel.

    :param multiverse_dir: The multiverse directory.
    :param branches: The versions to configure.
    :param links: The shared and pinned repositories to link into each version directory.
    :param reset_config: Whether to recreate the Python environments.
    :param overwrite_config: Whether to overwrite the configuration files.
    :param vscode: Whether to write the Visual Studio Code configuration.
    :param devcontainer: Whether to write the dev container configuration, or None for the versions that have one.
    :return: The results per version.
    """
    versions = get_versions()

    def has_devcontainer(branch: str) -> bool:
        if devcontainer is not None:
            return devcontainer
        return (multiverse_dir / branch / ".devcontainer" / "devcontainer.json").is_file()

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
                    + [COMPAT_REQUIREMENTS_FILE, multiverse_dir / "requirements.txt"],
                    reset=reset_config,
                ),
            ),
            ("javascript", lambda: setup_js_tooling(version_dir)),
            ("language server", lambda: configure_language_server(version_dir)),
            ("ruff", lambda: write_ruff_config(version_dir, reset=overwrite_config)),
            ("skills", lambda: link_skills(version_dir)),
        ]
        if vscode:
            steps.append(
                ("vscode", lambda: write_vscode_config(version_dir, reset=overwrite_config)),
            )
        if has_devcontainer(branch):
            steps.append(
                ("devcontainer", lambda: write_devcontainer_config(version_dir, reset=overwrite_config)),
            )
        progress.update(task, total=len(steps))
        results: list[RepoResult] = []
        for name, step in steps:
            progress.update(task, description=f"Configuring [b]{branch}[/b]: {name}")
            results.append(run_safely(name, step))
            progress.advance(task)
        return results

    with ThreadPoolExecutor() as executor, TransientProgress() as progress:
        futures = {
            branch: executor.submit(
                configure,
                branch,
                progress.add_task(f"Configuring [b]{branch}[/b]", total=None),
            )
            for branch in branches
        }
        return {branch: future.result() for branch, future in futures.items()}
