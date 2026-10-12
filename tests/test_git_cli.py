import subprocess
from pathlib import Path

import pytest

from odoo_toolkit.git_cli import Git, GitError, clone


@pytest.fixture
def repo(tmp_path: Path) -> Git:
    path = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    repo = Git(path)
    (path / "file.txt").write_text("content")
    repo("add", "file.txt")
    repo("commit", "-q", "-m", "init")
    return repo


def test_git_runs_commands(repo: Git) -> None:
    assert repo.current_branch() == "master"
    assert repo.has_branch("master")
    assert not repo.has_branch("19.0")
    assert len(repo.head()) == 40
    assert not repo.is_dirty()
    (repo.path / "untracked.txt").write_text("new")
    assert repo.is_dirty()


def test_git_detached_head_has_no_current_branch(repo: Git) -> None:
    repo("checkout", "-q", "--detach")
    assert repo.current_branch() is None


def test_git_error_shows_the_relevant_line(repo: Git, tmp_path: Path) -> None:
    with pytest.raises(GitError) as error:
        repo("checkout", "does-not-exist")
    assert error.value.message == "error: pathspec 'does-not-exist' did not match any file(s) known to git"
    assert error.value.returncode == 1

    with pytest.raises(GitError, match="fatal: not a git repository"):
        Git(tmp_path)("status")
    with pytest.raises(GitError, match="fatal: cannot change to"):
        Git(tmp_path / "missing")("status")


def test_clone_reports_progress(repo: Git, tmp_path: Path) -> None:
    stages: set[str] = set()

    def track_stage(stage: str, _current: int, _total: int) -> None:
        stages.add(stage)

    cloned = clone(f"file://{repo.path}", tmp_path / "clone", progress=track_stage)

    assert cloned.head() == repo.head()
    assert "Receiving objects" in stages


def test_clone_failure_raises(tmp_path: Path) -> None:
    with pytest.raises(GitError, match=r"does not exist|not appear to be a git repository"):
        clone(f"file://{tmp_path}/missing", tmp_path / "clone")


def test_upstream(repo: Git, tmp_path: Path) -> None:
    assert repo.upstream() is None
    cloned = clone(f"file://{repo.path}", tmp_path / "clone")
    assert cloned.upstream() == ("origin", "master")
    cloned("checkout", "-q", "--detach")
    assert cloned.upstream() is None
