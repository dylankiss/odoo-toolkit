import subprocess
from pathlib import Path

import pytest
from git import Repo
from typer.testing import CliRunner

from odoo_toolkit.main import app
from odoo_toolkit.mv.repos import (
    Outcome,
    find_version_dir,
    get_default_branch,
    get_other_worktree_using_branch,
    remote_has_branch,
)
from odoo_toolkit.mv.reset import reset_repo
from odoo_toolkit.mv.switch import switch_repo


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(work_dir: Path, filename: str, content: str = "content") -> str:
    (work_dir / filename).write_text(content)
    git(work_dir, "add", filename)
    git(work_dir, "commit", "-q", "-m", f"add {filename}")
    return git(work_dir, "rev-parse", "HEAD")


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate Git from the user's configuration."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    (tmp_path / "gitconfig").write_text(
        "[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = master\n",
    )


@pytest.fixture
def remotes(tmp_path: Path) -> dict[str, Path]:
    """Create an `origin` remote with `master` and `19.0`, and a `dev` remote with feature branches."""
    origin = tmp_path / "remotes" / "origin.git"
    dev = tmp_path / "remotes" / "dev.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    git(tmp_path, "init", "-q", "--bare", str(dev))

    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(origin), str(seed))
    commit(seed, "base.txt")
    git(seed, "push", "-q", "origin", "master")
    git(seed, "push", "-q", "origin", "master:19.0")
    git(seed, "push", "-q", str(dev), "master:19.0-fix-abc", "master:fix/master")
    return {"origin": origin, "dev": dev, "seed": seed}


