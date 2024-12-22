import os
import re
from pathlib import Path
from typing import Annotated

from python_on_whales import DockerException
from typer import Exit, Option, Typer

from odoo_toolkit.common import TransientProgress, print, print_command_title, print_error, print_header, print_success

from .common import DOCKER, UbuntuVersion

app = Typer()


@app.command()
def start(
    workspace: Annotated[
        Path,
        Option(
            "--workspace",
            "-w",
            help='Specify the path to your development workspace that will be mounted in the container\'s "/code" '
                'directory.',
        ),
    ] = Path("~/code/odoo"),
    ubuntu_version: Annotated[
        UbuntuVersion,
        Option(
            "--ubuntu-version",
            "-u",
            help="Specify the Ubuntu version to run in this container.",
            case_sensitive=False,
        ),
    ] = UbuntuVersion.NOBLE,
    db_port: Annotated[
        int,
        Option(
            "--db-port", "-p", help="Specify the port on your local machine the PostgreSQL database should listen on.",
        ),
    ] = 5432,
    rebuild: Annotated[
        bool, Option("--rebuild", help="Rebuild the Docker image to get the latest dependencies."),
    ] = False,
) -> None:
    """Start an Odoo Development Server using Docker and launch a terminal session into it.

    This command will start both a PostgreSQL container and an Odoo container containing your source code, located on
    your machine at the location specified by `-w`. Your specified workspace will be sourced in the container at the
    location `/code` and allows live code updates during local development.\n
    \n
    You can choose to launch a container using Ubuntu 24.04 [`-u noble`] (default, recommended starting from version
    18.0) or 22.04 [`-u jammy`] (for earlier versions).\n
    \n
    When you're done with the container, you can exit the session by running the `exit` command. At this point, the
    container will still be running and you can start a new session using the same `otk dev start` command.
    """
    print_command_title(":computer: Odoo Development Server")

    # Set the environment variables to be used by Docker Compose.
    os.environ["DB_PORT"] = str(db_port)
    os.environ["ODOO_WORKSPACE_DIR"] = str(workspace)

    print_header(":rocket: Start Odoo Development Server")

    try:
        with TransientProgress() as progress:
            if rebuild or not DOCKER.image.exists(f"localhost/odoo-dev:{ubuntu_version.value}"):
                progress_task = progress.add_task("Building Docker image :coffee: ...", total=None)
                # Build Docker image if it wasn't already or when forced.
                output_generator = DOCKER.compose.build(
                    [f"odoo-{ubuntu_version.value}"],
                    stream_logs=True,
                    cache=False,
                )
                for stream_type, stream_content in output_generator:
                    # Loop through every output line to check on the progress.
                    if stream_type != "stdout":
                        continue
                    match = re.search(r"(\d+)/(\d+)\]", stream_content.decode())
                    if match:
                        completed, total = (int(g) for g in match.groups())
                        progress.update(
                            progress_task,
                            description=f"Building Docker image :coffee: ({completed}/{total + 1}) ...",
                            total=total + 1,
                            completed=completed,
                        )
                    else:
                        # (Under)estimate progress update per log line in the longest task.
                        progress.update(progress_task, advance=0.0002)
                progress.update(progress_task, description="Building Docker image :coffee: ...", total=1, completed=1)
                print_success("Docker image built")

            progress_task = progress.add_task("Starting containers ...", total=None)
            # Start the container in the background.
            DOCKER.compose.up([f"odoo-{ubuntu_version.value}"], detach=True, quiet=True)
            progress.update(progress_task, total=1, completed=1)
            print_success("Containers started\n")

        print_header(":computer: Start Session")

        # Start a bash session in the container and let the user interact with it.
        DOCKER.compose.execute(f"odoo-{ubuntu_version.value}", ["bash"], tty=True)
        print("\nSession ended :white_check_mark:\n")

    except DockerException as e:
        print_error(
            "Starting the development server failed. The command that failed was:\n\n"
            f"[b]{' '.join(e.docker_command)}[/b]",
            "\n\n".join(o for o in (e.stderr, e.stdout) if o),
        )
        raise Exit from e
