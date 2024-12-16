import os
import re
import subprocess
import xmlrpc.client
from base64 import b64decode
from enum import Enum
from operator import itemgetter
from pathlib import Path
from socket import socket
from subprocess import PIPE, CalledProcessError, Popen
from typing import Annotated

from rich.table import Table
from typer import Argument, Option

from .common import TransientProgress, app, print, print_command_title, print_error, print_header, print_warning


class OdooServerType(str, Enum):
    COMMUNITY = "Community"
    COMMUNITY_L10N = "Community Localizations"
    ENTERPRISE = "Enterprise"
    ENTERPRISE_L10N = "Enterprise Localizations"
    FULL_BASE = "Full Base"


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
    ] = "admin",
    host: Annotated[
        str, Option(help="Specify the hostname of your Odoo server.", rich_help_panel="Odoo Server Options")
    ] = "localhost",
    port: Annotated[
        int, Option(help="Specify the port of your Odoo server.", rich_help_panel="Odoo Server Options")
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
        str, Option(help="Specify the PostgreSQL server's hostname.", rich_help_panel="Database Options")
    ] = "localhost",
    db_port: Annotated[
        int, Option(help="Specify the PostgreSQL server's port.", rich_help_panel="Database Options")
    ] = 5432,
    db_username: Annotated[
        str, Option(help="Specify the PostgreSQL server's username.", rich_help_panel="Database Options")
    ] = "",
    db_password: Annotated[
        str, Option(help="Specify the PostgreSQL user's password.", rich_help_panel="Database Options")
    ] = "",
):
    """
    Export Odoo translation files (.pot) to each module's i18n folder.

    With the default settings, it will start an Odoo server for Community and Enterprise terms separately and install
    the modules to export in the corresponding server. Some Community modules require extra Enterprise modules to be
    installed that override some terms. These modules will be exported from the Enterprise server as well with the
    extra modules installed.

    When exporting "base", it will install all modules in Community and Enterprise to have the terms from their
    manifest files exported in there.

    If you want to export from your own running server, you can provide the corresponding options to the command.
    """
    print_command_title(":outbox_tray: Odoo POT Export")

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
    modules_for_transifex = {m for m in all_modules if exportable_for_transifex(m)}

    # Some modules' POT files contain terms generated by other modules.
    # In order to keep them, we define which modules contribute to the terms of another.
    modules_to_contributors_mapping = {
        "account": {"account", "account_avatax", "point_of_sale", "pos_restaurant", "stock_account"},
    }

    def map_modules_to_paths(modules: set[str]) -> dict[str, Path]:
        """
        Map the given modules to their containing directories using a dictionary.
        """
        return {
            module: path
            for modules, path in [
                ({"base"} & modules, base_module_path),
                (com_modules & modules, com_modules_path),
                (ent_modules & modules, ent_modules_path),
            ]
            for module in modules
        }

    def get_modules_per_server_type(
        modules: set[str], server_type: OdooServerType = None
    ) -> dict[OdooServerType, set[str]] | set[str]:
        """
        Get the provided modules according to the Odoo server type they should be exported from.

        :param modules: The modules to split per Odoo server type
        :type modules: set[str]
        :param server_type: The Odoo server type to get the modules for, defaults to None
        :type server_type: OdooServerType, optional
        :return: A mapping between the Odoo server type and the modules, or the modules according to the given Odoo
            server type
        :rtype: dict[OdooServerType, set[str]] | set[str]
        """
        com_server_modules = modules & com_modules_no_l10n
        com_l10n_server_modules = modules & com_modules_l10n
        ent_server_modules = modules & ent_modules_no_l10n
        ent_l10n_server_modules = modules & ent_modules_l10n
        server_to_modules_mapping = {
            OdooServerType.COMMUNITY: com_server_modules,
            OdooServerType.COMMUNITY_L10N: com_l10n_server_modules,
            OdooServerType.ENTERPRISE: ent_server_modules,
            OdooServerType.ENTERPRISE_L10N: ent_l10n_server_modules,
            OdooServerType.FULL_BASE: all_modules if "base" in modules else {},
        }
        if server_type:
            return server_to_modules_mapping[server_type]
        return server_to_modules_mapping

    # Determine all modules to export.
    if len(modules) == 1 and modules[0] == "all":
        modules_to_export = modules_for_transifex
    elif len(modules) == 1 and modules[0] == "community":
        modules_to_export = modules_for_transifex & com_modules
    elif len(modules) == 1 and modules[0] == "enterprise":
        modules_to_export = modules_for_transifex & ent_modules
    elif len(modules) == 1:
        modules_to_export = set(modules[0].split(",")) & all_modules
    else:
        modules_to_export = {re.sub(r",", "", m) for m in modules if m in all_modules}

    if not modules_to_export:
        print_error("The provided modules are not available! Nothing to export ...\n")
        return

    if full_install:
        modules_to_install = modules_for_transifex | modules_to_export
    else:
        modules_to_install = {
            contributor
            for module in modules_to_export
            for contributor in modules_to_contributors_mapping.get(module, {module})
        }

    print(f"Modules to export: [b]{'[/b], [b]'.join(sorted(modules_to_export))}[/b]\n")

    # Determine the URL to connect to our Odoo server.
    host = "localhost" if start_server else host
    port = free_port(host, port) if start_server else port
    url = "{protocol}{host}:{port}".format(
        protocol="" if "://" in host else "http://" if port != 443 else "https://",
        host=host,
        port=port,
    )

    if start_server:
        # Start a temporary Odoo server to export the terms.
        addons_path = str(com_modules_path)
        odoo_bin_path = com_path.expanduser().resolve() / "odoo-bin"

        for server_type, modules in get_modules_per_server_type(modules_to_export).items():
            if not modules:
                continue

            print_header(f":rocket: Start Odoo Server ({server_type.value})")

            database_created = False
            server_error = False
            if server_type in (OdooServerType.ENTERPRISE, OdooServerType.ENTERPRISE_L10N, OdooServerType.FULL_BASE):
                addons_path = f"{ent_modules_path},{addons_path}"

            odoo_cmd = [
                "python3",
                "-u",
                odoo_bin_path,
                "--addons-path",
                addons_path,
                "-d",
                database,
                "-i",
                ",".join(get_modules_per_server_type(modules_to_install, server_type)),
                "--http-port",
                str(port),
                "--db_host",
                db_host,
                "--db_port",
                str(db_port),
            ]
            if db_username:
                odoo_cmd.extend(["--db_user", db_username])
            if db_password:
                odoo_cmd.extend(["--db_password", db_password])

            with Popen(odoo_cmd, stderr=PIPE, text=True) as p, TransientProgress() as progress:
                # Run the Odoo server.
                log_buffer = ""
                progress_task = None
                while p.poll() is None:
                    log_line = p.stderr.readline()
                    log_buffer += log_line

                    if "odoo.modules.loading: init db" in log_line:
                        log_buffer = ""
                        database_created = True
                        print(f"Database [b]{database}[/b] has been created :white_check_mark:")
                        continue

                    if match := re.search(r"odoo\.modules\.loading: loading (\d+) modules", log_line):
                        log_buffer = ""
                        total = int(match.group(1))
                        if progress_task is None:
                            # First module loaded is base. We don't want to display a total of 1 yet.
                            print("Installing required modules ...")
                            progress_task = progress.add_task("Installing modules", total=None)
                        else:
                            progress.update(progress_task, total=total)
                        continue

                    if match := re.search(r"odoo\.modules\.loading: Loading module (\w+) \(\d+/\d+\)", log_line):
                        log_buffer = ""
                        module_name = match.group(1)
                        progress.update(
                            progress_task,
                            advance=1,
                            description=f"Installing module [b]{module_name}[/b]\n",
                        )
                        continue

                    if re.search(r"odoo\.(modules\.)?registry: Failed to load registry", log_line):
                        print_error("An error occurred during loading! Terminating the process ...", log_buffer.strip())
                        server_error = True
                        break

                    if "odoo.sql_db: Connection to the database failed" in log_line:
                        print_error(
                            "Could not connect to the database! Terminating the process ...", log_buffer.strip()
                        )
                        server_error = True
                        break

                    if "odoo.modules.loading: Modules loaded." in log_line:
                        # Close the pipe to prevent overfilling the buffer and blocking the process.
                        p.stderr.close()

                        progress.update(progress_task, description="Installing modules")
                        progress.stop()
                        print("Modules have been installed :white_check_mark:")
                        print("Odoo Server has started :white_check_mark:\n")

                        # Export module terms.
                        export_module_terms(
                            modules_to_path_mapping=map_modules_to_paths(modules),
                            url=url,
                            database=database,
                            username=username,
                            password=password,
                        )
                        break

                if p.returncode:
                    print_error(
                        f"Running the Odoo server failed and exited with code: {p.returncode}", log_buffer.strip()
                    )
                    server_error = True
                else:
                    print_header(f":raised_hand: Stop Odoo Server ({server_type.value})")
                    p.kill()
                    print("Odoo Server has stopped :white_check_mark:\n")

            if database_created and server_error:
                print_warning(
                    f"The database [b]{database}[/b] was not deleting to allow inspecting the error. You can delete it manually afterwards."
                )
            elif database_created:
                dropdb_cmd = ["dropdb", database, "--host", db_host, "--port", str(db_port)]
                cmd_env = os.environ
                if db_username:
                    dropdb_cmd.extend(["--username", db_username])
                if db_password:
                    cmd_env |= {"PGPASSWORD": db_password}

                try:
                    subprocess.run(dropdb_cmd, env=cmd_env, capture_output=True, check=True)
                    print(f"Database [b]{database}[/b] has been deleted :white_check_mark:\n")
                except CalledProcessError as e:
                    print_error(
                        f"Deleting database [b]{database}[/b] failed. You can try deleting it manually.",
                        e.stderr.strip(),
                    )

    else:
        # Export from a running server.
        export_module_terms(
            modules_to_pat_mapping=map_modules_to_paths(modules_to_export),
            url=url,
            database=database,
            username=username,
            password=password,
        )


def export_module_terms(
    modules_to_path_mapping: dict[str, Path],
    url: str,
    database: str,
    username: str,
    password: str,
):
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

            if is_pot_file_empty(pot_file_content):
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


def free_port(host: str, start_port: int) -> int:
    """
    Find the first free port on the host starting from the provided port.
    """
    for port in range(start_port, 65536):
        with socket() as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    return None


def exportable_for_transifex(module: str) -> bool:
    """
    Determine if the given module should be exported for Transifex.
    """
    return (
        ("l10n_" not in module or module == "l10n_multilang")
        and "theme_" not in module
        and "hw_" not in module
        and "test" not in module
        and "pos_blackbox_be" not in module
    )


def is_pot_file_empty(contents: bytes) -> bool:
    """
    Determine if the given POT file's contents doesn't contains translatable terms.
    """
    for line in contents.decode().split("\n"):
        line = line.strip()
        if line.startswith("msgid") and line != 'msgid ""':
            return False
    return True
