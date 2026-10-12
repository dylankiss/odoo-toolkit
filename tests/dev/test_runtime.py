import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from odoo_toolkit.dev import run as dev_run
from odoo_toolkit.dev.runtime import (
    DEBUGGER_READY,
    RunOptions,
    build_run_command,
    check_mountable,
    find_free_port,
    get_addons_paths,
    is_port_free,
)
from odoo_toolkit.main import app
from odoo_toolkit.versions import get_versions


@pytest.fixture
def version_dir(tmp_path: Path) -> Path:
    """Create a version directory like `otk mv setup` does, with modules in some of its repositories."""
    root = tmp_path / "odoo-mv"
    (root / ".worktree-source").mkdir(parents=True)
    version_dir = root / "19.0"
    repos: list[tuple[str, list[str]]] = [
        ("odoo", ["addons/base", "addons/web"]),
        ("enterprise", ["web_enterprise"]),
        ("design-themes", ["theme_default"]),
        ("documentation", []),
    ]
    for repo, modules in repos:
        (version_dir / repo / ".git").mkdir(parents=True)
        for module in modules:
            (version_dir / repo / module).mkdir(parents=True)
            (version_dir / repo / module / "__manifest__.py").write_text("{}")
    # Linked repositories: one with modules, and the upgrade scripts.
    for repo, module in [("odoofin", "odoofin_proxy"), ("upgrade-util", "src"), ("upgrade", "migrations")]:
        (root / repo / ".git").mkdir(parents=True)
        (root / repo / module).mkdir()
        (version_dir / repo).symlink_to(Path("..") / repo)
    (root / "odoofin" / "odoofin_proxy" / "__manifest__.py").write_text("{}")
    return version_dir


def test_get_addons_paths(version_dir: Path) -> None:
    assert get_addons_paths(version_dir) == [
        version_dir / "enterprise",
        version_dir / "design-themes",
        version_dir / "odoofin",
        version_dir / "odoo" / "addons",
    ]


def test_get_addons_paths_without_some_repositories(version_dir: Path) -> None:
    assert get_addons_paths(version_dir, community=True) == [version_dir / "odoo" / "addons"]
    assert get_addons_paths(version_dir, exclude=["enterprise", "odoofin"]) == [
        version_dir / "design-themes",
        version_dir / "odoo" / "addons",
    ]


def test_build_run_command(version_dir: Path) -> None:
    options = RunOptions(
        version_dir=version_dir,
        version=get_versions()["19.0"],
        port=8070,
        debug_port=5679,
        upgrade=True,
        odoo_args=["-d", "mydb"],
        arch="amd64",
    )

    command = build_run_command(
        options,
        multiverse_dir=version_dir.parent,
        git_identity={"GIT_AUTHOR_NAME": "Test"},
        user=None,
        tty=True,
    )

    # The ports are only reachable from your machine.
    assert "127.0.0.1:8070:8069" in command
    assert "127.0.0.1:5679:5678" in command
    # The multiverse is mounted at the same path, and Odoo runs from the version directory.
    assert f"{version_dir.parent}:{version_dir.parent}" in command
    assert command[command.index("--workdir") + 1] == str(version_dir)
    assert "GIT_AUTHOR_NAME=Test" in command
    assert "dylankiss/odoo-dev:19.0" in command
    assert "--init" in command
    assert command[command.index("--platform") + 1] == "linux/amd64"
    # The debugger waits for Visual Studio Code, which waits for the message.
    script = command[command.index("-c") + 1]
    assert f'echo "{DEBUGGER_READY} on port 5679"' in script
    odoo = command[command.index("-c") + 3 :]
    assert odoo[:6] == ["python3", "-m", "debugpy", "--listen", "0.0.0.0:5678", "--wait-for-client"]
    assert odoo[6] == str(version_dir / "odoo" / "odoo-bin")
    assert f"--upgrade-path={version_dir / 'upgrade-util' / 'src'},{version_dir / 'upgrade' / 'migrations'}" in odoo
    assert odoo[-2:] == ["-d", "mydb"]


def test_build_run_command_for_a_shell(version_dir: Path) -> None:
    options = RunOptions(version_dir=version_dir, version=get_versions()["19.0"], port=None, command=["bash"])

    command = build_run_command(options, multiverse_dir=version_dir.parent, git_identity={}, user="1001:1001", tty=False)

    assert "--publish" not in command
    assert command[-1] == "bash"
    assert command[command.index("--user") + 1] == "1001:1001"
    assert "--tty" not in command


def test_check_mountable() -> None:
    assert check_mountable(Path("/Users/me/code/odoo-mv")) is None
    assert check_mountable(Path("/home/me/odoo-mv")) is None
    assert check_mountable(Path("/opt/odoo-mv")) is not None
    assert check_mountable(Path("/home/odoo/mv")) is not None


def test_ports() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        taken = sock.getsockname()[1]
        assert not is_port_free(taken)
        free = find_free_port(taken)
        assert free is not None
        assert free > taken


def always_free(_: int) -> bool:
    return True


def default_port_taken(port: int) -> bool:
    return port != 8069


def same_port(port: int) -> int:
    return port


def test_cli_run(version_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[list[str]] = []

    def capture(command: list[str]) -> None:
        executed.append(command)

    def no_docker(*_: object, **__: object) -> None:
        pass

    monkeypatch.setattr(dev_run, "_exec", capture)
    monkeypatch.setattr(dev_run, "ensure_image", no_docker)
    monkeypatch.setattr(dev_run, "start_services", no_docker)
    monkeypatch.setattr(dev_run, "is_port_free", always_free)
    monkeypatch.chdir(version_dir / "odoo")

    result = CliRunner().invoke(app, ["dev", "run", "--port", "8075", "-x", "enterprise", "--", "-d", "mydb", "-i", "sale"])

    assert result.exit_code == 0, result.output
    assert "addons path: design-themes, odoofin, odoo/addons" in result.output
    assert "127.0.0.1:8075:8069" in executed[0]
    assert executed[0][-4:] == ["-d", "mydb", "-i", "sale"]


def test_cli_run_with_port_in_use(version_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev_run, "is_port_free", default_port_taken)
    monkeypatch.setattr(dev_run, "find_free_port", same_port)
    monkeypatch.chdir(version_dir)

    result = CliRunner().invoke(app, ["dev", "run"])

    assert result.exit_code == 1
    assert "Port 8069 is already in use" in result.output
    assert "--port 8070" in result.output


def test_cli_run_outside_multiverse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert CliRunner().invoke(app, ["dev", "run"]).exit_code == 1
