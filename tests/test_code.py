from pathlib import Path

import pytest
from typer.testing import CliRunner

from odoo_toolkit import code as code_module
from odoo_toolkit.main import app


@pytest.fixture
def multiverse_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a multiverse with a `19.0` version directory, where the `code` command exists."""
    root = tmp_path / "mv"
    (root / ".worktree-source").mkdir(parents=True)
    (root / "19.0").mkdir()

    def which(_command: str) -> str:
        return "/bin/code"

    monkeypatch.setattr(code_module.shutil, "which", which)
    return root


@pytest.fixture
def code_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record the commands `otk code` runs, instead of running them."""
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> None:
        calls.append(command)

    monkeypatch.setattr(code_module.subprocess, "run", run)
    return calls


def test_code_opens_the_version_directory_you_are_in(
    multiverse_dir: Path, code_calls: list[list[str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(multiverse_dir / "19.0")
    monkeypatch.setenv("PWD", str(multiverse_dir / "19.0"))

    assert CliRunner().invoke(app, ["code"]).exit_code == 0

    assert code_calls == [["/bin/code", str(multiverse_dir / "19.0")]]


def test_code_opens_a_named_version_with_a_profile(
    multiverse_dir: Path, code_calls: list[list[str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(code_module, "get_current_dir", lambda: multiverse_dir)

    assert CliRunner().invoke(app, ["code", "19.0", "--profile", "Odoo"]).exit_code == 0
    # The profile can also come from the multiverse's configuration.
    (multiverse_dir / "multiverse.toml").write_text('[code]\nprofile = "Work"\n')
    assert CliRunner().invoke(app, ["code", "19.0"]).exit_code == 0

    assert code_calls == [
        ["/bin/code", "--profile", "Odoo", str(multiverse_dir / "19.0")],
        ["/bin/code", "--profile", "Work", str(multiverse_dir / "19.0")],
    ]


def test_code_opens_the_dev_container_of_a_version(
    multiverse_dir: Path, code_calls: list[list[str]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(code_module, "get_current_dir", lambda: multiverse_dir)
    version_dir = multiverse_dir / "19.0"
    (version_dir / ".devcontainer").mkdir()
    (version_dir / ".devcontainer" / "devcontainer.json").write_text("{}")

    assert CliRunner().invoke(app, ["code", "19.0", "-p", "Odoo"]).exit_code == 0
    assert CliRunner().invoke(app, ["code", "19.0", "--local"]).exit_code == 0

    host_path = str(version_dir).encode().hex()
    assert code_calls == [
        ["/bin/code", "--profile", "Odoo", "--folder-uri", f"vscode-remote://dev-container+{host_path}{version_dir}"],
        ["/bin/code", str(version_dir)],
    ]


def test_code_needs_a_known_version_and_the_code_command(
    multiverse_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(code_module, "get_current_dir", lambda: multiverse_dir)

    unknown = CliRunner().invoke(app, ["code", "20.0"])
    assert unknown.exit_code == 1
    assert "no version directory" in unknown.output

    def which(_command: str) -> None:
        return None

    monkeypatch.setattr(code_module.shutil, "which", which)
    missing = CliRunner().invoke(app, ["code", "19.0"])
    assert missing.exit_code == 1
    assert "isn't available" in missing.output
