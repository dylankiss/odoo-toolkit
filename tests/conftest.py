from pathlib import Path

import pytest

from odoo_toolkit.mv import setup as mv_setup
from odoo_toolkit.mv.repos import RepoResult


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate Git from the user's configuration."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    (tmp_path / "gitconfig").write_text(
        "[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = master\n",
    )


@pytest.fixture(autouse=True)
def no_git_maintenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never schedule Git's background maintenance, which installs jobs in the user's operating system."""

    def skip_maintenance(*_: object) -> list[RepoResult]:
        return []

    monkeypatch.setattr(mv_setup, "_start_maintenance", skip_maintenance)
