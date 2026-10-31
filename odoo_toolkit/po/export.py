import ast
import os
import re
import shlex
import subprocess
import time
import xmlrpc.client
from base64 import b64decode
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache
from http.client import HTTPConnection, HTTPSConnection
from operator import itemgetter
from pathlib import Path
from subprocess import DEVNULL, PIPE, Popen
from typing import Annotated

from polib import pofile
from rich.markup import escape
from rich.progress import Progress, TaskID
from rich.table import Table
from typer import Argument, Exit, Option, Typer

from odoo_toolkit.common import (
    EMPTY_LIST,
    TransientProgress,
    console,
    get_odoo_version,
    is_l10n_module,
    print,
    print_command_title,
    print_error,
    print_header,
    print_indent,
    print_warning,
)
from odoo_toolkit.dev.common import ensure_image, start_services
from odoo_toolkit.dev.images import Arch, get_native_arch
from odoo_toolkit.dev.runtime import (
    DB_HOST,
    VOLUMES,
    RunOptions,
    build_run_command,
    check_mountable,
    find_free_port,
    get_container_args,
    get_container_name,
    get_git_identity,
    get_image_tag,
    get_ssh_agent,
)
from odoo_toolkit.mv.config import find_version_dir
from odoo_toolkit.versions import OdooVersion, get_versions

from .common import select_modules

HTTPS_PORT = 443
WITH_DEMO_VERSION = 18.3
# Seconds to wait for each answer of the Odoo server, so a stuck server doesn't make us wait forever. Generous, since
# exporting a big module like `base` takes a while.
XMLRPC_TIMEOUT = 1800

app = Typer()


class ServerType(StrEnum):
    """The kinds of Odoo servers the terms are exported from, each with its own addons path."""

    COM = "Community"
    ENT = "Enterprise"
    L10N = "Localizations"
    CUSTOM = "Custom"


@dataclass
class _Container:
    """Where to run the Odoo servers in Docker: the image of a multiverse version directory."""

    version_dir: Path
    version: OdooVersion
    multiverse_dir: Path


@dataclass
class _Server:
    """An Odoo server to start, and export the terms of some modules from."""

    name: str
    # The name, formatted in a short printable way.
    formatted: str
    url: str
    database: str
    module_to_path: dict[str, Path]
    odoo_cmd: list[str]
    env: dict[str, str] = field(default_factory=dict[str, str])
    # The command to stop the server, if stopping `odoo_cmd` doesn't.
    stop_cmd: list[str] | None = None
    # The commands to remove the database and its files afterwards, if any.
    drop_cmds: list[list[str]] = field(default_factory=list[list[str]])


@dataclass
class _LogLineData:
    server_formatted: str
    progress: Progress
    progress_task: TaskID
    log_buffer: str
    database: str
    database_created: bool
    server_error: bool
    error_msg: str | None


