import socket
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from odoo_toolkit.dev import run as dev_run
from odoo_toolkit.dev import runtime
from odoo_toolkit.dev.runtime import (
    DEBUGGER_READY,
    ORBSTACK_SSH_AGENT,
    SSH_AGENT_SOCKET,
    RunOptions,
    build_run_command,
    check_mountable,
    find_free_port,
    get_addons_paths,
    get_ssh_agent,
    is_port_free,
    normalize_volume,
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
        tty=True,
        ssh_agent=None,
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
    assert command[command.index("--name") + 1] == "otk-19.0-8070"
    assert "io.github.dylankiss.odoo-toolkit.role=run" in command
    # The debugger says when it listens, which Visual Studio Code waits for, and then runs Odoo.
    launcher = command[command.index("-c") + 1]
    assert launcher.index("debugpy.listen(('0.0.0.0', 5678))") < launcher.index(f"{DEBUGGER_READY} on port 5679")
    odoo = command[command.index("-c") + 2 :]
    assert odoo[0] == str(version_dir / "odoo" / "odoo-bin")
    assert f"--upgrade-path={version_dir / 'upgrade-util' / 'src'},{version_dir / 'upgrade' / 'migrations'}" in odoo
    assert odoo[-2:] == ["-d", "mydb"]


def test_build_run_command_with_volumes_and_env(version_dir: Path) -> None:
    options = RunOptions(
        version_dir=version_dir,
        version=get_versions()["19.0"],
        volumes=["/data/etc-odoo:/etc/odoo:ro", "/data/ca.pem:/etc/ca.pem:ro"],
        env=["REQUESTS_CA_BUNDLE=/etc/ca.pem"],
    )

    command = build_run_command(options, multiverse_dir=version_dir.parent, git_identity={}, tty=False, ssh_agent=None)

    assert command.count("/data/etc-odoo:/etc/odoo:ro") == 1
    assert command[command.index("/data/ca.pem:/etc/ca.pem:ro") - 1] == "--volume"
    assert command[command.index("REQUESTS_CA_BUNDLE=/etc/ca.pem") - 1] == "--env"
    # They come before the image, so Docker doesn't take them for Odoo's options.
    image = command.index("dylankiss/odoo-dev:19.0")
    assert command.index("/data/ca.pem:/etc/ca.pem:ro") < image
    assert command.index("REQUESTS_CA_BUNDLE=/etc/ca.pem") < image


def test_normalize_volume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # Absolute paths and the names of volumes stay as they are.
    assert normalize_volume("/etc/odoo:/etc/odoo:ro") == "/etc/odoo:/etc/odoo:ro"
    assert normalize_volume("otk-filestore:/data") == "otk-filestore:/data"
    # Relative paths and `~` would be taken for volume names, or not understood, by Docker.
    assert normalize_volume("~/etc-odoo:/etc/odoo") == f"{(tmp_path / 'home' / 'etc-odoo').resolve()}:/etc/odoo"
    assert normalize_volume("./conf:/etc/odoo:ro") == f"{(tmp_path / 'conf').resolve()}:/etc/odoo:ro"


def test_build_run_command_for_a_shell_with_your_ssh_agent(version_dir: Path) -> None:
    options = RunOptions(
        version_dir=version_dir,
        version=get_versions()["19.0"],
        port=None,
        command=["bash"],
        role="shell",
    )
    with socket.socket(socket.AF_UNIX) as agent:
        # Unix sockets have a short maximum path length.
        agent_path = Path(tempfile.mkdtemp(dir="/tmp")) / "agent.sock"
        agent.bind(str(agent_path))
        command = build_run_command(
            options,
            multiverse_dir=version_dir.parent,
            git_identity={},
            tty=False,
            ssh_agent=agent_path,
        )

    assert "--publish" not in command
    assert command[-1] == "bash"
    assert command[command.index("--name") + 1] == "otk-19.0-shell"
    assert "--tty" not in command
    assert f"{agent_path}:{SSH_AGENT_SOCKET}" in command
    assert f"SSH_AUTH_SOCK={SSH_AGENT_SOCKET}" in command
    assert "--group-add" in command


def test_get_ssh_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "is_orbstack", lambda: False)
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    assert get_ssh_agent() is None
    # Not a socket.
    (tmp_path / "file").write_text("")
    monkeypatch.setenv("SSH_AUTH_SOCK", str(tmp_path / "file"))
    assert get_ssh_agent() is None


