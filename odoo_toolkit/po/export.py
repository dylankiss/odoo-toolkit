import contextlib
import os
import re
import subprocess
import xmlrpc.client
from base64 import b64decode
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from operator import itemgetter
from pathlib import Path
from socket import socket
from subprocess import PIPE, CalledProcessError, Popen
from typing import Annotated

from polib import pofile
from rich.progress import TaskID
from rich.table import Table
from typer import Argument, Exit, Option, Typer

from odoo_toolkit.common import (
    TransientProgress,
    get_valid_modules_to_path_mapping,
    print,
    print_command_title,
    print_error,
    print_header,
    print_warning,
)

HTTPS_PORT = 443

app = Typer()


class _ServerType(str, Enum):
    COM = "Community"
    COM_L10N = "Community Localizations"
    ENT = "Enterprise"
    ENT_L10N = "Enterprise Localizations"
    FULL_BASE = "Full Base"


@dataclass
class _LogLineData:
    progress: TransientProgress
    progress_task: TaskID
    log_buffer: str
    database: str
    database_created: bool
    server_error: bool
    error_msg: str


@app.command()
def export(
    modules: Annotated[
        list[str],
        Argument(
            help='Export .pot files for these Odoo modules (supports glob patterns), or either "all", "community",'
                'or "enterprise".',
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
    ] = "__export_pot_db__",
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
    """Export Odoo translation files (.pot) to each module's i18n folder.

    This command can autonomously start separate Odoo servers to export translatable terms for one or more modules. A
    separate server will be started for Community, Community (Localizations), Enterprise, and Enterprise (Localizations)
    modules with only the modules installed to be exported in that version.\n
    \n
    When exporting the translations for `base`, we install all possible modules to ensure all terms added in by other
    modules get exported in the `base.pot` files as well.\n
    \n
    You can also export terms from your own running server using the `--no-start-server` option and optionally passing
    the correct arguments to reach your Odoo server.\n
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
        full_install=full_install,
    )

    print(f"Modules to export: [b]{'[/b], [b]'.join(sorted(valid_modules_to_export))}[/b]\n")

    # Determine the URL to connect to our Odoo server.
    host = "localhost" if start_server else host
    port = _free_port(host, port) if start_server else port
    url = "{protocol}{host}:{port}".format(
        protocol="" if "://" in host else "https://" if port == HTTPS_PORT else "http://",
        host=host,
        port=port,
    )

    if start_server:
        # Start a temporary Odoo server to export the terms.
        odoo_bin_path = com_path.expanduser().resolve() / "odoo-bin"
        com_modules_path = com_path.expanduser().resolve() / "addons"
        ent_modules_path = ent_path.expanduser().resolve()

        for server_type, (modules_to_export, modules_to_install) in modules_per_server_type.items():
            if not modules_to_export:
                continue

            if server_type in (_ServerType.ENT, _ServerType.ENT_L10N, _ServerType.FULL_BASE):
                addons_path = f"{ent_modules_path},{com_modules_path}"
            else:
                addons_path = str(com_modules_path)

            cmd_env = os.environ | {"PYTHONUNBUFFERED": "1"}
            odoo_cmd = [
                "python3",       odoo_bin_path,
                "--addons-path", addons_path,
                "--database",    database,
                "--init",        ",".join(modules_to_install),
                "--http-port",   str(port),
                "--db_host",     db_host,
                "--db_port",     str(db_port),
            ]
            if db_username:
                odoo_cmd.extend(["--db_user", db_username])
            if db_password:
                odoo_cmd.extend(["--db_password", db_password])

            dropdb_cmd = ["dropdb", database, "--host", db_host, "--port", str(db_port)]
            if db_username:
                dropdb_cmd.extend(["--username", db_username])
            if db_password:
                cmd_env |= {"PGPASSWORD": db_password}

            _run_server_and_export_terms(
                server_type=server_type,
                odoo_cmd=odoo_cmd,
                dropdb_cmd=dropdb_cmd,
                env=cmd_env,
                url=url,
                database=database,
                username=username,
                password=password,
                module_to_path={k: v for k, v in module_to_path.items() if k in modules_to_export},
            )

    else:
        # Export from a running server.
        _export_module_terms(
            module_to_path={k: v for k, v in module_to_path.items() if k in valid_modules_to_export},
            url=url,
            database=database,
            username=username,
            password=password,
        )


def _run_server_and_export_terms(
    server_type: _ServerType,
    odoo_cmd: list[str],
    dropdb_cmd: list[str],
    env: dict[str, str],
    url: str,
    database: str,
    username: str,
    password: str,
    module_to_path: dict[str, Path],
) -> None:
    """Start an Odoo server and export .pot files for the given modules.

    :param server_type: The server type to run.
    :type server_type: :class:`_ServerType`
    :param odoo_cmd: The command to start the Odoo server.
    :type odoo_cmd: list[str]
    :param dropdb_cmd: The command to drop the database.
    :type dropdb_cmd: list[str]
    :param env: The environment variables to run the commands with.
    :type env: dict[str, str]
    :param url: The Odoo server URL.
    :type url: str
    :param database: The database name.
    :type database: str
    :param username: The Odoo username.
    :type username: str
    :param password: The Odoo password.
    :type password: str
    :param module_to_path: The modules to export mapped to their directories.
    :type module_to_path: dict[str, :class:`pathlib.Path`]
    """
    print_header(f":rocket: Start Odoo Server ({server_type.value})")

    data = _LogLineData(
        progress=None,
        progress_task=None,
        log_buffer="",
        database=database,
        database_created=False,
        server_error=False,
        error_msg=None,
    )

    with Popen(odoo_cmd, env=env, stderr=PIPE, text=True) as proc, TransientProgress() as progress:
        data.progress = progress
        while proc.poll() is None:
            # As long as the process is still running ...
            log_line = proc.stderr.readline()
            data.log_buffer += log_line

            if _process_server_log_line(log_line=log_line, data=data):
                # The server is ready to export.

                # Close the pipe to prevent overfilling the buffer and blocking the process.
                proc.stderr.close()

                # Stop the progress.
                progress.update(data.progress_task, description="Installing modules")
                progress.stop()
                print("Modules have been installed :white_check_mark:")
                print("Odoo Server has started :white_check_mark:\n")

                # Export module terms.
                _export_module_terms(
                    module_to_path=module_to_path,
                    url=url,
                    database=database,
                    username=username,
                    password=password,
                )
                break

            if data.server_error:
                # The server encountered an error.
                print_error(data.error_msg, data.log_buffer.strip())
                break

        if proc.returncode:
            print_error(
                f"Running the Odoo server failed and exited with code: {proc.returncode}", data.log_buffer.strip(),
            )
            data.server_error = True
        else:
            print_header(f":raised_hand: Stop Odoo Server ({server_type.value})")
            proc.kill()
            print("Odoo Server has stopped :white_check_mark:\n")

    if data.database_created and data.server_error:
        print_warning(
            f"The database [b]{database}[/b] was not deleted to allow inspecting the error. "
            "You can delete it manually afterwards.",
        )
    elif data.database_created:
        try:
            subprocess.run(dropdb_cmd, env=env, capture_output=True, check=True)
            print(f"Database [b]{database}[/b] has been deleted :white_check_mark:\n")
        except CalledProcessError as e:
            print_error(
                f"Deleting database [b]{database}[/b] failed. You can try deleting it manually.", e.stderr.strip(),
            )




def _process_server_log_line(log_line: str, data: _LogLineData) -> bool:
    """Process an Odoo server log line and update the passed data.

    :param log_line: The log line to process.
    :type log_line: str
    :param data: The data needed to process the line and to be updated by this function.
    :type data: _LogLineData
    :return: `True` if the server is ready to export, `False` if not.
    :rtype: bool
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
        print(f"Database [b]{data.database}[/b] has been created :white_check_mark:")

    match = re.search(r"loading (\d+) modules", log_line)
    if match:
        data.log_buffer = ""
        if data.progress_task is None:
            data.progress_task = data.progress.add_task("Installing modules", total=None)
        else:
            data.progress.update(data.progress_task, total=int(match.group(1)))

    match = re.search(r"Loading module (\w+) \(\d+/\d+\)", log_line)
    if match:
        data.log_buffer = ""
        data.progress.update(
            data.progress_task,
            advance=1,
            description=f"Installing module [b]{match.group(1)}[/b]",
        )
    return False


def _export_module_terms(
    module_to_path: dict[str, Path],
    url: str,
    database: str,
    username: str,
    password: str,
) -> None:
    """Export .pot files for the given modules.

    :param module_to_path: A mapping from each module to its directory.
    :type module_to_path: dict[str, Path]
    :param url: The Odoo server URL to connect to.
    :type url: str
    :param database: The database name.
    :type database: str
    :param username: The Odoo username.
    :type username: str
    :param password: The Odoo password.
    :type password: str
    """
    print_header(":link: Access Odoo Server")

    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(database, username, password, {})
    print(f"Logged in as [b]{username}[/b] in database [b]{database}[/b] :white_check_mark:\n")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    print_header(":speech_balloon: Export Terms")

    modules = list(module_to_path.keys())
    if not modules:
        return

    # Export the terms.
    modules_to_export = sorted(
        models.execute_kw(
            database,
            uid,
            password,
            "ir.module.module",
            "search_read",
            [
                [["name", "in", modules], ["state", "=", "installed"]],
                ["name"],
            ],
        ),
        key=itemgetter("name"),
    )

    export_table = Table(box=None, pad_edge=False)

    for module in TransientProgress().track(modules_to_export, description="Exporting terms ..."):
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
        pot_file_content = b64decode(pot_file[0]["data"])

        module_name: str = module["name"]
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
        with contextlib.suppress(OSError, ValueError):
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
            continue

        export_table.add_row(
            f"[b]{module_name}[/b]",
            f"[d]{i18n_path}{os.sep}[/d][b]{module_name}.pot[/b] :white_check_mark: ({len(pot)} terms)",
        )

    print(export_table, "")
    print("Terms have been exported :white_check_mark:\n")


def _is_pot_file_empty(contents: bytes) -> bool:
    """Determine if the given .pot file's contents doesn't contain translatable terms."""
    in_msgid = False
    for line in map(str.strip, contents.decode().splitlines()):
        if line.startswith("msgid"):
            in_msgid = True
            if line != 'msgid ""':
                return False
        elif in_msgid:
            if line.startswith('"') and line != '""':
                return False
            in_msgid = False

    return True


def _get_modules_per_server_type(
    module_to_path: dict[str, Path],
    com_path: Path,
    ent_path: Path,
    full_install: bool = False,
) -> dict[_ServerType, tuple[set[str], set[str]]]:
    """Get all modules to export and install per server type.

    :param module_to_path: The modules to export, mapped to their directories.
    :type module_to_path: dict[str, :class:`pathlib.Path`]
    :param com_path: The path to the Odoo Community repository.
    :type com_path: :class:`pathlib.Path`
    :param ent_path: The path to the Odoo Enterprise repository.
    :type ent_path: :class:`pathlib.Path`
    :param full_install: Whether we want to install all modules before exporting, defaults to `False`.
    :type full_install: bool, optional
    :return: A mapping from each server type to a tuple containing the set of modules to export,
        and the set of modules to install.
    :rtype: dict[:class:`_ServerType`, tuple[set[str], set[str]]]
    """
    com_modules_path = com_path.expanduser().resolve() / "addons"
    ent_modules_path = ent_path.expanduser().resolve()

    modules_to_export = defaultdict(set)
    modules_to_install = defaultdict(set)

    # Determine all modules to export per server type.
    for m, p in module_to_path.items():
        if m == "base":
            modules_to_export[_ServerType.FULL_BASE].add(m)
        elif p.is_relative_to(ent_modules_path):
            modules_to_export[_ServerType.ENT_L10N if _is_l10n_module(m) else _ServerType.ENT].add(m)
        elif p.is_relative_to(com_modules_path):
            modules_to_export[_ServerType.COM_L10N if _is_l10n_module(m) else _ServerType.COM].add(m)

    # Determine all modules to install per server type.
    if full_install:
        modules_to_install = _get_full_install_modules_per_server_type(com_modules_path, ent_modules_path)
    else:
        # Some modules' .pot files contain terms generated by other modules.
        # In order to keep them, we define which modules contribute to the terms of another.
        contributors_per_module = {
            "account": {"account", "account_avatax", "point_of_sale", "pos_restaurant", "stock_account"},
        }
        for server_type in _ServerType:
            modules_to_install[server_type].update(
                c for m in modules_to_export[server_type] for c in contributors_per_module.get(m, {m})
            )

    return {
        server_type: (modules_to_export[server_type], modules_to_install[server_type]) for server_type in _ServerType
    }


def _get_full_install_modules_per_server_type(
    com_modules_path: Path,
    ent_modules_path: Path,
) -> dict[_ServerType, set[str]]:
    """Get all modules to install per server type for .pot export with `full_install = True`."""
    modules = defaultdict(set)

    for m in (f.parent.name for f in com_modules_path.glob("*/__manifest__.py")):
        # Add each Community module to the right server types.
        if _is_l10n_module(m):
            modules[_ServerType.COM_L10N].add(m)
        else:
            modules[_ServerType.COM].add(m)
        if _is_needed_for_base_export(m):
            modules[_ServerType.FULL_BASE].add(m)

    for m in (f.parent.name for f in ent_modules_path.glob("*/__manifest__.py")):
        # Add each Enterprise module to the right server types.
        if _is_l10n_module(m):
            modules[_ServerType.ENT_L10N].add(m)
        else:
            modules[_ServerType.ENT].add(m)
        if _is_needed_for_base_export(m):
            modules[_ServerType.FULL_BASE].add(m)

    modules[_ServerType.FULL_BASE].add("base")

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


def _is_needed_for_base_export(module: str) -> bool:
    """Determine if the given module should be installed to export base terms."""
    return "hw_" not in module and "test" not in module


def _is_l10n_module(module: str) -> bool:
    """Determine if the given module is a localization module."""
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
    return None