@app.command()
def export(  # noqa: C901, PLR0912, PLR0915
    modules: Annotated[
        list[str],
        Argument(
            help="Export `.pot` files for these Odoo modules (supports glob patterns), or either `all`, `community`, "
            "`enterprise`, `community-l10n`, or `enterprise-l10n`.",
        ),
    ],
    exclude: Annotated[
        list[str],
        Option(
            "--exclude",
            "-x",
            help="Exclude these modules from being installed and exported, or `default` for localizations, themes, "
            "hardware and tests.",
        ),
    ] = EMPTY_LIST,
    path_filters: Annotated[
        list[Path],
        Option("--path-filter", "-f", help="Only include modules within these paths."),
    ] = EMPTY_LIST,
    start_server: Annotated[
        bool,
        Option(
            "--start-server/--own-server",
            help="Start an Odoo server automatically or connect to your own server.",
            rich_help_panel="Odoo Server Options",
        ),
    ] = True,
    keep_db: Annotated[
        bool,
        Option(
            "--keep-db",
            help="Keep the database after exporting the `.pot` files.",
            rich_help_panel="Odoo Server Options",
        ),
    ] = False,
    full_install: Annotated[
        bool,
        Option(
            "--full-install",
            help="Install every available Odoo module in Community and Enterprise.",
            rich_help_panel="Odoo Server Options",
        ),
    ] = False,
    quick_install: Annotated[
        bool,
        Option("--quick-install", help="Install only the modules to export.", rich_help_panel="Odoo Server Options"),
    ] = False,
    local: Annotated[
        bool,
        Option(
            "--local",
            help="Run the Odoo servers with Python on this machine, instead of in the Docker image of the Odoo "
            "Multiverse version directory you're in.",
            rich_help_panel="Odoo Server Options",
        ),
    ] = False,
    build: Annotated[
        bool,
        Option(
            "--build",
            help="Build the Docker image on your machine instead of pulling it.",
            rich_help_panel="Odoo Server Options",
        ),
    ] = False,
    com_path: Annotated[
        Path,
        Option(
            "--com-path",
            "-c",
            help="Specify the path to your Odoo Community repository.",
            rich_help_panel="Odoo Server Options",
        ),
    ] = Path("odoo"),
    ent_path: Annotated[
        Path,
        Option(
            "--ent-path",
            "-e",
            help="Specify the path to your Odoo Enterprise repository.",
            rich_help_panel="Odoo Server Options",
        ),
    ] = Path("enterprise"),
    extra_addons_paths: Annotated[
        list[Path],
        Option(
            "--addons-path",
            "-a",
            help="Specify extra addons paths if your modules are not in Community or Enterprise.",
            rich_help_panel="Odoo Server Options",
        ),
    ] = EMPTY_LIST,
    username: Annotated[
        str,
        Option(
            "--username",
            "-u",
            help="Specify the username to log in to Odoo.",
            rich_help_panel="Odoo Server Options",
        ),
    ] = "admin",
    password: Annotated[
        str,
        Option(
            "--password",
            help="Specify the password to log in to Odoo.",
            rich_help_panel="Odoo Server Options",
        ),
    ] = "admin",  # noqa: S107
    host: Annotated[
        str,
        Option(help="Specify the hostname of your Odoo server.", rich_help_panel="Odoo Server Options"),
    ] = "localhost",
    port: Annotated[
        int,
        Option(
            help="Specify the port of your Odoo server. When starting servers, they use the first free ports from it.",
            rich_help_panel="Odoo Server Options",
        ),
    ] = 8069,
    database: Annotated[
        str,
        Option(
            "--database",
            "-d",
            help="Specify the PostgreSQL database name used by Odoo.",
            rich_help_panel="Database Options",
        ),
    ] = "export_pot_db_{port}",
    db_host: Annotated[
        str,
        Option(
            help="Specify the PostgreSQL server's hostname. The database options don't apply to servers in Docker, "
            "which use the PostgreSQL of `otk dev`.",
            rich_help_panel="Database Options",
        ),
    ] = "localhost",
    db_port: Annotated[
        int,
        Option(help="Specify the PostgreSQL server's port.", rich_help_panel="Database Options"),
    ] = 5432,
    db_username: Annotated[
        str,
        Option(help="Specify the PostgreSQL server's username.", rich_help_panel="Database Options"),
    ] = "",
    db_password: Annotated[
        str,
        Option(help="Specify the PostgreSQL user's password.", rich_help_panel="Database Options"),
    ] = "",
) -> None:
    """Export Odoo translation files (`.pot`) to each module's `i18n` folder.

    This command can autonomously start separate Odoo servers to export translatable terms for one or more modules. A
    separate server is started for Community, Enterprise, localizations, and custom modules, with only the modules to
    export installed, and all (indirect) dependent modules that might contribute terms to them.\n
    \n
    Inside an Odoo Multiverse version directory (like `19.0`), the servers run in that version's Docker image, like
    `otk dev run`, with the PostgreSQL of `otk dev`. Use `--local` to run them with Python on this machine instead.\n
    \n
    You can also export terms from your own running server using the `--own-server` option and optionally passing the
    correct arguments to reach your Odoo server.\n
    \n
    > Outside a multiverse, the command is supposed to run from within the parent directory where your `odoo` and
    `enterprise` repositories are checked out with these names. Your database is supposed to run on `localhost` using
    port `5432`, accessible without a password using your current user.\n
    \n
    > Of course, all of this can be tweaked with the available options.
    """
    print_command_title(":outbox_tray: Odoo POT Export")

    if full_install and quick_install:
        print_error("Use either [b]--full-install[/b] or [b]--quick-install[/b], not both.\n")
        raise Exit(1)

    container = _find_container(local=local or not start_server)
    selection = select_modules(
        modules,
        exclude=exclude,
        path_filters=path_filters,
        com_path=com_path,
        ent_path=ent_path,
        extra_addons_paths=extra_addons_paths,
    )
    if container:
        _check_mounted(container, [selection.com_path, selection.ent_path, *extra_addons_paths])
    module_to_path = selection.module_to_path
    if not module_to_path:
        print_error("The provided modules are not available! Nothing to export ...\n")
        raise Exit(1)

    if extra_addons_paths and "base" in module_to_path:
        extra_modules_paths = [p.expanduser().resolve() for p in extra_addons_paths]
        custom_modules = [
            m for m, p in module_to_path.items()
            if any(p.is_relative_to(emp) for emp in extra_modules_paths)
        ]
        if custom_modules:
            print_warning(
                "Exporting [b]base[/b] is not compatible with exporting modules from extra addons paths.\nSkipping:",
            )
            print_indent(", ".join(sorted(custom_modules)))
            print()
            module_to_path = {m: p for m, p in module_to_path.items() if m not in custom_modules}

    modules_per_server_type = get_modules_per_server_type(
        module_to_path=module_to_path,
        com_path=selection.com_path,
        ent_path=selection.ent_path,
        extra_addons_paths=extra_addons_paths,
        full_install=full_install,
        quick_install=quick_install,
        include_path=selection.include_path,
        include_module=selection.include_module,
    )

    print("[b]All modules to export[/b]")
    print_indent(", ".join(sorted(module_to_path)))
    print()

    # Determine the URL to connect to our Odoo server.
    host = "localhost" if start_server else host
    url = "{protocol}{host}".format(
        protocol="" if "://" in host else "https://" if port == HTTPS_PORT else "http://",
        host=host,
    )

    if not start_server:
        # Export from a running server.
        database = database.format(port=port)
        with TransientProgress() as progress:
            server_formatted = _format_server_name(ServerType.CUSTOM)
            try:
                succeeded = [
                    _export_module_terms(
                        server_name=ServerType.CUSTOM.value,
                        server_formatted=server_formatted,
                        module_to_path=module_to_path,
                        url=f"{url}:{port}",
                        database=database,
                        username=username,
                        password=password,
                        progress=progress,
                    ),
                ]
            except (OSError, xmlrpc.client.Error) as e:
                print_error(f"{server_formatted} Exporting the terms failed.", str(e))
                succeeded = [False]
    else:
        # Start temporary Odoo servers to export the terms.
        odoo_repo_path = selection.com_path.expanduser().resolve()
        ent_modules_path = selection.ent_path.expanduser().resolve()
        extra_modules_paths = [p.expanduser().resolve() for p in extra_addons_paths]
        arch = get_native_arch()
        if container:
            ensure_image(container.version, build=build, arch=arch)
            start_services()

        servers: list[_Server] = []
        odoo_version = get_odoo_version(odoo_repo_path)
        # The same for every server, and each takes a command to find out.
        git_identity: dict[str, str] = get_git_identity() if container else {}
        ssh_agent = get_ssh_agent() if container else None
        server_types = {t: modules for t, modules in modules_per_server_type.items() if modules[0]}
        ports = free_ports(host, port, len(server_types))
        for (server_type, (modules_to_export, modules_to_install)), server_port in zip(
            server_types.items(), ports, strict=True,
        ):
            suffix = f"{server_port}_{server_type.name.lower()}"
            server_database = database.format(port=suffix) if "{port}" in database else f"{database}_{suffix}"
            odoo_cmd: list[str] = [
                "python3", str(odoo_repo_path / "odoo-bin"),
                "--addons-path", _get_addons_path(
                    server_type, odoo_repo_path / "addons", ent_modules_path, extra_modules_paths,
                ),
                "--database", server_database,
                "--init", ",".join(sorted(modules_to_install)),
                "--limit-time-cpu", "99999999",
                "--limit-time-real", "99999999",
            ]
            if odoo_version and odoo_version >= WITH_DEMO_VERSION:
                odoo_cmd.append("--with-demo")
            server = _Server(
                name=server_type.value,
                formatted=_format_server_name(server_type),
                url=f"{url}:{server_port}",
                database=server_database,
                module_to_path={k: v for k, v in module_to_path.items() if k in modules_to_export},
                odoo_cmd=odoo_cmd,
            )
            if container:
                _run_in_container(
                    server,
                    container,
                    port=server_port,
                    arch=arch,
                    keep_db=keep_db,
                    git_identity=git_identity,
                    ssh_agent=ssh_agent,
                )
            else:
                _run_locally(
                    server,
                    port=server_port,
                    keep_db=keep_db,
                    db=(db_host, db_port, db_username, db_password),
                )
            servers.append(server)

        # Run the servers in parallel to speed things up.
        with ThreadPoolExecutor() as executor, TransientProgress() as progress:
            futures = [
                executor.submit(
                    run_server_and_export_terms, server, username=username, password=password, progress=progress,
                )
                for server in servers
            ]
            # Wait until every process has finished, and raise what failed unexpectedly.
            wait(futures)
            succeeded = [future.result() for future in futures]

    if not all(succeeded):
        raise Exit(1)


