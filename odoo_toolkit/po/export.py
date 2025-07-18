import ast
import os
import re
import subprocess
import xmlrpc.client
from base64 import b64decode
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from operator import itemgetter
from pathlib import Path
from socket import socket
from subprocess import PIPE, CalledProcessError, Popen
from typing import Annotated, Any

from polib import pofile
from rich.progress import Progress, TaskID
from rich.table import Table
from typer import Argument, Exit, Option, Typer

from odoo_toolkit.common import (
    EMPTY_LIST,
    TransientProgress,
    get_odoo_version,
    get_valid_modules_to_path_mapping,
    print,
    print_command_title,
    print_error,
    print_header,
    print_indent,
    print_warning,
)

HTTPS_PORT = 443
WITH_DEMO_VERSION = 18.3

app = Typer()


class _ServerType(str, Enum):
    COM = "Community"
    COM_L10N = "Community Localizations"
    ENT = "Enterprise"
    ENT_L10N = "Enterprise Localizations"
    CUSTOM = "Custom"


@dataclass
class _LogLineData:
    server_formatted: str
    progress: Progress | None
    progress_task: TaskID | None
    log_buffer: str
    database: str
    database_created: bool
    server_error: bool
    error_msg: str | None


@app.command()
def export(
    modules: Annotated[
        list[str],
        Argument(
            help="Export `.pot` files for these Odoo modules (supports glob patterns), or either `all`, `community`,"
                "or `enterprise`.",
        ),
    ],
    start_server: Annotated[
        bool,
        Option(
            "--start-server/--own-server",
            help="Start an Odoo server automatically or connect to your own server.",
            rich_help_panel="Odoo Server Options",
        ),
    ] = True,
    full_install: Annotated[
        bool,
        Option("--full-install", help="Install every available Odoo module.", rich_help_panel="Odoo Server Options"),
    ] = False,
    quick_install: Annotated[
        bool,
        Option("--quick-install", help="Install only the modules to export.", rich_help_panel="Odoo Server Options"),
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
            "-p",
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
        Option(help="Specify the port of your Odoo server.", rich_help_panel="Odoo Server Options"),
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
        Option(help="Specify the PostgreSQL server's hostname.", rich_help_panel="Database Options"),
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
    separate server will be started for Community, Community (Localizations), Enterprise, Enterprise (Localizations),
    and custom modules with only the modules installed to be exported in that version, and all (indirect) dependent
    modules that might contribute terms to the modules to be exported.\n
    \n
    You can also export terms from your own running server using the `--own-server` option and optionally passing the
    correct arguments to reach your Odoo server.\n
    \n
    > Without any options specified, the command is supposed to run from within the parent directory where your `odoo`
    and `enterprise` repositories are checked out with these names. Your database is supposed to run on `localhost`
    using port `5432`, accessible without a password using your current user.\n
    \n
    > Of course, all of this can be tweaked with the available options.
    """
    print_command_title(":outbox_tray: Odoo POT Export")

    module_to_path = get_valid_modules_to_path_mapping(
        modules=modules,
        com_path=com_path,
        ent_path=ent_path,
        extra_addons_paths=extra_addons_paths,
        filter_fn=_is_exportable_to_transifex,
    )
    valid_modules_to_export = module_to_path.keys()

    if not valid_modules_to_export:
        print_error("The provided modules are not available! Nothing to export ...\n")
        raise Exit

    modules_per_server_type = _get_modules_per_server_type(
        module_to_path=module_to_path,
        com_path=com_path,
        ent_path=ent_path,
        extra_addons_paths=extra_addons_paths,
        full_install=full_install,
        quick_install=quick_install,
    )

    print("[b]All modules to export[b]")
    print_indent(", ".join(sorted(valid_modules_to_export)))
    print()

    # Determine the URL to connect to our Odoo server.
    host = "localhost" if start_server else host
    port = _free_port(host, port) if start_server else port
    url = "{protocol}{host}".format(
        protocol="" if "://" in host else "https://" if port == HTTPS_PORT else "http://",
        host=host,
    )

    # Generate a unique database name in case multiple exports would be happening simultaneously.
    if "{port}" in database and not start_server:
        database = database.format(port=port)

    if start_server:
        # Start a temporary Odoo server to export the terms.
        odoo_repo_path = com_path.expanduser().resolve()
        odoo_bin_path = odoo_repo_path / "odoo-bin"
        com_modules_path = odoo_repo_path / "addons"
        ent_modules_path = ent_path.expanduser().resolve()
        extra_modules_paths = [p.expanduser().resolve() for p in extra_addons_paths]
        odoo_version = get_odoo_version(odoo_repo_path)

        # Gather all parameters per server type to run the processes in parallel later.
        params_per_server_type: list[dict[str, Any]] = []
        for seq, (server_type, (modules_to_export, modules_to_install)) in enumerate(modules_per_server_type.items()):
            if not modules_to_export:
                continue

            if server_type == _ServerType.CUSTOM:
                addons_path = [*extra_modules_paths, ent_modules_path, com_modules_path]
            elif server_type in (_ServerType.ENT, _ServerType.ENT_L10N):
                addons_path = [ent_modules_path, com_modules_path]
            else:
                addons_path = [com_modules_path]
            addons_path = ",".join(str(p) for p in addons_path)

            cmd_env = os.environ | {"PYTHONUNBUFFERED": "1"}
            cur_port = _free_port(host, port + seq)
            cur_server_name = server_type.name.lower()
            cur_suffix = f"{cur_port}_{cur_server_name}"
            cur_database = database.format(port=cur_suffix) if "{port}" in database else f"{database}_{cur_suffix}"
            odoo_cmd: list[str | Path] = [
                "python3",       odoo_bin_path,
                "--addons-path", addons_path,
                "--database",    cur_database,
                "--init",        ",".join(modules_to_install),
                "--http-port",   str(cur_port),
                "--db_host",     db_host,
                "--db_port",     str(db_port),
            ]
            if db_username:
                odoo_cmd.extend(["--db_user", db_username])
            if db_password:
                odoo_cmd.extend(["--db_password", db_password])
            if odoo_version and odoo_version >= WITH_DEMO_VERSION:
                odoo_cmd.extend(["--with-demo"])

            dropdb_cmd = ["dropdb", cur_database, "--host", db_host, "--port", str(db_port)]
            if db_username:
                dropdb_cmd.extend(["--username", db_username])
            if db_password:
                cmd_env |= {"PGPASSWORD": db_password}

            server_type_name = f"[{server_type.name}]"
            params_per_server_type.append({
                "server_name": server_type.value,
                "server_formatted": f"[b][cyan]{server_type_name:10}[/cyan][/b]",
                "odoo_cmd": odoo_cmd,
                "dropdb_cmd": dropdb_cmd,
                "env": cmd_env,
                "url": f"{url}:{cur_port}",
                "database": cur_database,
                "username": username,
                "password": password,
                "module_to_path": {k: v for k, v in module_to_path.items() if k in modules_to_export},
            })

        # Run the servers in parallel to speed things up.
        with ThreadPoolExecutor() as executor, TransientProgress() as progress:
            futures = [
                executor.submit(_run_server_and_export_terms, **params, progress=progress)
                for params in params_per_server_type
            ]
            # Wait until every process has finished.
            wait(futures)

    else:
        # Export from a running server.
        with TransientProgress() as progress:
            server_type_name = f"[{_ServerType.CUSTOM.name}]"
            _export_module_terms(
                server_name=_ServerType.CUSTOM.value,
                server_formatted=f"[b][cyan]{server_type_name:10}[/cyan][/b]",
                module_to_path={k: v for k, v in module_to_path.items() if k in valid_modules_to_export},
                url=f"{url}:{port}",
                database=database,
                username=username,
                password=password,
                progress=progress,
            )


def _run_server_and_export_terms(
    server_name: str,
    server_formatted: str,
    odoo_cmd: Sequence[str | Path],
    dropdb_cmd: Sequence[str | Path],
    env: Mapping[str, str],
    url: str,
    database: str,
    username: str,
    password: str,
    module_to_path: Mapping[str, Path],
    progress: Progress,
) -> None:
    """Start an Odoo server and export .pot files for the given modules.

    :param server_name: The server type to run.
    :param server_formatted: The server type to run, formatted in a short printable way.
    :param odoo_cmd: The command to start the Odoo server.
    :param dropdb_cmd: The command to drop the database.
    :param env: The environment variables to run the commands with.
    :param url: The Odoo server URL.
    :param database: The database name.
    :param username: The Odoo username.
    :param password: The Odoo password.
    :param module_to_path: The modules to export mapped to their directories.
    :param progress: The shared progress instance to add a task to.
    """
    progress_task = progress.add_task(f"{server_formatted} :package: Installing modules", total=None)

    data = _LogLineData(
        server_formatted=server_formatted,
        progress=progress,
        progress_task=progress_task,
        log_buffer="",
        database=database,
        database_created=False,
        server_error=False,
        error_msg=None,
    )

    with Popen(odoo_cmd, env=env, stderr=PIPE, text=True) as proc:
        data.progress = progress
        while proc.poll() is None and proc.stderr:
            # As long as the process is still running ...
            log_line = proc.stderr.readline()
            data.log_buffer += log_line

            if _process_server_log_line(log_line=log_line, data=data):
                # The server is ready to export.

                # Close the pipe to prevent overfilling the buffer and blocking the process.
                proc.stderr.close()

                # Stop the progress.
                progress.update(
                    progress_task,
                    description=f"{server_formatted} :package: Installing modules",
                    completed=1,
                    total=1,
                )

                print(f"{server_formatted} Modules have been installed :white_check_mark:")
                print(f"{server_formatted} Odoo Server has started :white_check_mark:")

                # Export module terms.
                _export_module_terms(
                    server_name=server_name,
                    server_formatted=server_formatted,
                    module_to_path=module_to_path,
                    url=url,
                    database=database,
                    username=username,
                    password=password,
                    progress=progress,
                )
                break

            if data.server_error:
                # The server encountered an error.
                print_error(data.error_msg or "The server encountered an error.", data.log_buffer.strip())
                break

        if proc.returncode:
            print_error(
                f"Running the Odoo server failed and exited with code: {proc.returncode}", data.log_buffer.strip(),
            )
            data.server_error = True
        else:
            proc.kill()
            print(f"{server_formatted} Odoo Server has stopped :white_check_mark:")

    if data.database_created and data.server_error:
        print_warning(
            f"The database [b]{database}[/b] was not deleted to allow inspecting the error. "
            "You can delete it manually afterwards.",
        )
    elif data.database_created:
        try:
            subprocess.run(dropdb_cmd, env=env, capture_output=True, check=True)
            print(f"{server_formatted} Database [b]{database}[/b] has been deleted :white_check_mark:\n")
        except CalledProcessError as e:
            print_error(
                f"Deleting database [b]{database}[/b] failed. You can try deleting it manually.", e.stderr.strip(),
            )


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

    if "odoo.modules.loading: init db" in log_line:
        data.log_buffer = ""
        data.database_created = True
        print(f"{data.server_formatted} Database [b]{data.database}[/b] has been created :white_check_mark:")

    match = re.search(r"loading (\d+) modules", log_line)
    if match:
        data.log_buffer = ""
        if data.progress:
            if data.progress_task is None:
                data.progress_task = data.progress.add_task(
                    f"{data.server_formatted} :package: Installing modules",
                    total=None,
                )
            else:
                data.progress.update(data.progress_task, total=int(match.group(1)))

    match = re.search(r"Loading module (\w+) \(\d+/\d+\)", log_line)
    if match:
        data.log_buffer = ""
        if data.progress is not None and data.progress_task is not None:
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
) -> None:
    """Export .pot files for the given modules.

    :param server_name: The server type running.
    :param server_formatted: The server type running, formatted in a short printable way.
    :param module_to_path: A mapping from each module to its directory.
    :param url: The Odoo server URL to connect to.
    :param database: The database name.
    :param username: The Odoo username.
    :param password: The Odoo password.
    """
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(database, username, password, {})
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    progress_task = progress.add_task(f"{server_formatted} :speech_balloon: Exporting terms", total=None)

    modules = list(module_to_path.keys())
    if not modules:
        return

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
        return

    modules_to_export: list[Mapping[str, str]] = sorted(installed_modules, key=itemgetter("name"))
    export_table = Table(box=None, pad_edge=False, show_header=False)

    progress.update(progress_task, total=len(modules_to_export))
    for module in modules_to_export:
        module_name: str = module["name"]
        progress.update(
            progress_task,
            description=f"{server_formatted} :speech_balloon: Exporting terms for [b]{module_name}[/b]",
        )
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
                    "modules": [(6, False, [module["id"]])],
                    "state": "choose",
                },
            ],
        )
        # Export the .pot file.
        models.execute_kw(
            database,
            uid,
            password,
            "base.language.export",
            "act_getfile",
            [[export_id]],
        )
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
            export_table.add_row(
                f"[b]{module_name}[/b]",
                "[d]Exporting the .pot file failed[/d] :negative_squared_cross_mark:",
            )
            continue
        pot_file_content = b64decode(pot_file[0]["data"])
        i18n_path = module_to_path[module_name] / "i18n"
        if not i18n_path.exists():
            i18n_path.mkdir()
        pot_path = i18n_path / f"{module_name}.pot"

        if _is_pot_file_empty(pot_file_content):
            if pot_path.is_file():
                # Remove empty .pot files.
                pot_path.unlink()
                export_table.add_row(
                    f"[b]{module_name}[/b]",
                    f"[d]Removed empty[/d] [b]{module_name}.pot[/b] :negative_squared_cross_mark:",
                )
                continue

            export_table.add_row(
                f"[b]{module_name}[/b]",
                "[d]No terms to translate[/d] :negative_squared_cross_mark:",
            )
            continue

        pot_metadata = None
        with suppress(OSError, ValueError):
            pot_metadata = pofile(str(pot_path)).metadata
        try:
            pot = pofile(pot_file_content.decode())
            if pot_metadata:
                pot.metadata = pot_metadata
            pot.save(str(pot_path))
        except (OSError, ValueError):
            export_table.add_row(
                f"[b]{module_name}[/b]",
                f"[d]Error while exporting [b]{module_name}.pot[/b][/d] :negative_squared_cross_mark:",
            )
        else:
            export_table.add_row(
                f"[b]{module_name}[/b]",
                f"[d]{i18n_path}{os.sep}[/d][b]{module_name}.pot[/b] :white_check_mark: ({len(pot)} terms)",
            )
        progress.advance(progress_task, 1)

    print()
    print_header(f":speech_balloon: Exported Terms for {server_name}")
    print(export_table, "")
    print(f"{server_formatted} Terms have been exported :white_check_mark:")


def _is_pot_file_empty(contents: bytes) -> bool:
    """Determine if the given .pot file's contents doesn't contain translatable terms."""
    try:
        pot = pofile(contents.decode())
        return not any(entry for entry in pot if entry.msgid)
    except (OSError, ValueError):
        return False


def _get_modules_per_server_type(
    module_to_path: Mapping[str, Path],
    com_path: Path,
    ent_path: Path,
    extra_addons_paths: Iterable[Path] = EMPTY_LIST,
    full_install: bool = False,
    quick_install: bool = False,
) -> dict[_ServerType, tuple[set[str], set[str]]]:
    """Get all modules to export and install per server type.

    :param module_to_path: The modules to export, mapped to their directories.
    :param com_path: The path to the Odoo Community repository.
    :param ent_path: The path to the Odoo Enterprise repository.
    :param extra_addons_paths: An optional list of extra directories containing Odoo modules, defaults to `[]`.
    :param full_install: Whether we want to install all modules before exporting, defaults to `False`.
    :param quick_install: Whether we only want to install the modules to export before exporting, defaults to `False`.
    :return: A mapping from each server type to a tuple containing the set of modules to export,
        and the set of modules to install.
    """
    com_modules_path = com_path.expanduser().resolve() / "addons"
    ent_modules_path = ent_path.expanduser().resolve()
    extra_modules_paths = [p.expanduser().resolve() for p in extra_addons_paths]

    modules_to_export: dict[_ServerType, set[str]] = defaultdict(set[str])
    modules_to_install: dict[_ServerType, set[str]] = defaultdict(set[str])
    paths_and_filter_per_server_type: dict[_ServerType, tuple[list[Path], Callable[[str], bool] | None]] = {
        _ServerType.COM: ([com_modules_path], lambda m: _is_exportable(m) and not _is_l10n_module(m)),
        _ServerType.COM_L10N: ([com_modules_path], _is_exportable),
        _ServerType.ENT: (
            [com_modules_path, ent_modules_path],
            lambda m: _is_exportable(m) and not _is_l10n_module(m),
        ),
        _ServerType.ENT_L10N: ([com_modules_path, ent_modules_path], _is_exportable),
        _ServerType.CUSTOM: (extra_modules_paths, None),
    }

    # Determine all modules to export per server type.
    for m, p in module_to_path.items():
        if p.is_relative_to(com_modules_path):
            modules_to_export[_ServerType.COM_L10N if _is_l10n_module(m) else _ServerType.COM].add(m)
            modules_to_install[_ServerType.COM_L10N if _is_l10n_module(m) else _ServerType.COM].add(m)
        elif p.is_relative_to(ent_modules_path) or m == "base":
            modules_to_export[_ServerType.ENT_L10N if _is_l10n_module(m) else _ServerType.ENT].add(m)
            modules_to_install[_ServerType.ENT_L10N if _is_l10n_module(m) else _ServerType.ENT].add(m)
        elif any(p.is_relative_to(emp) for emp in extra_modules_paths):
            modules_to_export[_ServerType.CUSTOM].add(m)
            modules_to_install[_ServerType.CUSTOM].add(m)

    # Determine all modules to install per server type.
    if full_install:
        modules_to_install = _get_full_install_modules_per_server_type(
            com_modules_path=com_modules_path,
            ent_modules_path=ent_modules_path,
            extra_modules_paths=extra_modules_paths,
        )
    elif not quick_install:
        # Some modules' .pot files contain terms generated by other dependent modules.
        # In order to keep them, we add the modules that (indirectly) depend on the installable modules.
        for server_type in _ServerType:
            if not modules_to_export[server_type]:
                continue
            dependents = _find_all_dependents(
                addons_paths=paths_and_filter_per_server_type[server_type][0],
                filter_fn=paths_and_filter_per_server_type[server_type][1],
            )
            modules_to_install[server_type].update(
                d for m in modules_to_export[server_type] for d in dependents.get(m, set[str]())
            )
            # The `calendar` and `rating` modules seem to add fields to a lot of models, so we always install them.
            modules_to_install[server_type].update({"calendar", "rating"})

    return {
        server_type: (modules_to_export[server_type], modules_to_install[server_type]) for server_type in _ServerType
    }


def _find_all_dependents(
    addons_paths: Iterable[Path],
    filter_fn: Callable[[str], bool] | None = None,
) -> Mapping[str, set[str]]:
    """Find all direct and indirect dependents for each module in the given addons paths.

    :param addons_paths: A list of paths to directories containing Odoo module folders.
    :param filter_fn: An optional function to filter modules based on their name.
        If provided, it should return True if the module should be included, False otherwise.
    :return: A mapping where keys are module names and values are sets of their direct and indirect dependents.
        Includes all modules found, even those without any dependencies.
    """
    # Maps modules to its direct dependents.
    dependents_mapping: Mapping[str, set[str]] = defaultdict(set)
    all_modules: set[str] = set()
    l10n_multilang = False

    for addons_path in addons_paths:
        for manifest_file in addons_path.glob("*/__manifest__.py"):
            dependent_module = manifest_file.parent.name
            if dependent_module == "l10n_multilang":
                l10n_multilang = True
            all_modules.add(dependent_module)

            if filter_fn and not filter_fn(dependent_module):
                # Skip module if it doesn't pass the filter.
                continue

            try:
                with manifest_file.open() as f:
                    manifest_content = f.read()
                # Parse the manifest file.
                manifest = ast.literal_eval(manifest_content)
            except (OSError, ValueError):
                # Skip if the manifest file is invalid.
                continue

            for dependency in manifest.get("depends", []):
                if filter_fn and not filter_fn(dependency):
                    # Skip dependency if it doesn't pass the filter.
                    continue
                # Add direct dependent.
                dependents_mapping[dependency].add(dependent_module)
                all_modules.add(dependency)

    all_dependents_mapping: Mapping[str, set[str]] = {}

    def _find_all_dependents_recursive(module: str, visited: set[str]) -> set[str]:
        """Recursively find all dependents (direct and indirect) of a given module.

        :param module: The module to find dependents for.
        :param visited: A set of modules already visited during the current recursive call
            (used to prevent infinite loops due to circular dependencies).
        :return: A set of all dependents of the given module.
        """
        all_dependents: set[str] = set()
        for dependent in dependents_mapping[module]:
            if dependent in visited:
                # Avoid infinite recursion due to circular dependencies.
                continue
            all_dependents.add(dependent)
            all_dependents.update(_find_all_dependents_recursive(dependent, visited | {dependent}))
        return all_dependents

    # Find all dependents for each module.
    for module in all_modules:
        all_dependents_mapping[module] = _find_all_dependents_recursive(module, set())
        if l10n_multilang and _is_l10n_module(module):
            # Add `l10n_multilang` to all l10n modules, to have all translatable fields exported.
            all_dependents_mapping[module].add("l10n_multilang")

    return all_dependents_mapping


def _get_full_install_modules_per_server_type(
    com_modules_path: Path,
    ent_modules_path: Path,
    extra_modules_paths: Iterable[Path] = EMPTY_LIST,
) -> dict[_ServerType, set[str]]:
    """Get all modules to install per server type for .pot export with `full_install = True`."""
    modules: dict[_ServerType, set[str]] = defaultdict(set)

    for m in (f.parent.name for f in com_modules_path.glob("*/__manifest__.py")):
        # Add each Community module to the right server types.
        if _is_l10n_module(m):
            modules[_ServerType.COM_L10N].add(m)
            modules[_ServerType.ENT_L10N].add(m)
        else:
            modules[_ServerType.COM].add(m)
            modules[_ServerType.ENT].add(m)

    for m in (f.parent.name for f in ent_modules_path.glob("*/__manifest__.py")):
        # Add each Enterprise module to the right server types.
        if _is_l10n_module(m):
            modules[_ServerType.ENT_L10N].add(m)
        else:
            modules[_ServerType.ENT].add(m)

    # Add the base module.
    modules[_ServerType.ENT].add("base")

    # Add all custom modules.
    modules[_ServerType.CUSTOM].update(
        f.parent.name for p in extra_modules_paths for f in p.glob("*/__manifest__.py")
    )

    return modules


def _is_exportable_to_transifex(module: str) -> bool:
    """Determine if the given module should be exported to Transifex."""
    return (
        ("l10n_" not in module or module == "l10n_multilang")
        and "theme_" not in module
        and "hw_" not in module
        and "test" not in module
        and "pos_blackbox_be" not in module
    )


def _is_exportable(module: str) -> bool:
    """Determine if the given module should be exportable at all."""
    return "hw_" not in module and "test" not in module


def _is_l10n_module(module: str) -> bool:
    """Determine if the given module is a localization related module."""
    return "l10n_" in module and module != "l10n_multilang"


def _free_port(host: str, start_port: int) -> int:
    """Find the first free port on the host starting from the provided port."""
    for port in range(start_port, 65536):
        with socket() as s:
            try:
                s.bind((host, port))
            except OSError:
                continue
            else:
                return port
    return 8069
