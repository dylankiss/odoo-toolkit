import subprocess
from typing import Annotated

from typer import Exit, Option, Typer

from odoo_toolkit.common import print, print_command_title, print_error, print_success
from odoo_toolkit.mv.config import find_version_dir

from .common import check_docker, get_run_containers, stop_services

app = Typer()


@app.command()
def stop(
    port: Annotated[
        int | None, Option("--port", "-p", help="Only stop the Odoo running on this port, on your machine."),
    ] = None,
    include_services: Annotated[
        bool,
        Option(
            "--all",
            help="Stop every container of this toolkit, including shells and `otk po export` servers, and PostgreSQL, "
            "Mailpit and pgAdmin as well.",
        ),
    ] = False,
) -> None:
    """Stop :stop_sign: the Odoo containers started with `otk dev run`.

    Inside a multiverse version directory, this stops the Odoo containers of that version. Elsewhere, it stops all of
    them. Your shells and `otk po export` servers keep running. Your databases and filestores stay, so you can continue
    where you left off.
    """
    print_command_title(":stop_sign: Stop Odoo")
    check_docker()
    version_dir = find_version_dir()
    version = None if include_services or not version_dir else version_dir.name
    containers = get_run_containers(version, role=None if include_services else "run")
    if port:
        containers = [name for name in containers if name.endswith(f"-{port}")]
    if containers:
        result = subprocess.run(["docker", "stop", *containers], capture_output=True, text=True, check=False)
        if result.returncode:
            print_error("Stopping the containers failed.", result.stderr.strip())
            raise Exit(1)
        print_success(f"Stopped [b]{'[/b], [b]'.join(containers)}[/b].")
    else:
        print_success("No Odoo container is running" + (f" for [b]{version}[/b]." if version else "."))
    if include_services:
        if error := stop_services():
            print_error("Stopping PostgreSQL, Mailpit and pgAdmin failed.", error)
            raise Exit(1)
        print_success("Stopped PostgreSQL, Mailpit and pgAdmin.")
    print()