def run_server_and_export_terms(server: _Server, *, username: str, password: str, progress: Progress) -> bool:
    """Start an Odoo server and export .pot files for its modules.

    :param server: The server to start.
    :param username: The Odoo username.
    :param password: The Odoo password.
    :param progress: The shared progress instance to add a task to.
    :return: `True` if all terms were exported, `False` if not.
    """
    data = _LogLineData(
        server_formatted=server.formatted,
        progress=progress,
        progress_task=progress.add_task(f"{server.formatted} :package: Installing modules", total=None),
        log_buffer="",
        database=server.database,
        database_created=False,
        server_error=False,
        error_msg=None,
    )

    with Popen(server.odoo_cmd, env=server.env or None, stdin=DEVNULL, stderr=PIPE, text=True) as proc:
        try:
            data.server_error = not _wait_and_export_terms(
                proc, data, server, username=username, password=password, progress=progress,
            )
        finally:
            # Leaving the `with` block waits for the server to stop, so make sure it does.
            if _stop_server(proc, server):
                print(f"{server.formatted} Odoo Server has stopped :white_check_mark:")

    if data.database_created and data.server_error:
        print_warning(
            f"{server.formatted} The database [b]{server.database}[/b] was not deleted to allow inspecting the error. "
            "You can delete it manually afterwards.",
        )
    elif data.database_created and not server.drop_cmds:
        print(f"{server.formatted} Database [b]{server.database}[/b] has been kept to re-use or inspect later :white_check_mark:\n")
    elif data.database_created:
        for drop_cmd in server.drop_cmds:
            try:
                result = subprocess.run(drop_cmd, env=server.env or None, capture_output=True, text=True, check=False)
                error = result.stderr.strip() if result.returncode else None
            except OSError as e:
                # Like `dropdb` that isn't installed.
                error = str(e)
            if error is not None:
                print_error(
                    f"{server.formatted} Deleting database [b]{server.database}[/b] failed. You can try deleting it "
                    "manually.",
                    error,
                )
                _print_command_for_copy(drop_cmd)
                break
        else:
            print(f"{server.formatted} Database [b]{server.database}[/b] has been deleted :white_check_mark:\n")

    return not data.server_error


