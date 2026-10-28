"""Run Odoo in Docker for a multiverse version directory.

The shared services (PostgreSQL, Mailpit and optionally pgAdmin) run with Docker Compose. Odoo itself runs in a new
container for every `otk dev run`, removed when Odoo stops, so every run can use its own ports.

The multiverse directory is mounted at the same path as on your machine. That way the links between repositories and
the Git worktrees work inside the container, the debugger needs no path mappings, and the paths in tracebacks are the
ones of your machine.
"""

import os
import socket
import stat
import subprocess
import sys
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from odoo_toolkit.mv.config import get_repo_dirs
from odoo_toolkit.versions import OdooVersion

from .images import DOCKER_DIR, DOCKER_REPOSITORY, Arch, get_native_arch

COMPOSE_FILE = DOCKER_DIR / "compose.yaml"
NETWORK = "otk"
DB_HOST = "otk-postgres"
SMTP_HOST = "otk-mailpit"
HOME = "/home/odoo"
# The labels marking the containers of this toolkit, with the Odoo version, and what they're for, as values.
RUN_LABEL = "io.github.dylankiss.odoo-toolkit.version"
ROLE_LABEL = "io.github.dylankiss.odoo-toolkit.role"
# What a container is for: running Odoo, a shell, or exporting terms with `otk po export`.
Role = Literal["run", "shell", "export"]
# The ports Odoo and the debugger listen on inside the container.
ODOO_PORT = 8069
DEBUG_PORT = 5678
# Printed when Odoo waits for the debugger, which Visual Studio Code waits for before attaching.
DEBUGGER_READY = "otk: waiting for the debugger"
VOLUMES = {
    "otk-filestore": f"{HOME}/.local/share/Odoo",
    "otk-pycache": f"{HOME}/.cache/pycache",
}
# Where your SSH agent is available in the container, for Odoo modules that run Git commands.
SSH_AGENT_SOCKET = "/run/otk-ssh-agent.sock"
# Mounting the multiverse at one of these paths, or at one containing them, would hide files the image needs.
SYSTEM_PATHS = ("/bin", "/etc", HOME, "/lib", "/opt", "/root", "/sbin", "/tmp", "/usr", "/var", "/venv")  # noqa: S108


@dataclass
class RunOptions:
    """What to run, and how."""

    version_dir: Path
    version: OdooVersion
    # The port Odoo listens on, on your machine, or None to not publish it.
    port: int | None = 8069
    # The port of the debugger on your machine, or None to not wait for a debugger.
    debug_port: int | None = None
    upgrade: bool = False
    odoo_args: list[str] = field(default_factory=list[str])
    # The repositories to leave out of the addons path, like `enterprise`.
    exclude: list[str] = field(default_factory=list[str])
    # Whether to only load Odoo Community's modules.
    community: bool = False
    # More `--volume` and `--env` arguments for the container, like `/etc/odoo:/etc/odoo:ro` and `KEY=value`.
    volumes: list[str] = field(default_factory=list[str])
    env: list[str] = field(default_factory=list[str])
    # Run this command instead of Odoo, like `bash`.
    command: list[str] | None = None
    # The architecture to run. Another than the machine's runs emulated, which is slower.
    arch: Arch = field(default_factory=get_native_arch)
    role: Role = "run"


def get_image_tag(version: OdooVersion) -> str:
    """Get the image of an Odoo version, like `dylankiss/odoo-dev:19.0`."""
    return f"{DOCKER_REPOSITORY}:{version.name}"


def check_mountable(multiverse_dir: Path) -> str | None:
    """Check whether the multiverse can be mounted at its own path in the container.

    Mounting it hides what the image has at that path, so it can't be a folder the container needs, or one containing
    such a folder. Inside the home folder, it would collide with the volumes.

    :return: The problem, or None if it can be mounted.
    """
    path = str(multiverse_dir).rstrip("/")
    if path.startswith(f"{HOME}/") or any(path == system or system.startswith(f"{path}/") for system in SYSTEM_PATHS):
        return f"The multiverse can't be at [u]{path or '/'}[/u], since the container needs that path itself."
    return None


