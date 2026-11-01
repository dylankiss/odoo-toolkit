"""Clone the repositories of a multiverse, complete partial clones, and keep them on their default branch."""

import contextlib
from pathlib import Path

from odoo_toolkit.git_cli import CloneProgress, Git, GitError, clone, get_git_version, run_with_progress

from .config import RepoSpec
from .remotes import configure_fetching, fetch_on_demand
from .repos import (
    Outcome,
    RepoResult,
    checkout_branch,
    fetch_branch,
    get_known_default_branch,
    get_remote_default_branch,
)

# The first version of Git having `git backfill`, which downloads only what a partial clone is missing.
BACKFILL_GIT_VERSION = (2, 49)


def clone_bare_repo(
    repo: RepoSpec,
    repo_dir: Path,
    progress: CloneProgress | None = None,
    *,
    branch_suffix: str | None = None,
    full_history: bool = False,
) -> RepoResult:
    """Clone a versioned repository as a bare repository to add worktrees from, and configure its remotes.

    The clone is partial, unless you want the full history: file contents are only downloaded when a worktree needs
    them, which saves gigabytes, but `git blame` then needs GitHub. Only the default branch is cloned; the version
    branches are fetched when adding their worktrees.

    :param repo: The repository to clone.
    :param repo_dir: The directory to clone into, which gets the bare repository in its `.bare` subdirectory.
    :param progress: Called with the progress of the clone.
    :param branch_suffix: The suffix of your own branches, which the `dev` remote fetches.
    :param full_history: Whether to download the file contents of the whole history, completing a partial clone.
    :return: The result of the clone.
    """
    bare_dir = repo_dir / ".bare"
    cloned = not bare_dir.exists()
    completed = False
    if cloned:
        filter_args = [] if full_history else ["--filter=blob:none"]
        bare_repo = clone(repo.url, bare_dir, "--bare", "--single-branch", *filter_args, progress=progress)
    else:
        bare_repo = Git(bare_dir)

    _configure_remotes(bare_repo, repo)
    fetching_changed = configure_fetching(bare_repo, branch_suffix=branch_suffix)
    # Allow running Git commands from the repository directory itself.
    if not (repo_dir / ".git").exists():
        (repo_dir / ".git").write_text("gitdir: ./.bare\n", encoding="utf-8")
    # Forget the worktrees whose directories were deleted.
    bare_repo("worktree", "prune")

    if full_history and not cloned:
        completed = complete_clone(bare_repo, progress)

    if cloned:
        return RepoResult(repo.name, Outcome.UPDATED, "cloned with its full history" if full_history else "cloned")
    if completed:
        return RepoResult(repo.name, Outcome.UPDATED, "downloaded its full history")
    if fetching_changed:
        return RepoResult(
            repo.name,
            Outcome.UPDATED,
            "now only fetches the branches you use and your own dev branches "
            "(run [b]otk mv cleanup[/b] to remove the others)",
        )
    return RepoResult(repo.name, Outcome.SKIPPED, "already cloned")


def complete_clone(repo: Git, progress: CloneProgress | None = None) -> bool:
    """Download everything a partial clone left out, and make it a regular repository.

    :param repo: The bare repository.
    :param progress: Called with the progress of the download.
    :raises GitError: If downloading the file contents failed.
    :return: Whether the repository was partial.
    """
    try:
        promisor_keys = [
            line.split()[0] for line in repo("config", "--get-regexp", r"^remote\..*\.promisor$").splitlines()
        ]
    except GitError:
        return False
    remotes = [key.removeprefix("remote.").removesuffix(".promisor") for key in promisor_keys]
    if not _backfill(repo, progress):
        for remote in remotes:
            run_with_progress(["fetch", "--refetch", "--no-filter", "--progress", remote], progress, cwd=repo.path)
    for key in (
        *promisor_keys,
        *(f"remote.{remote}.partialclonefilter" for remote in remotes),
        "extensions.partialclone",
    ):
        # Git exits with an error when the setting doesn't exist.
        with contextlib.suppress(GitError):
            repo("config", "--unset", key)
    return bool(remotes)


def _backfill(repo: Git, progress: CloneProgress | None) -> bool:
    """Download only the missing file contents of a partial clone, which is much less than fetching everything again.

    :return: Whether it worked, which needs Git 2.49 or newer.
    """
    if get_git_version() < BACKFILL_GIT_VERSION:
        return False
    batch = 1
    received = 0

    def report_batches(stage: str, current: int, total: int) -> None:
        # Every batch goes through all the stages of a download again, so only follow the one that takes the time.
        nonlocal batch, received
        if not progress or stage != "Receiving objects":
            return
        if current < received:
            batch += 1
        received = current
        progress(f"Downloading file contents, batch {batch}", current, total)

    try:
        # It only prints its progress to a terminal.
        run_with_progress(["backfill", "--no-sparse", "--all"], report_batches, cwd=repo.path, tty=True)
    except GitError:
        return False
    return True


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
        if repo_dir.is_symlink() and not repo_dir.exists():
            # The link to a pinned repository whose version directory was deleted.
            repo_dir.unlink()
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
            name,
            Outcome.WARNING,
            f"the default branch is now [b]{current}[/b], but you're on [b]{branch}[/b]",
        )
    if repo.is_dirty():
        return RepoResult(
            name,
            Outcome.WARNING,
            f"the default branch is now [b]{current}[/b], but [b]{branch}[/b] has uncommitted changes, so it stays",
        )
    checkout_branch(repo, "origin", current)
    return RepoResult(name, Outcome.UPDATED, f"switched to the new default branch [b]{current}[/b]")


def write_commit_graph(repo: Git, name: str) -> RepoResult:
    """Write the commit graph of a repository, including the paths every commit changed.

    Git walks the history much faster with it, which speeds up `git blame`, `git log` on a file, and the file history
    and blame annotations of editors. New commits are added to the existing graph, so running it again is quick.

    :param repo: The repository, or bare repository.
    :param name: The repository name, to report the result under.
    :return: The result, a warning if the graph couldn't be written, since Git works without it.
    """
    chain_file = repo.path / repo("rev-parse", "--git-path", "objects/info/commit-graphs/commit-graph-chain")
    before = chain_file.read_text(encoding="utf-8") if chain_file.is_file() else None
    try:
        repo("commit-graph", "write", "--reachable", "--changed-paths", "--split")
    except GitError as e:
        return RepoResult(name, Outcome.WARNING, f"couldn't write the commit graph: {e.message}")
    after = chain_file.read_text(encoding="utf-8") if chain_file.is_file() else None
    if after == before:
        return RepoResult(name, Outcome.SKIPPED, "commit graph is up to date")
    return RepoResult(name, Outcome.UPDATED, "commit graph written" if before is None else "commit graph updated")
