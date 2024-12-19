import os
import re
import subprocess
import xmlrpc.client
from base64 import b64decode
from dataclasses import dataclass
from enum import Enum
from operator import itemgetter
from pathlib import Path
from socket import socket
from subprocess import PIPE, CalledProcessError, Popen
from typing import Annotated

from rich.progress import TaskID
from rich.table import Table
from typer import Argument, Exit, Option

from .common import (
    TransientProgress,
    app,
    print,
    print_command_title,
    print_error,
    print_header,
    print_warning,
)

HTTPS_PORT = 443


class _OdooServerType(str, Enum):
    COMMUNITY = "Community"
    COMMUNITY_L10N = "Community Localizations"
    ENTERPRISE = "Enterprise"
    ENTERPRISE_L10N = "Enterprise Localizations"
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
def export_pot(
    modules: Annotated[
        list[str],
        Argument(help='Export .pot files for these Odoo modules, or either "all", "community", or "enterprise".'),
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
    modules with only the modules installed to be exported in that version.
    \n\n
    When exporting the translations for `base`, we install all possible modules to ensure all terms added in by other
    modules get exported in the `base.pot` files as well.
    \n\n
    You can also export terms from your own running server using the `--no-start-server` option and optionally passing
    the correct arguments to reach your Odoo server.
    \n\n
    > Without any options specified, the command is supposed to run from within the parent directory where your `odoo`
    and `enterprise` repositories are checked out with these names. Your database is supposed to run on `localhost`
    using port `5432`, accessible without a password using your current user.
    \n\n
    > Of course, all of this can be tweaked with the available options.
    """
    print_command_title(":outbox_tray: Odoo POT Export")

    com_modules_path = com_path.expanduser().resolve() / "addons"
    ent_modules_path = ent_path.expanduser().resolve()

    modules_per_server_type, modules_to_path_mapping = _get_modules_to_install_and_export_per_server_type(
        modules=modules,
        com_path=com_path,
        ent_path=ent_path,
        full_install=full_install,
    )
    valid_modules_to_export = modules_to_path_mapping.keys()

    if not valid_modules_to_export:
        print_error("The provided modules are not available! Nothing to export ...\n")
        raise Exit

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

        for server_type, (modules_to_install, modules_to_export) in modules_per_server_type.items():
            if not modules_to_export:
                continue

            if server_type in (_OdooServerType.ENTERPRISE, _OdooServerType.ENTERPRISE_L10N, _OdooServerType.FULL_BASE):
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

            current_modules_to_path_mapping = {
                k: v for k, v in modules_to_path_mapping.items() if k in modules_to_export
            }
            _run_server_and_export_terms(
                server_type=server_type,
                odoo_cmd=odoo_cmd,
                dropdb_cmd=dropdb_cmd,
                env=cmd_env,
                url=url,
                database=database,
                username=username,
                password=password,
                modules_to_path_mapping=current_modules_to_path_mapping,
            )

    else:
        # Export from a running server.
        _export_module_terms(
            modules_to_path_mapping={k: v for k, v in modules_to_path_mapping.items() if k in valid_modules_to_export},
            url=url,
            database=database,
            username=username,
            password=password,
        )


def _run_server_and_export_terms(
    server_type: _OdooServerType,
    odoo_cmd: list[str],
    dropdb_cmd: list[str],
    env: dict[str, str],
    url: str,
    database: str,
    username: str,
    password: str,
    modules_to_path_mapping: dict[str, Path],
) -> None:
    """Start an Odoo server and export .pot files for the given modules.

    :param server_type: The server type to run
    :type server_type: _OdooServerType
    :param odoo_cmd: The command to start the Odoo server
    :type odoo_cmd: list[str]
    :param dropdb_cmd: The command to drop the database
    :type dropdb_cmd: list[str]
    :param env: The environment variabled to run commands with
    :type env: dict[str, str]
    :param url: The Odoo server URL
    :type url: str
    :param database: The database name
    :type database: str
    :param username: The Odoo username
    :type username: str
    :param password: The Odoo password
    :type password: str
    :param modules_to_path_mapping: A mapping from each module to export to its addons path
    :type modules_to_path_mapping: dict[str, Path]
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

    with Popen(odoo_cmd, env=env, stderr=PIPE, text=True) as p, TransientProgress() as progress:
        data.progress = progress
        while p.poll() is None:
            log_line = p.stderr.readline()
            data.log_buffer += log_line

            if _process_server_log_line(log_line=log_line, data=data):
                # The server is ready to export.

                # Close the pipe to prevent overfilling the buffer and blocking the process.
                p.stderr.close()

                # Stop the progress.
                progress.update(data.progress_task, description="Installing modules")
                progress.stop()
                print("Modules have been installed :white_check_mark:")
                print("Odoo Server has started :white_check_mark:\n")

                # Export module terms.
                _export_module_terms(
                    modules_to_path_mapping=modules_to_path_mapping,
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

        if p.returncode:
            print_error(f"Running the Odoo server failed and exited with code: {p.returncode}", data.log_buffer.strip())
            data.server_error = True
        else:
            print_header(f":raised_hand: Stop Odoo Server ({server_type.value})")
            p.kill()
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

    :param log_line: The log line to process
    :type log_line: str
    :param data: The data needed to process the line and to be updated by this function
    :type data: _LogLineData
    :return: `True` if the server is ready to export, `False` if not
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
    modules_to_path_mapping: dict[str, Path],
    url: str,
    database: str,
    username: str,
    password: str,
) -> None:
    """Export .pot files for the given modules.

    :param modules_to_path_mapping: A mapping from each module to its addons path
    :type modules_to_path_mapping: dict[str, Path]
    :param url: The Odoo server URL to connect to
    :type url: str
    :param database: The database name
    :type database: str
    :param username: The Odoo username
    :type username: str
    :param password: The Odoo password
    :type password: str
    """
    print_header(":link: Access Odoo Server")

    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(database, username, password, {})
    print(f"Logged in as [b]{username}[/b] in database [b]{database}[/b] :white_check_mark:\n")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    print_header(":speech_balloon: Export Terms")

    modules = list(modules_to_path_mapping.keys())
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

    with TransientProgress() as progress:
        progress_task = progress.add_task("Exporting terms ...", total=len(modules_to_export))
        for module in modules_to_export:
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
            # Export the POT file.
            models.execute_kw(
                database,
                uid,
                password,
                "base.language.export",
                "act_getfile",
                [[export_id]],
            )
            # Get the exported POT file.
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
            i18n_path = modules_to_path_mapping[module_name] / module_name / "i18n"
            if not i18n_path.exists():
                i18n_path.mkdir()
            pot_path = i18n_path / f"{module_name}.pot"

            if _is_pot_file_empty(pot_file_content):
                if pot_path.is_file():
                    # Remove empty POT files.
                    pot_path.unlink()
                    export_table.add_row(
                        f"[b]{module_name}[/b]",
                        f"[d]Removed empty[/d] [b]{module_name}.pot[/b] :negative_squared_cross_mark:",
                    )
                else:
                    export_table.add_row(
                        f"[b]{module_name}[/b]",
                        "[d]No terms to translate[/d] :negative_squared_cross_mark:",
                    )
            else:
                pot_path.write_bytes(pot_file_content)
                export_table.add_row(
                    f"[b]{module_name}[/b]",
                    f"[d]{i18n_path}{os.sep}[/d][b]{module_name}.pot[/b] :white_check_mark:",
                )
            progress.update(progress_task, advance=1)

    print(export_table, "")
    print("Terms have been exported :white_check_mark:\n")


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


def _installable_for_base(module: str) -> bool:
    """Determine if the given module should be installed to export base terms."""
    return "hw_" not in module and "test" not in module


def _exportable_for_transifex(module: str) -> bool:
    """Determine if the given module should be exported for Transifex."""
    return (
        ("l10n_" not in module or module == "l10n_multilang")
        and "theme_" not in module
        and "hw_" not in module
        and "test" not in module
        and "pos_blackbox_be" not in module
    )


def _is_pot_file_empty(contents: bytes) -> bool:
    """Determine if the given POT file's contents doesn't contains translatable terms."""
    for pot_line in contents.decode().split("\n"):
        line = pot_line.strip()
        if line.startswith("msgid") and line != 'msgid ""':
            return False
    return True


def _get_modules_to_install_and_export_per_server_type(
    modules: list[str],
    com_path: Path,
    ent_path: Path,
    full_install: bool = False,
) -> tuple[dict[_OdooServerType, tuple[list[str], list[str]]], dict[str, Path]]:
    """Find out what modules to install and export per server type.

    :param modules: The requested modules to export
    :type modules: list[str]
    :param com_path: The path to the Odoo Community repository
    :type com_path: Path
    :param ent_path: The path to the Odoo Enterprise repository
    :type ent_path: Path
    :param full_install: Whether we want to install all modules before export, defaults to False
    :type full_install: bool, optional
    :return: A tuple containing:
        - A mapping from the server type to a tuple of modules to install, and modules to export
        - A mapping from modules to export to their addons path
    :rtype: tuple[dict[_OdooServerType, tuple[list[str], list[str]]], dict[str, Path]]
    """
    base_module_path = com_path.expanduser().resolve() / "odoo" / "addons"
    com_modules_path = com_path.expanduser().resolve() / "addons"
    ent_modules_path = ent_path.expanduser().resolve()

    com_modules = {f.parent.name for f in com_modules_path.glob("*/__manifest__.py")}
    com_modules_l10n = {m for m in com_modules if "l10n_" in m and m != "l10n_multilang"}
    com_modules_no_l10n = com_modules - com_modules_l10n
    ent_modules = {f.parent.name for f in ent_modules_path.glob("*/__manifest__.py")}
    ent_modules_l10n = {m for m in ent_modules if "l10n_" in m}
    ent_modules_no_l10n = ent_modules - ent_modules_l10n
    all_modules = {"base"} | com_modules | ent_modules
    modules_for_base = {m for m in all_modules if _installable_for_base(m)}
    modules_for_transifex = {m for m in all_modules if _exportable_for_transifex(m)}

    # Determine all modules to export.
    if len(modules) == 1:
        match modules[0]:
            case "all":
                modules_to_export = modules_for_transifex
            case "community":
                modules_to_export = ({"base"} | com_modules) & modules_for_transifex
            case "enterprise":
                modules_to_export = ent_modules & modules_for_transifex
            case _:
                modules_to_export = set(modules[0].split(",")) & all_modules
    else:
        modules_to_export = {re.sub(r",", "", m) for m in modules if m in all_modules}

    # Some modules' .pot files contain terms generated by other modules.
    # In order to keep them, we define which modules contribute to the terms of another.
    modules_to_contributors_mapping = {
        "account": {"account", "account_avatax", "point_of_sale", "pos_restaurant", "stock_account"},
    }
    modules_to_install = {
        contributor
        for module in modules_to_export
        for contributor in modules_to_contributors_mapping.get(module, {module})
    }

    # Map each server type to its modules to install and export.
    modules_per_server_type = {
        _OdooServerType.COMMUNITY: (
            com_modules_no_l10n if full_install else modules_to_install & com_modules_no_l10n,
            modules_to_export & com_modules_no_l10n,
        ),
        _OdooServerType.COMMUNITY_L10N: (
            com_modules_l10n if full_install else modules_to_install & com_modules_l10n,
            modules_to_export & com_modules_l10n,
        ),
        _OdooServerType.ENTERPRISE: (
            ent_modules_no_l10n if full_install else modules_to_install & ent_modules_no_l10n,
            modules_to_export & ent_modules_no_l10n,
        ),
        _OdooServerType.ENTERPRISE_L10N: (
            ent_modules_l10n if full_install else modules_to_install & ent_modules_l10n,
            modules_to_export & ent_modules_l10n,
        ),
        _OdooServerType.FULL_BASE: (
            modules_for_base if "base" in modules_to_export else {},
            {"base"} & modules_to_export,
        ),
    }

    # Map each module to export to its addons directory.
    modules_to_path_mapping = {
        module: path
        for scoped_modules, path in [
            ({"base"} & modules_to_export, base_module_path),
            (com_modules & modules_to_export, com_modules_path),
            (ent_modules & modules_to_export, ent_modules_path),
        ]
        for module in scoped_modules
    }

    return modules_per_server_type, modules_to_path_mapping