def get_addons_paths(version_dir: Path, *, exclude: Collection[str] = (), community: bool = False) -> list[Path]:
    """Get the addons paths of a version: every repository in it that contains modules, Enterprise first.

    :param version_dir: The version directory.
    :param exclude: The repositories to leave out, like `enterprise`.
    :param community: Whether to only use Odoo Community's modules.
    :return: The addons paths, in the order Odoo should look for modules.
    """
    paths: list[Path] = []
    for repo_dir in get_repo_dirs(version_dir, include_linked=True):
        if repo_dir.name in exclude or (community and repo_dir.name != "odoo"):
            continue
        candidate = repo_dir / "addons" if repo_dir.name == "odoo" else repo_dir
        if candidate.is_dir() and any((child / "__manifest__.py").is_file() for child in candidate.iterdir()):
            paths.append(candidate)

    def priority(path: Path) -> tuple[int, str]:
        # Enterprise overrides Community modules, so it comes first, and Community last.
        if path.name == "enterprise":
            return (0, "")
        return (2, "") if path == version_dir / "odoo" / "addons" else (1, path.name)

    return sorted(paths, key=priority)


def get_upgrade_paths(version_dir: Path) -> list[Path]:
    """Get the paths of the upgrade scripts in a version, if the upgrade repositories are linked into it."""
    candidates = [version_dir / "upgrade-util" / "src", version_dir / "upgrade" / "migrations"]
    return [path for path in candidates if path.is_dir()]


def get_odoo_args(
    version_dir: Path, *, exclude: Collection[str] = (), community: bool = False, upgrade: bool = False,
) -> list[str]:
    """Get the Odoo options that load the modules of a version and connect Odoo to the shared services.

    :param version_dir: The version directory.
    :param exclude: The repositories to leave out of the addons path, like `enterprise`.
    :param community: Whether to only load Odoo Community's modules.
    :param upgrade: Whether to add the upgrade scripts, if their repositories are linked into the version.
    :return: The options, without the path of `odoo-bin`.
    """
    addons_paths = get_addons_paths(version_dir, exclude=exclude, community=community)
    args = [
        f"--addons-path={','.join(str(path) for path in addons_paths)}",
        f"--db_host={DB_HOST}",
        "--db_user=odoo",
        "--db_password=odoo",
        f"--smtp={SMTP_HOST}",
        "--smtp-port=1025",
        # Since saas-19.1, Odoo only listens on the container's localhost by default, which Docker can't publish.
        "--http-interface=0.0.0.0",
        f"--http-port={ODOO_PORT}",
        f"--data-dir={VOLUMES['otk-filestore']}",
        # Don't stop requests while you're paused in the debugger.
        "--limit-time-cpu=99999",
        "--limit-time-real=99999",
    ]
    if upgrade and (upgrade_paths := get_upgrade_paths(version_dir)):
        args.append(f"--upgrade-path={','.join(str(path) for path in upgrade_paths)}")
    return args


def build_odoo_command(options: RunOptions) -> list[str]:
    """Build the command that starts Odoo in the container."""
    version_dir = options.version_dir
    command = [
        str(version_dir / "odoo" / "odoo-bin"),
        *get_odoo_args(version_dir, exclude=options.exclude, community=options.community, upgrade=options.upgrade),
        *options.odoo_args,
    ]
    if options.debug_port:
        # Only say the debugger is ready once it listens, since Visual Studio Code attaches right away.
        launcher = (
            "import debugpy, runpy, sys; "
            f"debugpy.listen(('0.0.0.0', {DEBUG_PORT})); "
            f"print('{DEBUGGER_READY} on port {options.debug_port}', flush=True); "
            "debugpy.wait_for_client(); "
            "sys.argv = sys.argv[1:]; "
            "runpy.run_path(sys.argv[0], run_name='__main__')"
        )
        # Frozen modules make the debugger miss breakpoints in the standard library.
        command = ["python3", "-Xfrozen_modules=off", "-c", launcher, *command]
    return command


def get_container_name(options: RunOptions) -> str:
    """Get the name of a container, like `otk-19.0-8069`, or `otk-19.0-export-8070` for a `po export` server."""
    role = "" if options.role == "run" else f"-{options.role}"
    return f"otk-{options.version.name}{role}" + (f"-{options.port}" if options.port else "")


