import os
import re
import subprocess
import xmlrpc.client
from base64 import b64decode
from operator import itemgetter
from pathlib import Path
from socket import socket
from subprocess import PIPE, CalledProcessError, Popen
from typing import Annotated

from rich.panel import Panel
from rich.progress import Progress
from rich.table import Table
from typer import Argument, Option

from .common import PROGRESS_COLUMNS, app, log, logger


@app.command()
def export_pot(
    modules: Annotated[
        list[str], Argument(help='The Odoo modules to export or either "all", "community", or "enterprise"')
    ],
    start_server: Annotated[
        bool,
        Option(help="Start an Odoo server automatically", rich_help_panel="Odoo Server Options"),
    ] = True,
    com_path: Annotated[
        Path,
        Option(
            "--com-path",
            "-c",
            help="The path to the Odoo Community repo",
            rich_help_panel="Odoo Server Options",
        ),
    ] = Path("odoo"),
    ent_path: Annotated[
        Path,
        Option(
            "--ent-path",
            "-e",
            help="The path to the Odoo Enterprise repo",
            rich_help_panel="Odoo Server Options",
        ),
    ] = Path("enterprise"),
    username: Annotated[
        str,
        Option(
            "--username",
            "-u",
            help="The Odoo username",
            rich_help_panel="Odoo Server Options",
        ),
    ] = "admin",
    password: Annotated[
        str,
        Option(
            "--password",
            "-p",
            help="The Odoo password",
            rich_help_panel="Odoo Server Options",
        ),
    ] = "admin",
    host: Annotated[str, Option(help="The Odoo hostname", rich_help_panel="Odoo Server Options")] = "localhost",
    port: Annotated[int, Option(help="The Odoo port", rich_help_panel="Odoo Server Options")] = 8069,
    database: Annotated[
        str,
        Option(
            "--database",
            "-d",
            help="The PostgreSQL database",
            rich_help_panel="Database Options",
        ),
    ] = "__export_pot_db__",
    db_host: Annotated[str, Option(help="The PostgreSQL hostname", rich_help_panel="Database Options")] = "localhost",
    db_port: Annotated[int, Option(help="The PostgreSQL port", rich_help_panel="Database Options")] = 5432,
    db_username: Annotated[str, Option(help="The PostgreSQL username", rich_help_panel="Database Options")] = "",
    db_password: Annotated[str, Option(help="The PostgreSQL password", rich_help_panel="Database Options")] = "",
):
    """
    Export Odoo translation files (.pot) to each module's i18n folder.

    With the default settings, it will start a new Odoo Enterprise server and install all modules in order to export
    all possible terms that can come from other modules in the module(s) you want to export.

    If you don't want this behavior, start an Odoo server manually and provide the corresponding options to the
    command.
    """
    log(
        Panel.fit(
            ":outbox_tray: Odoo POT Export",
            style="bold magenta",
            border_style="bold magenta",
        ),
        "",
    )

    # Compute the paths to all Odoo modules.
    base_modules_path = com_path.expanduser().resolve() / "odoo" / "addons"
    com_modules_path = com_path.expanduser().resolve() / "addons"
    ent_modules_path = ent_path.expanduser().resolve()

    # Determine all modules belonging to base, community or enterprise.
    base_modules = {f.parent.name for f in base_modules_path.glob("*/__manifest__.py")}
    com_modules = {f.parent.name for f in com_modules_path.glob("*/__manifest__.py")}
    ent_modules = {f.parent.name for f in ent_modules_path.glob("*/__manifest__.py")}
    all_modules = base_modules | com_modules | ent_modules

    # Determine whether a module should be installed by default.
    def installed_by_default(module: str) -> bool:
        return (
            ("l10n_" not in module or module == "l10n_multilang")
            and "theme_" not in module
            and "hw_" not in module
            and "test" not in module
            and "ldap" not in module
            and "pos_blackbox_be" not in module
        )

    modules_to_install = {m for m in all_modules if installed_by_default(m)}

    # Prepare values if we're starting a server.
    if start_server:
        # If we're starting a server, the host is always localhost.
        host = "localhost"
        # Check if the given port is free or try to find another free one.
        with socket() as sock:
            while sock.connect_ex((host, port)) == 0:
                port += 1

    # Determine the URL to connect to our Odoo server.
    url = "{protocol}{host}:{port}".format(
        protocol="" if "://" in host else "http://" if port != 443 else "https://",
        host=host,
        port=port,
    )

    # Determine all modules to export.
    if len(modules) == 1 and modules[0] == "all":
        modules_to_export = modules_to_install
    elif len(modules) == 1 and modules[0] == "community":
        modules_to_export = modules_to_install & (base_modules | com_modules)
    elif len(modules) == 1 and modules[0] == "enterprise":
        modules_to_export = modules_to_install & ent_modules
    elif len(modules) == 1:
        modules_to_export = set(modules[0].split(",")) & all_modules
    else:
        modules_to_export = {re.sub(r",", "", m) for m in modules if m in all_modules}

    if not modules_to_export:
        log(":exclamation_mark: [red]The provided modules are not available! Nothing to export ...\n")
        return

    log(f"Modules to export: [bold]{'[/bold], [bold]'.join(sorted(modules_to_export))}[/bold]\n")

    # Map each module to its directory.
    modules_to_path_mapping = {
        module: path
        for modules, path in [
            (base_modules & modules_to_export, base_modules_path),
            (com_modules & modules_to_export, com_modules_path),
            (ent_modules & modules_to_export, ent_modules_path),
        ]
        for module in modules
    }

    if start_server:
        # Start a temporary Odoo server to export the terms.
        database_created = False
        addons_path = f"{ent_modules_path},{com_modules_path}"
        odoo_bin_path = com_path.expanduser().resolve() / "odoo-bin"

        log(
            Panel.fit(":rocket: [bold]Start Odoo Server[/bold]"),
            "",
        )

        odoo_cmd = [
            "python3",
            "-u",
            odoo_bin_path,
            "--addons-path",
            addons_path,
            "-d",
            database,
            "-i",
            ",".join(modules_to_install | modules_to_export),
            "--xmlrpc-port",
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

        with Popen(odoo_cmd, stderr=PIPE, text=True) as p, Progress(*PROGRESS_COLUMNS, console=logger, transient=True) as progress:
            # Run the Odoo server.
            log_buffer = ""
            task = None
            while p.poll() is None:
                log_line = p.stderr.readline()
                log_buffer += log_line

                if "odoo.modules.loading: init db" in log_line:
                    log_buffer = ""
                    database_created = True
                    log(f"Database [bold]{database}[/bold] has been created :white_check_mark:")
                    continue

                if match := re.search(r"odoo\.modules\.loading: loading (\d+) modules", log_line):
                    log_buffer = ""
                    total = int(match.group(1))
                    if task is None:
                        # First module loaded is base. We don't want to display a total yet.
                        log("Installing all modules ...")
                        task = progress.add_task(description="Installing modules\n", total=None)
                    else:
                        progress.update(task, total=total)
                    continue

                if match := re.search(r"odoo\.modules\.loading: Loading module (\w+) \(\d+/\d+\)", log_line):
                    log_buffer = ""
                    module_name = match.group(1)
                    progress.update(
                        task,
                        advance=1,
                        description=f"Installing module [bold]{module_name}[/bold]\n",
                    )
                    continue

                if re.search(r"odoo\.(modules\.)?registry: Failed to load registry", log_line):
                    log(":exclamation_mark: [red]An error occurred during loading! Terminating the process ...\n")
                    log(
                        Panel(
                            log_buffer.strip(),
                            title="Error Log",
                            title_align="left",
                            style="red",
                            border_style="bold red",
                        ),
                    )
                    break

                if "odoo.sql_db: Connection to the database failed" in log_line:
                    log(":exclamation_mark: [red]Could not connect to the database! Terminating the process ...\n")
                    log(
                        Panel(
                            log_buffer.strip(),
                            title="Error Log",
                            title_align="left",
                            style="red",
                            border_style="bold red",
                        ),
                    )
                    break

                if "odoo.modules.loading: Modules loaded." in log_line:
                    # Close the pipe to prevent overfilling the buffer and blocking the process.
                    p.stderr.close()

                    progress.update(task, description="Installing modules")
                    progress.stop()
                    log("Modules have been installed :white_check_mark:")
                    log("Odoo Server has started :white_check_mark:\n")

                    # Export module terms.
                    export_module_terms(
                        modules_to_path_mapping=modules_to_path_mapping,
                        url=url,
                        database=database,
                        username=username,
                        password=password,
                    )
                    break

            if p.returncode:
                log(f":exclamation_mark: [red]Running the Odoo server failed and exited with code: {p.returncode}\n")
                log(
                    Panel(
                        log_buffer.strip(),
                        title="Error Log",
                        title_align="left",
                        style="red",
                        border_style="bold red",
                    ),
                )
            else:
                log(
                    Panel.fit(":raised_hand: [bold]Stop Odoo Server[/bold]"),
                    "",
                )
                p.kill()
                log("Odoo Server has stopped :white_check_mark:")

        if database_created:
            dropdb_cmd = [
                "dropdb",
                database,
                "--host",
                db_host,
                "--port",
                str(db_port),
            ]
            cmd_env = os.environ
            if db_username:
                dropdb_cmd.extend(["--username", db_username])
            if db_password:
                cmd_env |= {"PGPASSWORD": db_password}

            try:
                subprocess.run(dropdb_cmd, env=cmd_env, capture_output=True, check=True)
                log(f"Database [bold]{database}[/bold] has been deleted :white_check_mark:\n")
            except CalledProcessError as error:
                log(
                    f":exclamation_mark: [red]Deleting database [bold]{database}[/bold] failed. You can try deleting it manually.[/red]"
                )
                log(
                    Panel(
                        error.stderr.strip(),
                        title="Error Log",
                        title_align="left",
                        style="red",
                        border_style="bold red",
                    ),
                )

    else:
        # Export from a running server.
        export_module_terms(
            modules_to_pat_mapping=modules_to_path_mapping,
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
) -> None:
    log(
        Panel.fit(":link: [bold]Access Odoo Server"),
        "",
    )
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(database, username, password, {})
    log(f"Logged in as [bold]{username}[/bold] in database [bold]{database}[/bold] :white_check_mark:\n")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    log(Panel.fit(":speech_balloon: [bold]Export Terms[/bold]"))
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

    with Progress(*PROGRESS_COLUMNS, console=logger, transient=True) as progress:
        task = progress.add_task("Exporting terms ...", total=len(modules_to_export))
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
            module_name = module["name"]
            i18n_path = modules_to_path_mapping[module_name] / module_name / "i18n"
            if not i18n_path.exists():
                i18n_path.mkdir()
            pot_path = i18n_path / f"{module_name}.pot"
            pot_path.write_bytes(pot_file_content)

            progress.update(task, advance=1)
            export_table.add_row(
                f"[bold]{module_name}",
                f"[dim]{i18n_path}{os.sep}[/dim][bold]{module_name}.pot[/bold] :white_check_mark:",
            )

    log(export_table, "")
    log("Terms have been exported :white_check_mark:\n")
