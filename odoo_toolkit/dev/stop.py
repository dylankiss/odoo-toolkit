import subprocess
from pathlib import Path
from typing import Annotated

from typer import Option, Typer

from odoo_toolkit.common import print, print_command_title, print_success
from odoo_toolkit.mv.config import find_version_dir

from .common import get_run_containers, stop_services

app = Typer()


@app.command()
def stop(
    include_services: Annotated[
        bool, Option("--all", help="Stop every Odoo container, and PostgreSQL, Mailpit and pgAdmin as well."),
    ] = False,
) -> None:
    """Stop :stop_sign: the Odoo containers started with `otk dev run`.

    Inside a multiverse version directory, this stops the Odoo containers of that version. Elsewhere, it stops all of
    them. Your databases and filestores stay, so you can continue where you left off.
    """
    print_command_title(":stop_sign: Stop Odoo")
    version_dir = find_version_dir(Path.cwd())
    version = None if include_services or not version_dir else version_dir.name
    if containers := get_run_containers(version):
        subprocess.run(["docker", "stop", *containers], capture_output=True, check=False)
        print_success(f"Stopped [b]{'[/b], [b]'.join(containers)}[/b].")
    else:
        print_success("No Odoo container is running" + (f" for [b]{version}[/b]." if version else "."))
    if include_services:
        stop_services()
        print_success("Stopped PostgreSQL, Mailpit and pgAdmin.")
    print()