def build_run_command(
    options: RunOptions,
    *,
    multiverse_dir: Path,
    git_identity: dict[str, str],
    tty: bool,
    ssh_agent: Path | None = None,
) -> list[str]:
    """Build the `docker run` command for an Odoo container.

    :param options: What to run, and how.
    :param multiverse_dir: The multiverse directory, mounted at the same path in the container.
    :param git_identity: The Git name and email to commit with, as environment variables.
    :param tty: Whether the command runs in a terminal.
    :param ssh_agent: The socket of your SSH agent, to use your SSH keys in the container, if any.
    :return: The command.
    """
    version = options.version.name
    command = options.command or build_odoo_command(options)
    run = ["docker", "run", "--rm", "--name", get_container_name(options)]
    run += ["--label", f"{RUN_LABEL}={version}", "--label", f"{ROLE_LABEL}={options.role}"]
    # A small init process forwards signals to Odoo and cleans up the processes it leaves behind, like wkhtmltopdf.
    run += ["--init"]
    run += ["--platform", f"linux/{options.arch}", "--network", NETWORK, "--workdir", str(options.version_dir)]
    run += ["--interactive", "--tty"] if tty else ["--interactive"]
    if options.port:
        run += ["--publish", f"127.0.0.1:{options.port}:{ODOO_PORT}"]
    if options.debug_port:
        run += ["--publish", f"127.0.0.1:{options.debug_port}:{DEBUG_PORT}"]
    run += ["--volume", f"{multiverse_dir}:{multiverse_dir}"]
    for volume, path in VOLUMES.items():
        run += ["--volume", f"{volume}:{path}"]
    run += ["--env", f"PYTHONPYCACHEPREFIX={VOLUMES['otk-pycache']}"]
    for key, value in git_identity.items():
        run += ["--env", f"{key}={value}"]
    for volume in options.volumes:
        run += ["--volume", volume]
    for variable in options.env:
        run += ["--env", variable]
    run += get_ssh_agent_args(ssh_agent)
    return [*run, get_image_tag(options.version), *command]


def normalize_volume(volume: str) -> str:
    """Make the path on your machine in a `--volume` absolute, since Docker takes a relative one for a volume's name.

    `~` stands for your home folder, and `.` or `..` for the current folder. Names of volumes stay as they are.
    """
    source, separator, rest = volume.partition(":")
    if separator and source.startswith(("~", ".")):
        source = str(Path(source).expanduser().resolve())
    return f"{source}{separator}{rest}"


def get_ssh_agent() -> Path | None:
    """Get the socket of your SSH agent (like the one of Bitwarden or 1Password), if one is running."""
    socket_path = os.environ.get("SSH_AUTH_SOCK")
    if not socket_path:
        return None
    try:
        return Path(socket_path) if stat.S_ISSOCK(Path(socket_path).stat().st_mode) else None
    except OSError:
        return None


def get_ssh_agent_args(ssh_agent: Path | None) -> list[str]:
    """Get the `docker run` arguments that make your SSH agent available in the container.

    The socket keeps its owner in the container: `root` with Docker Desktop, you on Linux. Its group gives the `odoo`
    user access.
    """
    if not ssh_agent:
        return []
    group = "0" if sys.platform != "linux" else str(ssh_agent.stat().st_gid)
    return [
        "--volume", f"{ssh_agent}:{SSH_AGENT_SOCKET}",
        "--env", f"SSH_AUTH_SOCK={SSH_AGENT_SOCKET}",
        "--group-add", group,
    ]


def get_git_identity() -> dict[str, str]:
    """Get your Git name and email, to use for commits made in the container."""
    identity: dict[str, str] = {}
    for setting, variables in (("user.name", ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME")),
                               ("user.email", ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"))):
        result = subprocess.run(["git", "config", "--global", setting], capture_output=True, text=True, check=False)
        if value := result.stdout.strip():
            identity |= dict.fromkeys(variables, value)
    return identity


def get_user_ids() -> tuple[int, int] | None:
    """Get the user and group IDs the image must run with on Linux, so the files Odoo writes are yours.

    Docker Desktop on macOS and Windows maps the file ownership by itself.
    """
    return (os.getuid(), os.getgid()) if sys.platform == "linux" else None


def is_port_free(port: int) -> bool:
    """Check whether a port on your machine is free to publish a container port on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # Like a server, ignore the connections of a stopped container that are still closing.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def find_free_port(start: int, attempts: int = 20) -> int | None:
    """Find the first free port from a given one."""
    return next((port for port in range(start, start + attempts) if is_port_free(port)), None)
