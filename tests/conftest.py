from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def no_update_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Don't ask PyPI for a new version of the toolkit."""
    monkeypatch.setenv("OTK_NO_UPDATE_CHECK", "1")


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the tests away from the configuration file of the user."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate Git from the user's configuration."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    (tmp_path / "gitconfig").write_text(
        "[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = master\n",
    )


@pytest.fixture(autouse=True)
def isolated_claude_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the dev containers of the tests away from the Claude Code configuration of the user."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty-claude-config"))


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep what the tests write for the proxy of the dev containers away from the state folder of the user."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