def _stop_server(proc: Popen[str], server: _Server) -> bool:
    """Stop a server, if it still runs.

    :return: Whether it was running.
    """
    running = proc.poll() is None
    # `docker run` can stop without its container, like when we stop reading its log, so always stop that.
    if server.stop_cmd:
        running = subprocess.run(server.stop_cmd, capture_output=True, check=False).returncode == 0 or running
    if proc.poll() is None:
        proc.kill()
        proc.wait()
    return running


def _format_server_name(server_type: ServerType) -> str:
    """Format the name of a server in a short printable way, like `[COM]`, to prefix its messages with."""
    name = f"[{server_type.name}]"
    return f"[b][cyan]{name:10}[/cyan][/b]"


def _get_addons_path(server_type: ServerType, com: Path, ent: Path, extra: list[Path]) -> str:
    """Get the addons path of a server: custom modules see everything, the others only what they need."""
    if server_type == ServerType.CUSTOM:
        paths = [*extra, ent, com]
    elif server_type in (ServerType.ENT, ServerType.L10N):
        paths = [ent, com]
    else:
        paths = [com]
    return ",".join(str(path) for path in paths)


def _run_in_container(
    server: _Server,
    container: _Container,
    *,
    port: int,
    arch: Arch,
    keep_db: bool,
    git_identity: dict[str, str],
    ssh_agent: Path | None,
) -> None:
    """Make a server run in the version's Docker image, with the PostgreSQL of `otk dev`.

    :param git_identity: The Git identity to commit with in the container.
    :param ssh_agent: The SSH agent socket to forward, or None if there is none.
    """
    options = RunOptions(
        version_dir=container.version_dir,
        version=container.version,
        port=port,
        # Odoo listens on its usual port in the container, published on the free port we found.
        command=[*server.odoo_cmd, *get_container_args()],
        arch=arch,
        role="export",
    )
    server.odoo_cmd = build_run_command(
        options,
        multiverse_dir=container.multiverse_dir,
        git_identity=git_identity,
        tty=False,
        ssh_agent=ssh_agent,
    )
    # Stopping `docker run` doesn't stop the container.
    server.stop_cmd = ["docker", "rm", "--force", get_container_name(options)]
    if not keep_db:
        filestore = f"{VOLUMES['otk-filestore']}/filestore/{server.database}"
        server.drop_cmds = [
            # Odoo's connections may not be closed yet, right after stopping it.
            ["docker", "exec", DB_HOST, "dropdb", "--username=odoo", "--force", server.database],
            [
                "docker", "run", "--rm", "--platform", f"linux/{arch}",
                "--volume", f"otk-filestore:{VOLUMES['otk-filestore']}",
                get_image_tag(container.version), "rm", "-rf", filestore,
            ],
        ]


def _run_locally(server: _Server, *, port: int, keep_db: bool, db: tuple[str, int, str, str]) -> None:
    """Make a server run with Python on this machine.

    :param db: The PostgreSQL host, port, user and password.
    """
    db_host, db_port, db_username, db_password = db
    server.odoo_cmd += ["--http-port", str(port), "--db_host", db_host, "--db_port", str(db_port)]
    if db_username:
        server.odoo_cmd += ["--db_user", db_username]
    server.env = dict(os.environ)
    if db_password:
        # Odoo and dropdb both read it from here, so it doesn't show up in the commands.
        server.env["PGPASSWORD"] = db_password
    if not keep_db:
        dropdb_cmd = ["dropdb", server.database, "--host", db_host, "--port", str(db_port)]
        server.drop_cmds = [[*dropdb_cmd, "--username", db_username] if db_username else dropdb_cmd]


def _find_container(*, local: bool) -> _Container | None:
    """Find the multiverse version directory you're in, to run the Odoo servers in its Docker image.

    :param local: Whether to run the servers on this machine instead.
    :raises Exit: If the multiverse can't be mounted in a container.
    :return: Where to run the servers, or None to run them on this machine.
    """
    if local or not (version_dir := find_version_dir()):
        return None
    if not (version := get_versions().get(version_dir.name)):
        print_warning(f"Odoo [b]{version_dir.name}[/b] has no Docker image, so the servers run on this machine.\n")
        return None
    multiverse_dir = version_dir.parent
    if problem := check_mountable(multiverse_dir):
        print_error(f"{problem}\n")
        raise Exit(1)
    return _Container(version_dir=version_dir, version=version, multiverse_dir=multiverse_dir)


def _check_mounted(container: _Container, paths: Iterable[Path]) -> None:
    """Make sure the repositories are in the multiverse, since only that is mounted in the container.

    :raises Exit: If one isn't.
    """
    for path in paths:
        if not path.expanduser().resolve().is_relative_to(container.multiverse_dir):
            print_error(
                f"[u]{path}[/u] isn't in the multiverse, so the Odoo servers in Docker can't use it. Use [b]--local[/b] "
                "to run them on this machine instead.\n",
            )
            raise Exit(1)