def test_get_ssh_agent_on_orbstack(monkeypatch: pytest.MonkeyPatch) -> None:
    # OrbStack can't mount the socket from your machine, so the agent is its own, whatever `SSH_AUTH_SOCK` says.
    monkeypatch.setattr(runtime.sys, "platform", "darwin")
    monkeypatch.setattr(runtime, "is_orbstack", lambda: True)
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    assert get_ssh_agent() == Path(ORBSTACK_SSH_AGENT)


def test_check_mountable() -> None:
    assert check_mountable(Path("/Users/me/code/odoo-mv")) is None
    assert check_mountable(Path("/home/me/odoo-mv")) is None
    # Like the homes of Fedora Silverblue, which hide nothing the container needs.
    assert check_mountable(Path("/var/home/me/odoo-mv")) is None
    assert check_mountable(Path("/opt")) is not None
    assert check_mountable(Path("/home")) is not None
    assert check_mountable(Path("/")) is not None
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
    monkeypatch.setattr(dev_run, "_prepare", no_docker)
    monkeypatch.setattr(dev_run, "get_ssh_agent", lambda: None)
    monkeypatch.setattr(dev_run, "is_port_free", always_free)
    monkeypatch.chdir(version_dir / "odoo")

    result = CliRunner().invoke(
        app,
        ["dev", "run", "--port", "8075", "-x", "enterprise,enterprize", "--", "-d", "mydb", "-i", "sale"],
    )

    assert result.exit_code == 0, result.output
    assert "There is no enterprize" in result.output
    assert "addons path: design-themes, odoofin, odoo/addons" in result.output
    assert "127.0.0.1:8075:8069" in executed[0]
    assert executed[0][-4:] == ["-d", "mydb", "-i", "sale"]


def test_cli_run_with_volumes_and_env(version_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    executed: list[list[str]] = []

    def capture(command: list[str]) -> None:
        executed.append(command)

    def no_docker(*_: object, **__: object) -> None:
        pass

    config = tmp_path / "etc-odoo"
    config.mkdir()
    monkeypatch.setattr(dev_run, "_exec", capture)
    monkeypatch.setattr(dev_run, "_prepare", no_docker)
    monkeypatch.setattr(dev_run, "get_ssh_agent", lambda: None)
    monkeypatch.setattr(dev_run, "is_port_free", always_free)
    monkeypatch.chdir(version_dir / "odoo")

    result = CliRunner().invoke(
        app,
        ["dev", "run", "-v", f"{config}:/etc/odoo:ro", "-e", "A=1", "--env", "B=2,3", "--", "-d", "mydb"],
    )

    assert result.exit_code == 0, result.output
    assert f"{config}:/etc/odoo:ro" in executed[0]
    # Values aren't split on commas, like the repositories of `--exclude`.
    assert "B=2,3" in executed[0]
    assert "A=1" in executed[0]
    assert executed[0][-2:] == ["-d", "mydb"]


def test_cli_run_with_a_volume_that_does_not_exist(
    version_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(dev_run, "is_port_free", always_free)
    monkeypatch.chdir(version_dir / "odoo")

    result = CliRunner().invoke(app, ["dev", "run", "-v", f"{tmp_path / 'missing'}:/etc/odoo"])

    assert result.exit_code == 1
    assert "doesn't exist" in result.output


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
