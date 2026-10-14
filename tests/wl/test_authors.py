from pathlib import Path

import pytest
from typer.testing import CliRunner

from odoo_toolkit.main import app
from odoo_toolkit.wl.authors import ConfigError, complete_author, get_authors, resolve_author

CONFIG = """
[weblate.authors]
alice = { name = "Alice Martin", email = "alice.martin@example.com" }
mary = { name = "Mary Smith", email = "mary.smith@example.com" }
"""


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "odoo-toolkit" / "config.toml"
    path.parent.mkdir()
    path.write_text(CONFIG)
    return path


@pytest.mark.usefixtures("config_file")
def test_get_authors() -> None:
    assert get_authors() == {
        "alice": ("Alice Martin", "alice.martin@example.com"),
        "mary": ("Mary Smith", "mary.smith@example.com"),
    }


@pytest.mark.usefixtures("config_file")
def test_resolve_author() -> None:
    assert resolve_author("alice", None) == {"author_name": "Alice Martin", "author_email": "alice.martin@example.com"}
    # An author that isn't configured, as before.
    assert resolve_author("Ann", "ann@example.com") == {"author_name": "Ann", "author_email": "ann@example.com"}
    assert resolve_author(None, None) == {}


@pytest.mark.usefixtures("config_file")
def test_complete_author() -> None:
    assert complete_author("a") == [("alice", "Alice Martin")]
    assert len(complete_author("")) == 2


@pytest.mark.parametrize("author", ['"Bob Jones"', '{ name = "Bob Jones" }', '{ name = "Bob Jones", email = "bob" }'])
def test_authors_need_a_name_and_an_email(config_file: Path, author: str) -> None:
    config_file.write_text(f"[weblate.authors]\nbob = {author}\n")
    with pytest.raises(ConfigError, match="Invalid author `bob`"):
        get_authors()


def test_without_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert get_authors() == {}
    assert "There are no authors" in CliRunner().invoke(app, ["wl", "authors"]).output


@pytest.mark.usefixtures("config_file")
def test_cli_authors_lists_them() -> None:
    result = CliRunner().invoke(app, ["wl", "authors"])

    assert result.exit_code == 0, result.output
    assert "Alice Martin" in result.output
    assert "mary.smith@example.com" in result.output