def _wait_and_export_terms(
    proc: Popen[str],
    data: _LogLineData,
    server: _Server,
    *,
    username: str,
    password: str,
    progress: Progress,
) -> bool:
    """Follow the server's log until its modules are installed, then export the terms.

    :return: `True` if the terms were exported, `False` if the server or the export failed.
    """
    while proc.poll() is None and proc.stderr:
        # As long as the process is still running ...
        log_line = proc.stderr.readline()
        data.log_buffer += log_line

        if _process_server_log_line(log_line=log_line, data=data):
            # The server is ready to export. Close the pipe to prevent overfilling the buffer and blocking the process.
            proc.stderr.close()
            progress.update(data.progress_task, completed=1, total=1)
            print(f"{server.formatted} Modules have been installed :white_check_mark:")
            print(f"{server.formatted} Odoo Server has started :white_check_mark:")
            try:
                return _export_module_terms(
                    server_name=server.name,
                    server_formatted=server.formatted,
                    module_to_path=server.module_to_path,
                    url=server.url,
                    database=server.database,
                    username=username,
                    password=password,
                    progress=progress,
                )
            except (OSError, xmlrpc.client.Error) as e:
                print_error(f"{server.formatted} Exporting the terms failed.", str(e))
                return False

        if data.server_error:
            # The traceback follows the error line, so give Odoo a moment to log it, then read what's left.
            time.sleep(1)
            _stop_server(proc, server)
            data.log_buffer += proc.stderr.read()
            print_error(
                f"{server.formatted} {data.error_msg or 'The server encountered an error.'}", data.log_buffer.strip(),
            )
            _print_command_for_copy(server.odoo_cmd)
            return False

    # The server stopped by itself. Its last lines often hold the traceback.
    if proc.stderr and not proc.stderr.closed:
        data.log_buffer += proc.stderr.read()
    print_error(
        f"{server.formatted} Running the Odoo server failed and exited with code: {proc.returncode}",
        data.log_buffer.strip(),
    )
    _print_command_for_copy(server.odoo_cmd)
    return False


def _process_server_log_line(log_line: str, data: _LogLineData) -> bool:
    """Process an Odoo server log line and update the passed data.

    :param log_line: The log line to process.
    :param data: The data needed to process the line and to be updated by this function.
    :return: `True` if the server is ready to export, `False` if not.
    """
    if "Modules loaded." in log_line:
        return True

    if "Failed to load registry" in log_line:
        data.server_error = True
        data.error_msg = "An error occurred during loading! Terminating the process ..."

    if "Connection to the database failed" in log_line:
        data.server_error = True
        data.error_msg = "Could not connect to the database! Terminating the process ..."

    if "odoo.modules.loading: init db" in log_line or "odoo.modules.loading: Initializing database" in log_line:
        data.log_buffer = ""
        data.database_created = True
        print(f"{data.server_formatted} Database [b]{data.database}[/b] has been created :white_check_mark:")

    match = re.search(r"loading (\d+) modules", log_line)
    if match:
        data.log_buffer = ""
        data.progress.update(data.progress_task, total=int(match.group(1)))

    match = re.search(r"Loading module (\w+) \(\d+/\d+\)", log_line)
    if match:
        data.log_buffer = ""
        data.progress.update(
            data.progress_task,
            advance=1,
            description=f"{data.server_formatted} :package: Installing module [b]{match.group(1)}[/b]",
        )
    return False


def _export_module_terms(
    server_name: str,
    server_formatted: str,
    module_to_path: Mapping[str, Path],
    url: str,
    database: str,
    username: str,
    password: str,
    progress: Progress,
) -> bool:
    """Export .pot files for the given modules.

    :param server_name: The server type running.
    :param server_formatted: The server type running, formatted in a short printable way.
    :param module_to_path: A mapping from each module to its directory.
    :param url: The Odoo server URL to connect to.
    :param database: The database name.
    :param username: The Odoo username.
    :param password: The Odoo password.
    :return: `True` if the terms of all modules were exported, `False` if not.
    :raises OSError: If the server can't be reached, or doesn't answer in time.
    :raises xmlrpc.client.Error: If the server returns an error.
    """
    modules = list(module_to_path.keys())
    if not modules:
        return True

    common = _server_proxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(database, username, password, {})
    # Odoo answers `False` when logging in fails.
    if not isinstance(uid, int) or not uid:
        print_error(f"{server_formatted} Logging in as [b]{username}[/b] on database [b]{database}[/b] failed.")
        return False
    models = _server_proxy(f"{url}/xmlrpc/2/object")

    progress_task = progress.add_task(f"{server_formatted} :speech_balloon: Exporting terms", total=None)

    # Export the terms.
    installed_modules = models.execute_kw(
        database,
        uid,
        password,
        "ir.module.module",
        "search_read",
        [
            [["name", "in", modules], ["state", "=", "installed"]],
            ["name"],
        ],
    )
    if not isinstance(installed_modules, list):
        print_warning(f"{server_formatted} No modules installed to export")
        return False

    modules_to_export: list[Mapping[str, str]] = sorted(installed_modules, key=itemgetter("name"))
    export_table = Table(box=None, pad_edge=False, show_header=False)
    success = True
    if not_installed := sorted(set(modules) - {m["name"] for m in modules_to_export}):
        success = False
        for module_name in not_installed:
            export_table.add_row(
                f"[b]{module_name}[/b]",
                "[d]Not installed, so not exported[/d] :negative_squared_cross_mark:",
            )

    progress.update(progress_task, total=len(modules_to_export))
    for module in modules_to_export:
        module_name: str = module["name"]
        progress.update(
            progress_task,
            description=f"{server_formatted} :speech_balloon: Exporting terms for [b]{module_name}[/b]",
        )
        exported, message = _export_module_pot(
            models=models,
            database=database,
            uid=uid,
            password=password,
            module_id=int(module["id"]),
            module_name=module_name,
            module_path=module_to_path[module_name],
        )
        success = success and exported
        export_table.add_row(f"[b]{module_name}[/b]", message)
        progress.advance(progress_task, 1)

    print()
    print_header(f":speech_balloon: Exported Terms for {server_name}")
    print(export_table, "")
    print(f"{server_formatted} Terms have been exported :white_check_mark:")
    return success


