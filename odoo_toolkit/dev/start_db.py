import os
from typing import Annotated

from python_on_whales import DockerException
from typer import Exit, Option, Typer

from odoo_toolkit.common import (
    TransientProgress,
    print_command_title,
    print_error,
    print_header,
    print_panel,
    print_success,
)

from .common import DOCKER

app = Typer()


@app.command()
def start_db(
    port: Annotated[
        int,
        Option("--port", "-p", help="Specify the port on your local machine the PostgreSQL database should listen on."),
    ] = 5432,
) -> None:
    """Start a standalone PostgreSQL container for your Odoo databases.

    You can use this standalone container if you want to connect to it from your local machine which is running Odoo.
    By default it will listen on port `5432`, but you can modify this if you already have another PostgreSQL server
    running locally.
    """
    print_command_title(":computer: PostgreSQL Server")

    # Set the environment variables to be used by Docker Compose.
    os.environ["DB_PORT"] = str(port)

    print_header(":rocket: Start PostgreSQL Server")

    try:
        with TransientProgress() as progress:
            progress_task = progress.add_task("Starting PostgreSQL container ...", total=None)
            # Start the PostgreSQL container in the background.
            DOCKER.compose.up(["postgres"], detach=True, quiet=True)
            progress.update(progress_task, total=1, completed=1)
            print_success("PostgreSQL container started\n")
            print_panel(
                f"Host: [b]localhost[/b]\n"
                f"Port: [b]{port}[/b]\n"
                f"User: [b]odoo[/b]\n"
                f"Password:",
                "Connection Details",
            )
    except DockerException as e:
        print_error(
            "Starting the PostgreSQL server failed. The command that failed was:\n\n"
            f"[b]{' '.join(e.docker_command)}[/b]",
            "\n\n".join(o for o in (e.stderr, e.stdout) if o),
        )
        raise Exit from e
