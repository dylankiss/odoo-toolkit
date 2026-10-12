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
    # A local branch tracking someone else's `dev` branch.
    bare("branch", "kept", "master")
    bare("config", "branch.kept.remote", "dev")
    bare("config", "branch.kept.merge", "refs/heads/feature/kept")

    assert configure_fetching(bare, branch_suffix="abcd")

    # `master`, and the versions with a worktree tracking them.
    assert get_fetch_refspecs(bare, "origin") == [
        "+refs/heads/master:refs/remotes/origin/master",
        "+refs/heads/19.0:refs/remotes/origin/19.0",
    ]
    # Your own branches, and the branches local branches track.
    assert get_fetch_refspecs(bare, "dev") == [
        "+refs/heads/*-abcd:refs/remotes/dev/*-abcd",
        "+refs/heads/feature/kept:refs/remotes/dev/feature/kept",
    ]
    assert bare("config", "core.fsmonitor") == "true"
    assert bare("config", "fetch.prune") == "true"
    # Running it again changes nothing.
    assert not configure_fetching(bare, branch_suffix="abcd")


def test_switch_to_other_dev_branch_keeps_fetching_it(multiverse: Path, bare: Git) -> None:
    worktree = multiverse / "19.0" / "odoo"
    assert "+refs/heads/19.0-fix-abc:refs/remotes/dev/19.0-fix-abc" not in get_fetch_refspecs(bare, "dev")

    switch_repo(worktree, branch="19.0-fix-abc", remote=None, version="19.0")

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