def _export_module_pot(
    *,
    models: xmlrpc.client.ServerProxy,
    database: str,
    uid: int,
    password: str,
    module_id: int,
    module_name: str,
    module_path: Path,
) -> tuple[bool, str]:
    """Export the .pot file of one module into its `i18n` directory.

    :return: A tuple containing `False` if exporting failed and `True` otherwise, and the message to show.
    """
    # Create the export wizard with the current module.
    export_id = models.execute_kw(
        database,
        uid,
        password,
        "base.language.export",
        "create",
        [
            {
                "lang": "__new__",
                "format": "po",
                "modules": [(6, False, [module_id])],
                "state": "choose",
            },
        ],
    )
    # Export the .pot file.
    models.execute_kw(database, uid, password, "base.language.export", "act_getfile", [[export_id]])
    # Get the exported .pot file.
    pot_file = models.execute_kw(
        database,
        uid,
        password,
        "base.language.export",
        "read",
        [[export_id], ["data"], {"bin_size": False}],
    )
    if not isinstance(pot_file, list):
        return False, "[d]Exporting the .pot file failed[/d] :negative_squared_cross_mark:"
    pot_file_content = b64decode(pot_file[0]["data"]) if pot_file[0].get("data") else False
    i18n_path = module_path / "i18n"
    pot_path = i18n_path / f"{module_name}.pot"

    if not pot_file_content or _is_pot_file_empty(pot_file_content):
        if pot_path.is_file():
            # Remove empty .pot files.
            pot_path.unlink()
            return True, f"[d]Removed empty[/d] [b]{module_name}.pot[/b] :negative_squared_cross_mark:"
        return True, "[d]No terms to translate[/d] :negative_squared_cross_mark:"

    try:
        i18n_path.mkdir(exist_ok=True)
        pot = pofile(pot_file_content.decode())
        pot.save(str(pot_path))
    except (OSError, ValueError) as e:
        return False, f"[d]Error while exporting [b]{module_name}.pot[/b][/d] :negative_squared_cross_mark:\n{e}"
    return True, f"[d]{i18n_path}{os.sep}[/d][b]{module_name}.pot[/b] :white_check_mark: ({len(pot)} terms)"


class _TimeoutTransport(xmlrpc.client.Transport):
    def make_connection(self, host: tuple[str, dict[str, str]] | str) -> HTTPConnection:
        connection = super().make_connection(host)
        connection.timeout = XMLRPC_TIMEOUT
        return connection


class _TimeoutSafeTransport(xmlrpc.client.SafeTransport):
    def make_connection(self, host: tuple[str, dict[str, str]] | str) -> HTTPSConnection:
        connection = super().make_connection(host)
        connection.timeout = XMLRPC_TIMEOUT
        return connection


def _server_proxy(url: str) -> xmlrpc.client.ServerProxy:
    """Connect to an Odoo XML-RPC endpoint, with a timeout."""
    transport = _TimeoutSafeTransport() if url.startswith("https://") else _TimeoutTransport()
    return xmlrpc.client.ServerProxy(url, transport=transport)


def _is_pot_file_empty(contents: bytes) -> bool:
    """Determine if the given .pot file's contents doesn't contain translatable terms."""
    try:
        pot = pofile(contents.decode())
        return not any(entry for entry in pot if entry.msgid)
    except (OSError, ValueError):
        return False