@pytest.fixture
def multiverse(tmp_path: Path, remotes: dict[str, Path]) -> Path:
    """Create a multiverse like `otk mv setup` does: a bare repository with worktrees for `master` and `19.0`."""
    root = tmp_path / "odoo-mv"
    bare = root / ".worktree-source" / "odoo" / ".bare"
    git(tmp_path, "clone", "-q", "--bare", str(remotes["origin"]), str(bare))
    git(bare, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    git(bare, "remote", "add", "dev", str(remotes["dev"]))
    git(bare, "fetch", "-q", "origin")
    for version in ("master", "19.0"):
        (root / version).mkdir(parents=True)
        git(bare, "worktree", "add", "-q", str(root / version / "odoo"), version)
        git(root / version / "odoo", "branch", "--set-upstream-to", f"origin/{version}", version)
    return root


def test_find_version_dir_from_nested_path(multiverse: Path) -> None:
    nested = multiverse / "19.0" / "odoo" / "addons" / "web"
    nested.mkdir(parents=True)
    assert find_version_dir(nested) == (multiverse / "19.0").resolve()
    assert find_version_dir(multiverse) is None


def test_remote_has_branch_matches_exact_name(multiverse: Path) -> None:
    repo = Repo(multiverse / "19.0" / "odoo")
    assert remote_has_branch(repo, "dev", "19.0-fix-abc")
    # `fix/master` exists on dev, which must not count as `master`.
    assert not remote_has_branch(repo, "dev", "master")


def test_get_default_branch_of_regular_clone(tmp_path: Path, remotes: dict[str, Path]) -> None:
    # `git remote show -n origin` reports "(not queried)", which used to be parsed as the branch name.
    clone = tmp_path / "upgrade"
    git(tmp_path, "clone", "-q", str(remotes["origin"]), str(clone))
    assert get_default_branch(Repo(clone)) == "master"


def test_reset_stashes_with_label_and_fast_forwards(multiverse: Path, remotes: dict[str, Path]) -> None:
    worktree = multiverse / "19.0" / "odoo"
    git(worktree, "checkout", "-q", "-b", "19.0-my-task")
    (worktree / "base.txt").write_text("uncommitted")
    git(remotes["seed"], "checkout", "-q", "-b", "19.0", "origin/19.0")
    new_head = commit(remotes["seed"], "new.txt")
    git(remotes["seed"], "push", "-q", "origin", "19.0")

    result = reset_repo(worktree, branch="19.0", version="19.0")

    assert result.outcome == Outcome.UPDATED
    assert result.stash == "otk: 19.0/odoo before reset to 19.0"
    assert git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == "19.0"
    assert git(worktree, "rev-parse", "HEAD") == new_head
    assert "otk: 19.0/odoo before reset to 19.0" in git(worktree, "stash", "list")


def test_reset_discards_local_commits_and_reports_them(multiverse: Path, remotes: dict[str, Path]) -> None:
    worktree = multiverse / "19.0" / "odoo"
    local_head = commit(worktree, "local.txt")
    git(remotes["seed"], "checkout", "-q", "-b", "19.0", "origin/19.0")
    remote_head = commit(remotes["seed"], "remote.txt")
    git(remotes["seed"], "push", "-q", "origin", "19.0")

    result = reset_repo(worktree, branch="19.0", version="19.0")

    assert result.outcome == Outcome.WARNING
    assert "discarding 1 local commit" in result.message
    assert local_head[:8] in result.message
    assert git(worktree, "rev-parse", "HEAD") == remote_head
    assert not (worktree / "local.txt").exists()


def test_reset_removes_leftover_module_folders_only(multiverse: Path) -> None:
    worktree = multiverse / "19.0" / "odoo"
    # A module that was deleted upstream leaves its ignored bytecode behind.
    leftover = worktree / "addons" / "removed_module" / "models" / "__pycache__"
    leftover.mkdir(parents=True)
    (leftover / "model.cpython-312.pyc").write_bytes(b"")
    # Bytecode of tracked code is removed, but the folder itself stays.
    (worktree / "__pycache__").mkdir()
    (worktree / "__pycache__" / "base.cpython-312.pyc").write_bytes(b"")
    (worktree / ".gitignore").write_text("__pycache__/\nodoo.conf\nnode_modules\n")
    git(worktree, "add", ".gitignore")
    git(worktree, "commit", "-q", "-m", "ignore")
    git(worktree, "push", "-q", "origin", "19.0")
    # Other ignored files are kept.
    (worktree / "odoo.conf").write_text("[options]")
    (worktree / "node_modules" / "pkg" / "__pycache__").mkdir(parents=True)

    result = reset_repo(worktree, branch="19.0", version="19.0")

    assert result.outcome == Outcome.UPDATED
    assert "removed 3 leftover module folder(s)" in result.message
    assert not (worktree / "addons").exists()
    assert not (worktree / "__pycache__").exists()
    assert (worktree / "odoo.conf").exists()
    assert (worktree / "node_modules" / "pkg" / "__pycache__").exists()
    assert result.stash is None


def test_switch_to_dev_branch_tracks_it(multiverse: Path) -> None:
    worktree = multiverse / "19.0" / "odoo"

    result = switch_repo(worktree, branch="19.0-fix-abc", remote=None, version="19.0")

    assert result.outcome == Outcome.UPDATED
    assert result.branch_found
    assert git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == "19.0-fix-abc"
    assert git(worktree, "rev-parse", "--abbrev-ref", "@{upstream}") == "dev/19.0-fix-abc"


def test_switch_to_missing_branch_keeps_current_branch(multiverse: Path) -> None:
    worktree = multiverse / "19.0" / "odoo"
    (worktree / "base.txt").write_text("uncommitted")

    result = switch_repo(worktree, branch="does-not-exist", remote=None, version="19.0")

    assert result.outcome == Outcome.SKIPPED
    assert not result.branch_found
    assert result.stash is None
    assert git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == "19.0"
    assert (worktree / "base.txt").read_text() == "uncommitted"


def test_cli_switch_and_reset(multiverse: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    worktree = multiverse / "19.0" / "odoo"
    monkeypatch.chdir(worktree / ".")

    result = runner.invoke(app, ["mv", "switch", "odoo-dev:19.0-fix-abc"])
    assert result.exit_code == 0, result.output
    assert git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == "19.0-fix-abc"

    result = runner.invoke(app, ["mv", "switch", "no-such-branch"])
    assert result.exit_code == 1, result.output

    result = runner.invoke(app, ["mv", "reset"])
    assert result.exit_code == 0, result.output
    assert git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == "19.0"


def test_cli_outside_multiverse_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert CliRunner().invoke(app, ["mv", "reset"]).exit_code == 1


def test_switch_to_branch_used_by_other_worktree_fails_without_stashing(multiverse: Path) -> None:
    worktree = multiverse / "19.0" / "odoo"
    (worktree / "base.txt").write_text("uncommitted")
    assert get_other_worktree_using_branch(Repo(worktree), "master") == (multiverse / "master" / "odoo").resolve()

    result = switch_repo(worktree, branch="master", remote=None, version="19.0")

    assert result.outcome == Outcome.FAILED
    assert "is checked out in" in result.message
    assert result.stash is None
    assert (worktree / "base.txt").read_text() == "uncommitted"
