"""Decide which branches the bare repositories fetch, and clean up the ones they don't need.

Every remote fetches the branches your local branches track: the versions you set up, and the branches you switched
to, as long as they still exist on GitHub. On top of that, `origin` fetches its default branch, and `dev` fetches your
own branches (ending with your branch suffix).
Other branches, like the staging branches or other people's branches, are only fetched on demand.
"""

import contextlib
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from odoo_toolkit.git_cli import Git, GitError

from .repos import branch_refspec, get_known_default_branch

# Where the branch suffix given to `otk mv setup` is remembered, in each repository.
BRANCH_SUFFIX_SETTING = "otk.branchSuffix"


@dataclass
class CleanupPlan:
    """The references a repository doesn't need anymore."""

    # Local branches that aren't checked out and have no commits that only exist locally.
    local_branches: list[str] = field(default_factory=list[str])
    # Remote-tracking branches outside the fetched branches, like other people's `dev` branches.
    remote_refs: list[str] = field(default_factory=list[str])

    def __bool__(self) -> bool:
        """Check whether there is anything to clean up."""
        return bool(self.local_branches or self.remote_refs)


def get_branch_suffix() -> str | None:
    """Get the suffix of the user's own branches: the part of their Git email before the `@`."""
    try:
        email = Git(Path.home())("config", "--global", "user.email")
    except GitError:
        return None
    return email.split("@")[0].lower() or None


def resolve_branch_suffix(repo: Git, branch_suffix: str | None) -> str | None:
    """Get the branch suffix of a repository.

    That's the given one, which is then remembered, or else the remembered one, or else the one from your Git email.
    """
    if branch_suffix:
        repo("config", BRANCH_SUFFIX_SETTING, branch_suffix)
        return branch_suffix
    with contextlib.suppress(GitError):
        return repo("config", BRANCH_SUFFIX_SETTING)
    return get_branch_suffix()


def get_fetch_refspecs(repo: Git, remote: str) -> list[str]:
    """Get the refspecs a remote fetches."""
    try:
        return repo("config", "--get-all", f"remote.{remote}.fetch").splitlines()
    except GitError:
        # Git exits with an error when the setting doesn't exist.
        return []


def is_fetched(refspecs: list[str], branch: str) -> bool:
    """Check whether one of the refspecs fetches a branch."""
    return any(fnmatchcase(f"refs/heads/{branch}", spec.lstrip("+").split(":")[0]) for spec in refspecs)