def get_modules_per_server_type(  # noqa: C901, PLR0912
    module_to_path: Mapping[str, Path],
    com_path: Path,
    ent_path: Path,
    extra_addons_paths: Iterable[Path] = EMPTY_LIST,
    full_install: bool = False,
    quick_install: bool = False,
    include_path: Callable[[Path], bool] = lambda _: True,
    include_module: Callable[[str], bool] = lambda _: True,
) -> dict[ServerType, tuple[set[str], set[str]]]:
    """Get all modules to export and install per server type.

    :param module_to_path: The modules to export, mapped to their directories.
    :param com_path: The path to the Odoo Community repository.
    :param ent_path: The path to the Odoo Enterprise repository.
    :param extra_addons_paths: An optional list of extra directories containing Odoo modules, defaults to `[]`.
    :param full_install: Whether we want to install all modules before exporting, defaults to `False`.
    :param quick_install: Whether we only want to install the modules to export before exporting, defaults to `False`.
    :param include_path: A function to include modules based on their directory path, defaults to always `True`.
    :param include_module: A function returning `True` if a module name should be included, defaults to always `True`.
    :return: A mapping from each server type to a tuple containing the set of modules to export,
        and the set of modules to install.
    """
    com_modules_path = com_path.expanduser().resolve() / "addons"
    ent_modules_path = ent_path.expanduser().resolve()
    extra_modules_paths = [p.expanduser().resolve() for p in extra_addons_paths]

    modules_to_export: dict[ServerType, set[str]] = defaultdict(set[str])
    modules_to_install: dict[ServerType, set[str]] = defaultdict(set[str])
    def include_non_l10n_module(m: str) -> bool:
        return not is_l10n_module(m) and include_module(m)

    paths_and_filter_per_server_type: dict[ServerType, tuple[list[Path], Callable[[str], bool]]] = {
        ServerType.COM: ([com_modules_path], include_non_l10n_module),
        ServerType.ENT: ([com_modules_path, ent_modules_path], include_non_l10n_module),
        ServerType.L10N: ([com_modules_path, ent_modules_path], include_module),
        ServerType.CUSTOM: (extra_modules_paths, include_module),
    }

    # Determine all modules to export per server type.
    for m, p in module_to_path.items():
        if p.is_relative_to(com_modules_path):
            modules_to_export[ServerType.L10N if is_l10n_module(m) else ServerType.COM].add(m)
            modules_to_install[ServerType.L10N if is_l10n_module(m) else ServerType.COM].add(m)
        elif p.is_relative_to(ent_modules_path):
            modules_to_export[ServerType.L10N if is_l10n_module(m) else ServerType.ENT].add(m)
            modules_to_install[ServerType.L10N if is_l10n_module(m) else ServerType.ENT].add(m)
        elif any(p.is_relative_to(emp) for emp in extra_modules_paths) or m == "base":
            # We want to export base with all addons paths, so we can get all module definitions in there.
            modules_to_export[ServerType.CUSTOM].add(m)
            modules_to_install[ServerType.CUSTOM].add(m)

    # Track server types that already have a complete module install (dependents resolution is not needed).
    fully_installed_server_types: set[ServerType] = set()
    if full_install or ("base" in module_to_path and not quick_install):
        modules_to_install_tmp = _get_full_install_modules_per_server_type(
            com_modules_path=com_modules_path,
            ent_modules_path=ent_modules_path,
            include_path=include_path,
        )
        if full_install:
            for server_type, modules in modules_to_install_tmp.items():
                modules_to_install[server_type].update(modules)
            fully_installed_server_types = set(ServerType)
        else:
            # If we're exporting base, install all community and enterprise modules on the CUSTOM server.
            # extra_addons_paths are in the addons path to discover all module definitions, but not installed.
            modules_to_install[ServerType.CUSTOM].update(modules_to_install_tmp[ServerType.CUSTOM])
            fully_installed_server_types.add(ServerType.CUSTOM)
    if not full_install and not quick_install:
        # Some modules' .pot files contain terms generated by other dependent modules.
        # In order to keep them, we add the modules that (indirectly) depend on the installable modules.
        for server_type in ServerType:
            if server_type in fully_installed_server_types or not modules_to_export[server_type]:
                continue
            addons_paths, include = paths_and_filter_per_server_type[server_type]
            modules_to_install[server_type] |= find_dependents(addons_paths, include, modules_to_export[server_type])
            # The `calendar` and `rating` modules seem to add fields to a lot of models, so we always install them.
            if include_module("calendar"):
                modules_to_install[server_type].add("calendar")
            if include_module("rating"):
                modules_to_install[server_type].add("rating")

    return {
        server_type: (modules_to_export[server_type], modules_to_install[server_type]) for server_type in ServerType
    }


@cache
def _collect_manifest_deps(addons_paths: tuple[Path, ...]) -> tuple[dict[str, list[str]], set[str], bool]:
    """Collect the dependencies of all modules in the given addons paths, once per set of addons paths.

    :param addons_paths: The directories containing Odoo modules.
    :return: A tuple of (module → direct dependencies, all known modules, l10n_multilang found).
    """
    module_deps: dict[str, list[str]] = {}
    all_modules: set[str] = set()
    for addons_path in addons_paths:
        for manifest_file in addons_path.glob("*/__manifest__.py"):
            module = manifest_file.parent.name
            all_modules.add(module)
            try:
                manifest = ast.literal_eval(manifest_file.read_text(encoding="utf-8"))
            except (OSError, ValueError, SyntaxError) as e:
                print_warning(f"Reading [b]{manifest_file}[/b] failed, so its dependencies are left out: {e}")
                continue
            module_deps[module] = manifest.get("depends", [])
            all_modules.update(module_deps[module])
    return module_deps, all_modules, "l10n_multilang" in all_modules


