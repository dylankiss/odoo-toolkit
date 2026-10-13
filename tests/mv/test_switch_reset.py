from pathlib import Path

import pytest
from typer.testing import CliRunner

from odoo_toolkit.git_cli import Git
from odoo_toolkit.main import app
from odoo_toolkit.mv.config import find_version_dir
from odoo_toolkit.mv.repos import (
    Outcome,
    get_default_branch,
    get_other_worktree_using_branch,
    remote_has_branch,
)
from odoo_toolkit.mv.reset import check_repo, reset_repo
from odoo_toolkit.mv.switch import switch_repo
from tests.mv.conftest import commit, git


def test_find_version_dir_from_nested_path(multiverse: Path) -> None:
    nested = multiverse / "19.0" / "odoo" / "addons" / "web"
    nested.mkdir(parents=True)
    assert find_version_dir(nested) == (multiverse / "19.0").resolve()
    assert find_version_dir(multiverse) is None


def test_remote_has_branch_matches_exact_name(multiverse: Path) -> None:
    repo = Git(multiverse / "19.0" / "odoo")
    assert remote_has_branch(repo, "dev", "19.0-fix-abc")
    # `fix/master` exists on dev, which must not count as `master`.
    assert not remote_has_branch(repo, "dev", "master")


def test_get_default_branch_of_regular_clone(tmp_path: Path, remotes: dict[str, Path]) -> None:
    # `git remote show -n origin` reports "(not queried)", which used to be parsed as the branch name.
    clone = tmp_path / "upgrade"
    git(tmp_path, "clone", "-q", str(remotes["origin"]), str(clone))
    assert get_default_branch(Git(clone)) == "master"


def test_reset_stashes_with_label_and_fast_forwards(multiverse: Path, remotes: dict[str, Path]) -> None:
    worktree = multiverse / "19.0" / "odoo"
    git(worktree, "checkout", "-q", "-b", "19.0-my-task")
    (worktree / "base.txt").write_text("uncommitted")
    git(remotes["seed"], "checkout", "-q", "-b", "19.0", "origin/19.0")
    new_head = commit(remotes["seed"], "new.txt")
    git(remotes["seed"], "push", "-q", "origin", "19.0")

    assert check_repo(worktree, "19.0").outcome == Outcome.UPDATED
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

    check = check_repo(worktree, "19.0")
    assert check.outcome == Outcome.WARNING
    assert check.message.startswith("1 commit(s) on [b]19.0[/b]")
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

    result = switch_repo(worktree, branch="19.0-fix-abc", version="19.0")

    assert result.outcome == Outcome.UPDATED
    assert result.branch_found
    assert git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == "19.0-fix-abc"
    assert git(worktree, "rev-parse", "--abbrev-ref", "@{upstream}") == "dev/19.0-fix-abc"


def test_switch_to_missing_branch_keeps_current_branch(multiverse: Path) -> None:
    worktree = multiverse / "19.0" / "odoo"
    (worktree / "base.txt").write_text("uncommitted")

    result = switch_repo(worktree, branch="does-not-exist", version="19.0")

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
    assert get_other_worktree_using_branch(Git(worktree), "master") == (multiverse / "master" / "odoo").resolve()

    result = switch_repo(worktree, branch="master", version="19.0")

    assert result.outcome == Outcome.FAILED
    assert "is checked out in" in result.message
    assert result.stash is None
    assert (worktree / "base.txt").read_text() == "uncommitted"


def test_switch_back_to_a_local_only_branch(multiverse: Path) -> None:
    worktree = multiverse / "19.0" / "odoo"
    git(worktree, "checkout", "-q", "-b", "my-experiment")
    git(worktree, "checkout", "-q", "19.0")

    result = switch_repo(worktree, branch="my-experiment", version="19.0")

    assert result.outcome == Outcome.WARNING
    assert "only exists locally" in result.message
    assert git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == "my-experiment"


def test_branch_deleted_on_github_stops_being_fetched(multiverse: Path, remotes: dict[str, Path]) -> None:
    worktree = multiverse / "19.0" / "odoo"
    switch_repo(worktree, branch="19.0-fix-abc", version="19.0")
    # Merged and deleted on GitHub.
    git(remotes["seed"], "push", "-q", str(remotes["dev"]), "--delete", "19.0-fix-abc")

    result = switch_repo(worktree, branch="does-not-exist", version="19.0")

    assert result.outcome == Outcome.SKIPPED
    assert "was deleted on GitHub" in result.message
    # Fetching a deleted branch would fail the whole fetch.
    git(worktree, "fetch", "-q", "dev")


def test_switch_and_reset_refuse_during_a_merge(multiverse: Path) -> None:
    worktree = multiverse / "19.0" / "odoo"
    head = git(worktree, "rev-parse", "HEAD")
    Path(git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "MERGE_HEAD")).write_text(head)

    assert "a merge is in progress" in switch_repo(worktree, branch="master", version="19.0").message
    assert check_repo(worktree, "19.0").outcome == Outcome.FAILED


def test_fast_forward_blocked_by_uncommitted_changes_says_so(multiverse: Path, remotes: dict[str, Path]) -> None:
    worktree = multiverse / "19.0" / "odoo"
    git(remotes["seed"], "checkout", "-q", "-b", "19.0", "origin/19.0")
    commit(remotes["seed"], "base.txt", "changed upstream")
    git(remotes["seed"], "push", "-q", "origin", "19.0")
    (worktree / "base.txt").write_text("changed locally")

    result = switch_repo(worktree, branch="19.0", version="19.0")

    assert result.outcome == Outcome.WARNING
    assert "uncommitted changes" in result.message
    assert "diverged" not in result.message


def test_cli_reset_asks_before_discarding_local_commits(multiverse: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worktree = multiverse / "19.0" / "odoo"
    local_head = commit(worktree, "local.txt")
    (worktree / "base.txt").write_text("uncommitted")
    monkeypatch.chdir(worktree)

    result = CliRunner().invoke(app, ["mv", "reset"], input="n\n")
    assert result.exit_code == 1, result.output
    assert "1 commit(s) on 19.0" in result.output
    assert git(worktree, "rev-parse", "HEAD") == local_head

    result = CliRunner().invoke(app, ["mv", "reset"], input="y\n")
    assert result.exit_code == 0, result.output
    assert git(worktree, "rev-parse", "HEAD") != local_head
    # The exact command to restore the stashed changes.
    assert f"git -C {multiverse / '19.0' / 'odoo'} stash pop stash@{{0}}" in result.output


def test_commands_work_inside_a_linked_repository(multiverse: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (multiverse / "upgrade").mkdir()
    (multiverse / "19.0" / "upgrade").symlink_to(Path("..") / "upgrade", target_is_directory=True)
    # The operating system only knows where the link points to, the shell knows where you are.
    monkeypatch.chdir(multiverse / "upgrade")
    monkeypatch.setenv("PWD", str(multiverse / "19.0" / "upgrade"))

    assert find_version_dir() == multiverse / "19.0"