def get_tracked_branches(repo: Git) -> dict[str, set[str]]:
    """Get the remote branches the local branches track, per remote.

    :return: The tracked branch names, like `{"origin": {"19.0", "master"}, "dev": {"19.0-fix-abc"}}`.
    """
    try:
        settings = repo("config", "--get-regexp", r"^branch\..*\.(remote|merge)$").splitlines()
    except GitError:
        # Git exits with an error when no setting matches.
        return {}
    remotes: dict[str, str] = {}
    merges: dict[str, str] = {}
    for line in settings:
        key, _, value = line.partition(" ")
        # Branch names can contain dots, like `branch.saas-19.2.remote`.
        branch, _, setting = key.removeprefix("branch.").rpartition(".")
        (remotes if setting == "remote" else merges)[branch] = value
    # Deleting a branch without Git's `branch` command leaves its settings behind.
    local_branches = set(repo("for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines())
    tracked: dict[str, set[str]] = {}
    for branch, remote in remotes.items():
        if (merge := merges.get(branch)) and branch in local_branches:
            tracked.setdefault(remote, set()).add(merge.removeprefix("refs/heads/"))
    return tracked


def get_existing_branches(repo: Git, remote: str, branches: set[str]) -> set[str]:
    """Get which of the given branches still exist on a remote, or all of them if the remote can't be reached."""
    if not branches:
        return branches
    try:
        output = repo("ls-remote", "--heads", remote, *(f"refs/heads/{branch}" for branch in sorted(branches)))
    except GitError:
        return branches
    return {line.partition("refs/heads/")[2] for line in output.splitlines()} & branches


def configure_fetching(repo: Git, *, branch_suffix: str | None) -> bool:
    """Configure which branches the remotes of a bare repository fetch, and Git's speed-ups for large repositories.

    Every remote fetches the branches the local branches track, as long as they exist on GitHub: fetching a branch
    that was deleted there fails the whole fetch. On top of that, `origin` fetches its default branch, and `dev`
    fetches your own branches.

    :param repo: The bare repository.
    :param branch_suffix: The suffix of your own branches on the `dev` remote, or None for the remembered one.
    :return: Whether the fetched branches changed.
    """
    changed = False
    branch_suffix = resolve_branch_suffix(repo, branch_suffix)
    tracked = get_tracked_branches(repo)
    for remote in repo.remotes():
        if remote == "origin":
            wanted = [branch_refspec(remote, get_known_default_branch(repo) or "master")]
        elif remote == "dev":
            wanted = [branch_refspec(remote, f"*-{branch_suffix}")] if branch_suffix else []
        else:
            continue
        extra = {branch for branch in tracked.get(remote, set()) if not is_fetched(wanted, branch)}
        wanted += [branch_refspec(remote, branch) for branch in sorted(get_existing_branches(repo, remote, extra))]
        if wanted != get_fetch_refspecs(repo, remote):
            set_fetch_refspecs(repo, remote, wanted)
            changed = True

    for key, value in (("fetch.prune", "true"), ("core.fsmonitor", "true"), ("core.untrackedCache", "true")):
        repo("config", key, value)
    return changed


def set_fetch_refspecs(repo: Git, remote: str, refspecs: list[str]) -> None:
    """Replace the refspecs a remote fetches."""
    # Git exits with an error when the setting doesn't exist.
    with contextlib.suppress(GitError):
        repo("config", "--unset-all", f"remote.{remote}.fetch")
    for refspec in refspecs:
        repo("config", "--add", f"remote.{remote}.fetch", refspec)


def refresh_fetching(repo: Git) -> None:
    """Update which branches the repository of a worktree or clone fetches, like after switching branches.

    The settings are shared by all worktrees, and live in the bare repository.
    """
    common_dir = Path(repo("rev-parse", "--path-format=absolute", "--git-common-dir"))
    configure_fetching(Git(common_dir), branch_suffix=None)


def fetch_on_demand(repo: Git, remote: str, branch: str) -> None:
    """Make a remote fetch a branch it doesn't fetch yet, so a local branch can track it.

    Git needs a remote to fetch a branch to show how far ahead or behind your local branch is.
    """
    refspecs = get_fetch_refspecs(repo, remote)
    if not is_fetched(refspecs, branch):
        repo("config", "--add", f"remote.{remote}.fetch", branch_refspec(remote, branch))


def plan_cleanup(repo: Git) -> CleanupPlan:
    """Find the references a bare repository doesn't need anymore.

    Only references that don't hold any work are listed: local branches that aren't checked out in a worktree and
    whose commits all exist on a remote, and remote-tracking branches the remotes don't fetch anymore.

    :param repo: The bare repository.
    :return: The references to delete.
    """
    plan = CleanupPlan()
    fetch_refspecs = {remote: get_fetch_refspecs(repo, remote) for remote in repo.remotes()}
    kept_remote_refs: list[str] = []
    for ref in repo("for-each-ref", "--format=%(refname)", "refs/remotes").splitlines():
        remote, _, branch = ref.removeprefix("refs/remotes/").partition("/")
        if branch != "HEAD" and remote in fetch_refspecs and not is_fetched(fetch_refspecs[remote], branch):
            plan.remote_refs.append(ref)
        else:
            kept_remote_refs.append(ref)

    checked_out = {
        line.removeprefix("branch refs/heads/")
        for line in repo("worktree", "list", "--porcelain").splitlines()
        if line.startswith("branch refs/heads/")
    }
    # The commits reachable from a local branch, but from none of the remote-tracking branches that are kept. Since a
    # commit's ancestors are reachable from wherever the commit is, a branch only holds local work if its tip is one of
    # these commits.
    revisions = "".join(f"^{ref}\n" for ref in kept_remote_refs)
    local_only = set(repo("rev-list", "--stdin", "--branches", stdin=revisions).split())
    default_branch = repo("symbolic-ref", "--short", "HEAD")

    for line in repo("for-each-ref", "--format=%(objectname) %(refname:short)", "refs/heads").splitlines():
        commit, branch = line.split(" ", 1)
        if branch not in checked_out and branch != default_branch and commit not in local_only:
            plan.local_branches.append(branch)
    return plan


def apply_cleanup(repo: Git, plan: CleanupPlan, *, branch_suffix: str | None) -> None:
    """Delete the references of a cleanup plan, and stop fetching the branches that were deleted.

    The disk space is only reclaimed by a garbage collection afterwards.
    """
    # Git's `branch` command also removes the branches' settings, so they stop being fetched.
    if plan.local_branches:
        repo("branch", "--delete", "--force", *plan.local_branches)
    if plan.remote_refs:
        repo("update-ref", "--stdin", stdin="".join(f"delete {ref}\n" for ref in plan.remote_refs))
    configure_fetching(repo, branch_suffix=branch_suffix)