def _expand_excluded_set(
    all_modules: set[str],
    include_module: Callable[[str], bool],
    module_deps: dict[str, list[str]],
) -> set[str]:
    """Compute the transitively expanded set of excluded modules.

    A module is excluded if ``include_module`` returns ``False`` for it, or if any module in its transitive dependency
    chain is excluded (installing it would force the excluded dependency to be installed as well).

    :param all_modules: All known module names.
    :param include_module: A function returning `True` if a module name should be included.
    :param module_deps: A mapping from each module to its direct dependencies.
    :return: The set of all modules that should be excluded.
    """
    direct_dependents: dict[str, set[str]] = defaultdict(set)
    for module, deps in module_deps.items():
        for dep in deps:
            direct_dependents[dep].add(module)

    excluded = {m for m in all_modules if not include_module(m)}
    worklist = list(excluded)
    while worklist:
        for dependent in direct_dependents[worklist.pop()] - excluded:
            excluded.add(dependent)
            worklist.append(dependent)
    return excluded


def find_dependents(addons_paths: Iterable[Path], include_module: Callable[[str], bool], modules: set[str]) -> set[str]:
    """Find the modules that (indirectly) depend on the given ones, since they can add terms to them.

    :param addons_paths: The directories containing Odoo modules.
    :param include_module: A function returning `True` if a module name may be installed.
    :param modules: The modules to find the dependents of.
    :return: Their direct and indirect dependents, and `l10n_multilang` for localizations, which makes all
        translatable fields exported.
    """
    module_deps, all_modules, l10n_multilang = _collect_manifest_deps(tuple(addons_paths))
    excluded = _expand_excluded_set(all_modules, include_module, module_deps)
    direct_dependents: dict[str, set[str]] = defaultdict(set)
    for module, deps in module_deps.items():
        if module not in excluded:
            for dep in deps:
                direct_dependents[dep].add(module)

    found: set[str] = set()
    worklist = [module for module in modules if module not in excluded]
    while worklist:
        for dependent in direct_dependents[worklist.pop()] - found:
            found.add(dependent)
            worklist.append(dependent)
    if l10n_multilang and "l10n_multilang" not in excluded and any(is_l10n_module(m) for m in modules - excluded):
        found.add("l10n_multilang")
    return found


def _get_full_install_modules_per_server_type(
    com_modules_path: Path,
    ent_modules_path: Path,
    include_path: Callable[[Path], bool],
) -> dict[ServerType, set[str]]:
    """Get all modules to install per server type for .pot export with `full_install = True`.

    :param com_modules_path: The `addons` folder of Odoo Community.
    :param ent_modules_path: The Odoo Enterprise repository.
    :param include_path: Whether to include the module at a path.
    :return: The modules to install per server type.
    """
    modules: dict[ServerType, set[str]] = defaultdict(set)
    # Localizations get a server of their own. Community modules also go on the Enterprise and custom servers, and
    # Enterprise modules on the custom one.
    for modules_path, server_types in (
        (com_modules_path, (ServerType.COM, ServerType.ENT, ServerType.CUSTOM)),
        (ent_modules_path, (ServerType.ENT, ServerType.CUSTOM)),
    ):
        for m in (f.parent.name for f in modules_path.glob("*/__manifest__.py")):
            if not include_path(modules_path / m):
                continue
            for server_type in (ServerType.L10N,) if is_l10n_module(m) else server_types:
                modules[server_type].add(m)
    return modules


def free_ports(host: str, start_port: int, count: int) -> list[int]:
    """Find free ports for several servers, each one after the previous: finding a free port doesn't reserve it."""
    ports: list[int] = []
    for _ in range(count):
        start = ports[-1] + 1 if ports else start_port
        if (port := find_free_port(start, attempts=65536, host=host)) is None:
            print_error(f"There is no free port on [b]{host}[/b] from port {start} on.\n")
            raise Exit(1)
        ports.append(port)
    return ports


def _print_command_for_copy(command: Sequence[str | Path]) -> None:
    """Print the retry command wrapped to avoid terminal soft-wrap during copy."""
    print("[b]Retry command:[/b]")
    # Not as markup, since a command can contain brackets, and wrapped already.
    print(escape(_format_wrapped_retry_command(command)), highlight=False, soft_wrap=True)


def _format_wrapped_retry_command(cmd: Sequence[str | Path]) -> str:
    """Return the original command split across continuation lines within the terminal width."""
    quoted_parts = [shlex.quote(str(part)) for part in cmd]
    if not quoted_parts:
        return ""

    max_width = _get_retry_command_max_width()
    lines = [quoted_parts[0]]
    for part in quoted_parts[1:]:
        candidate = f"{lines[-1]} {part}"
        if len(candidate) + 2 <= max_width:
            lines[-1] = candidate
        else:
            lines.append(part)

    return " \\\n".join(lines)


def _get_retry_command_max_width() -> int:
    """Get the width of the console the command is printed to, which is a default one when it isn't a terminal."""
    return max(console.width, 40)
