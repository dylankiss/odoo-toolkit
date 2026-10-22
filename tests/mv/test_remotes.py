from pathlib import Path

import pytest
from typer.testing import CliRunner

from odoo_toolkit.git_cli import Git
from odoo_toolkit.main import app
from odoo_toolkit.mv.remotes import apply_cleanup, configure_fetching, get_fetch_refspecs, plan_cleanup
from odoo_toolkit.mv.switch import switch_repo
from tests.mv.conftest import commit, git


@pytest.fixture
def bare(multiverse: Path) -> Git:
    return Git(multiverse / ".worktree-source" / "odoo" / ".bare")


def test_configure_fetching(bare: Git) -> None:
    # Start from the old configuration, fetching everything.
    bare("config", "--replace-all", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    bare("config", "--replace-all", "remote.dev.fetch", "+refs/heads/*:refs/remotes/dev/*")
    bare("config", "--add", "remote.dev.fetch", "+refs/heads/gone:refs/remotes/dev/gone")
    # A local branch tracking someone else's `dev` branch, and one tracking a branch that was deleted on GitHub.
    for branch, upstream in (("kept", "fix/master"), ("deleted", "fix/deleted")):
        bare("branch", branch, "master")
        bare("config", f"branch.{branch}.remote", "dev")
        bare("config", f"branch.{branch}.merge", f"refs/heads/{upstream}")
    # The settings of a branch deleted without Git's `branch` command.
    bare("config", "branch.removed.remote", "dev")
    bare("config", "branch.removed.merge", "refs/heads/19.0-fix-abc")

    assert configure_fetching(bare, branch_suffix="abcd")

    # `master`, and the versions with a worktree tracking them.
    assert get_fetch_refspecs(bare, "origin") == [
        "+refs/heads/master:refs/remotes/origin/master",
        "+refs/heads/19.0:refs/remotes/origin/19.0",
    ]
    # Your own branches, and the branches local branches track.
    assert get_fetch_refspecs(bare, "dev") == [
        "+refs/heads/*-abcd:refs/remotes/dev/*-abcd",
        "+refs/heads/fix/master:refs/remotes/dev/fix/master",
    ]
    assert bare("config", "core.fsmonitor") == "true"
    assert bare("config", "fetch.prune") == "true"
    # Running it again changes nothing.
    assert not configure_fetching(bare, branch_suffix="abcd")


def test_configure_fetching_without_dev_remote(bare: Git) -> None:
    # Without an `odoo-dev` fork, like for `i18n-apps`, your own branches are on `origin`. So they are fetched
    # however they were created, even with a plain `git push`.
    bare("remote", "remove", "dev")

    assert configure_fetching(bare, branch_suffix="abcd")

    assert get_fetch_refspecs(bare, "origin") == [
        "+refs/heads/master:refs/remotes/origin/master",
        "+refs/heads/*-abcd:refs/remotes/origin/*-abcd",
        "+refs/heads/19.0:refs/remotes/origin/19.0",
    ]


def test_switch_to_other_dev_branch_keeps_fetching_it(multiverse: Path, bare: Git) -> None:
    worktree = multiverse / "19.0" / "odoo"
    assert "+refs/heads/19.0-fix-abc:refs/remotes/dev/19.0-fix-abc" not in get_fetch_refspecs(bare, "dev")

    switch_repo(worktree, branch="19.0-fix-abc", version="19.0")

    assert "+refs/heads/19.0-fix-abc:refs/remotes/dev/19.0-fix-abc" in get_fetch_refspecs(bare, "dev")
    commit(worktree, "work.txt")
    assert git(worktree, "status", "--short", "--branch").startswith("## 19.0-fix-abc...dev/19.0-fix-abc [ahead 1]")


def test_plan_cleanup_only_removes_what_holds_no_work(multiverse: Path, bare: Git) -> None:
    # A copy of a remote branch, like the ones a bare clone creates.
    bare("branch", "mirror", "origin/19.0")
    # A branch with a commit that only exists locally.
    worktree = multiverse / "19.0" / "odoo"
    git(worktree, "checkout", "-q", "-b", "local-work")
    commit(worktree, "work.txt")
    git(worktree, "checkout", "-q", "19.0")
    # Someone else's branch, fetched when everything was fetched.
    bare("fetch", "-q", "dev", "+refs/heads/fix/master:refs/remotes/dev/fix/master")

    plan = plan_cleanup(bare)

    # `master` and `19.0` are checked out, and `local-work` holds local commits.
    assert plan.local_branches == ["mirror"]
    assert plan.remote_refs == ["refs/remotes/dev/fix/master"]

    apply_cleanup(bare, plan, branch_suffix="test")

    assert not bare.has_branch("mirror")
    assert bare.has_branch("local-work")
    assert "refs/remotes/dev/19.0-mine-test" in bare("for-each-ref", "--format=%(refname)", "refs/remotes/dev")
    assert "fix/master" not in bare("for-each-ref", "--format=%(refname)", "refs/remotes/dev")


def test_cli_cleanup(multiverse: Path, bare: Git, monkeypatch: pytest.MonkeyPatch) -> None:
    bare("branch", "mirror", "origin/19.0")
    monkeypatch.chdir(multiverse / "19.0")

    result = CliRunner().invoke(app, ["mv", "cleanup", "--yes", "--branch-suffix", "test"])

    assert result.exit_code == 0, result.output
    assert "removed 1 branches" in result.output
    assert not bare.has_branch("mirror")
    assert "Nothing to clean up" in CliRunner().invoke(app, ["mv", "cleanup", "--yes"]).output


def test_cli_cleanup_outside_multiverse_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert CliRunner().invoke(app, ["mv", "cleanup", "--yes"]).exit_code == 1


def test_cleanup_forgets_the_branches_it_deletes(bare: Git) -> None:
    bare("branch", "mirror", "origin/19.0")
    bare("config", "branch.mirror.remote", "dev")
    bare("config", "branch.mirror.merge", "refs/heads/fix/master")

    apply_cleanup(bare, plan_cleanup(bare), branch_suffix="test")

    assert not bare.has_branch("mirror")
    assert "mirror" not in bare("config", "--list")
    assert "fix/master" not in "\n".join(get_fetch_refspecs(bare, "dev"))


def test_cleanup_keeps_branches_only_pushed_to_branches_it_removes(multiverse: Path, bare: Git) -> None:
    worktree = multiverse / "19.0" / "odoo"
    git(worktree, "checkout", "-q", "-b", "pushed-once")
    work = commit(worktree, "work.txt")
    git(worktree, "checkout", "-q", "19.0")
    # A copy fetched when everything was fetched, of a branch deleted on GitHub since.
    bare("update-ref", "refs/remotes/dev/pushed-once", work)

    plan = plan_cleanup(bare)

    assert plan.remote_refs == ["refs/remotes/dev/pushed-once"]
    assert "pushed-once" not in plan.local_branches
