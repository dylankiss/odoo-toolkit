from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def no_update_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Don't ask PyPI for a new version of the toolkit."""
    monkeypatch.setenv("OTK_NO_UPDATE_CHECK", "1")


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate Git from the user's configuration."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    (tmp_path / "gitconfig").write_text(
        "[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = master\n",
    )

