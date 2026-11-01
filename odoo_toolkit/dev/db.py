import subprocess
from typing import Annotated

from typer import Option, Typer

from odoo_toolkit.common import (
    console,
    print_command_title,
    print_panel,
    print_success,
)

from .common import DEFAULT_DB_PORT, get_db_port, start_services
from .image_check import notify_outdated_images

app = Typer()

# The PostgreSQL volume of odoo-toolkit 3.x, which PostgreSQL 18 can't use.
OLD_DB_VOLUME = "odoo-postgres-data"


@app.command()
def db(
    port: Annotated[
        int | None,
        Option(
            "--port",
            "-p",
            help="The port to reach PostgreSQL on, on your machine. Defaults to the one it runs on, or `5432`. Changing "
            "it restarts PostgreSQL.",
        ),
    ] = None,
    pgadmin: Annotated[bool, Option("--pgadmin", help="Start pgAdmin as well, on `http://localhost:5050`.")] = False,
    pull: Annotated[
        bool,
        Option(
            "--pull",
            help="Pull newer builds of the images first. A service whose image changed is recreated, which keeps its "
            "data.",
        ),
    ] = False,
) -> None:
    """Start :elephant: PostgreSQL and Mailpit, the services Odoo uses, without starting Odoo.

    `otk dev run` starts them by itself. Use this command to connect to the databases with your own tools, to run an
    Odoo directly on your machine (with `--db_host=localhost --db_user=odoo`), or to use pgAdmin.
    """
    print_command_title(":elephant: Odoo Services")
    start_services(db_port=port, pgadmin=pgadmin, pull=pull)
    port = get_db_port() or DEFAULT_DB_PORT
    print_success("PostgreSQL and Mailpit are running.\n")
    if not pull:
        notify_outdated_images()
    print_panel(
        f"PostgreSQL: [b]localhost:{port}[/b], user [b]odoo[/b], no password needed\n"
        "Mailpit: [b]http://localhost:8025[/b]" + ("\npgAdmin: [b]http://localhost:5050[/b]" if pgadmin else ""),
        "Connection Details",
    )
    console.print(
        "\nTo connect without options, from an Odoo on your machine as well as from tools like [b]psql[/b], add this to "
        "your shell profile (like [u]~/.zshrc[/u]):\n"
        f"  [b]export PGHOST=localhost PGUSER=odoo{'' if port == DEFAULT_DB_PORT else f' PGPORT={port}'}[/b]",
    )
    volumes = subprocess.run(
        ["docker", "volume", "ls", "--format", "{{.Name}}"], capture_output=True, text=True, check=False
    )
    if OLD_DB_VOLUME in volumes.stdout.split():
        console.print(
            f"\nThe databases of odoo-toolkit 3.x (PostgreSQL 17) are still in the [b]{OLD_DB_VOLUME}[/b] volume. "
            f"Once you don't need them anymore, remove it with [b]docker volume rm {OLD_DB_VOLUME}[/b].",
        )
    console.print()
