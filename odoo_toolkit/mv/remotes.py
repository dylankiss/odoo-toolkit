"""Decide which branches the bare repositories fetch, and clean up the ones they don't need.

Every remote fetches the branches your local branches track: the versions you set up, and the branches you switched
to. On top of that, `origin` fetches its default branch, and `dev` fetches your own branches (ending with your branch
suffix).
Other branches, like the staging branches or other people's branches, are only fetched on demand.
"""

import contextlib
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from odoo_toolkit.git_cli import Git, GitError


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


def branch_refspec(remote: str, branch: str) -> str:
    """Get the refspec that fetches one branch (or branch pattern) of a remote into its remote-tracking branch."""
    return f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"


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
    tracked: dict[str, set[str]] = {}
    for branch, remote in remotes.items():
        if merge := merges.get(branch):
            tracked.setdefault(remote, set()).add(merge.removeprefix("refs/heads/"))
    return tracked


def configure_fetching(repo: Git, *, branch_suffix: str | None) -> bool:
    """Configure which branches the remotes of a bare repository fetch, and Git's speed-ups for large repositories.

    Every remote fetches the branches the local branches track. On top of that, `origin` fetches its default branch,
    and `dev` fetches your own branches.

    :param repo: The bare repository.
    :param branch_suffix: The suffix of your own branches on the `dev` remote, or None to fetch none of them.
    :return: Whether the fetched branches changed.
    """
    changed = False
    tracked = get_tracked_branches(repo)
    for remote in repo.remotes():
        if remote == "origin":
            wanted = [branch_refspec(remote, get_origin_default_branch(repo))]
        elif remote == "dev":
            wanted = [branch_refspec(remote, f"*-{branch_suffix}")] if branch_suffix else []
        else:
            continue
        wanted += [
            branch_refspec(remote, branch) for branch in sorted(tracked.get(remote, ())) if not is_fetched(wanted, branch)
        ]
        if wanted != get_fetch_refspecs(repo, remote):
            set_fetch_refspecs(repo, remote, wanted)
            changed = True

    for key, value in (("fetch.prune", "true"), ("core.fsmonitor", "true"), ("core.untrackedCache", "true")):
        repo("config", key, value)
    return changed


def get_origin_default_branch(repo: Git) -> str:
    """Get the default branch of `origin` as the repository knows it, without asking GitHub."""
    with contextlib.suppress(GitError):
        return repo("symbolic-ref", "--short", "refs/remotes/origin/HEAD").removeprefix("origin/")
    # A bare clone has no remote-tracking branches, but its own HEAD is the default branch.
    if repo("rev-parse", "--is-bare-repository") == "true":
        with contextlib.suppress(GitError):
            return repo("symbolic-ref", "--short", "HEAD")
    return "master"


def set_fetch_refspecs(repo: Git, remote: str, refspecs: list[str]) -> None:
    """Replace the refspecs a remote fetches."""
    # Git exits with an error when the setting doesn't exist.
    with contextlib.suppress(GitError):
        repo("config", "--unset-all", f"remote.{remote}.fetch")
    for refspec in refspecs:
        repo("config", "--add", f"remote.{remote}.fetch", refspec)


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
    checked_out = {
        line.removeprefix("branch refs/heads/")
        for line in repo("worktree", "list", "--porcelain").splitlines()
        if line.startswith("branch refs/heads/")
    }
    # The commits reachable from a local branch, but from no remote-tracking branch. Since a commit's ancestors are
    # reachable from wherever the commit is, a branch only holds local work if its tip is one of these commits.
    local_only = set(repo("rev-list", "--branches", "--not", "--remotes").split())
    default_branch = repo("symbolic-ref", "--short", "HEAD")

    plan = CleanupPlan()
    for line in repo("for-each-ref", "--format=%(objectname) %(refname:short)", "refs/heads").splitlines():
        commit, branch = line.split(" ", 1)
        if branch not in checked_out and branch != default_branch and commit not in local_only:
            plan.local_branches.append(branch)

    fetch_refspecs = {remote: get_fetch_refspecs(repo, remote) for remote in repo.remotes()}
    for ref in repo("for-each-ref", "--format=%(refname)", "refs/remotes").splitlines():
        remote, _, branch = ref.removeprefix("refs/remotes/").partition("/")
        if branch != "HEAD" and remote in fetch_refspecs and not is_fetched(fetch_refspecs[remote], branch):
            plan.remote_refs.append(ref)
    return plan


def apply_cleanup(repo: Git, plan: CleanupPlan, *, branch_suffix: str | None) -> None:
    """Delete the references of a cleanup plan, and stop fetching the branches that were deleted.

    The disk space is only reclaimed by a garbage collection afterwards.
    """
    refs = [f"refs/heads/{branch}" for branch in plan.local_branches] + plan.remote_refs
    if refs:
        repo("update-ref", "--stdin", stdin="".join(f"delete {ref}\n" for ref in refs))
    configure_fetching(repo, branch_suffix=branch_suffix)
