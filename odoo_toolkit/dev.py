import os
import re
from enum import Enum
from pathlib import Path
from typing import Annotated

from python_on_whales import DockerClient, DockerException
from typer import Option, Typer

from .common import TransientProgress, app, print, print_command_title, print_error, print_header, print_panel

# Initialize the Docker client with the correct compose file.
docker = DockerClient(compose_files=[Path(__file__).parent / "docker" / "compose.yaml"])


class UbuntuVersion(str, Enum):
    NOBLE = "noble"
    JAMMY = "jammy"


dev_app = Typer(no_args_is_help=True)
app.add_typer(dev_app, name="dev")


@dev_app.callback()
def callback():
    """
    💻 Odoo Development Server

    Run an Odoo Development Server using Docker.
    """


@dev_app.command()
def start(
    workspace: Annotated[
        Path,
        Option(
            "--workspace",
            "-w",
            help='Specify the path to your development workspace that will be mounted in the container\'s "/code" directory.',
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
            "--db-port", "-p", help="Specify the port on your local machine the PostgreSQL database should listen on."
        ),
    ] = 5432,
):
    """
    Start an Odoo Development Server using Docker and launch a terminal session into it.

    This command will start both a PostgreSQL container and an Odoo container containing your source code.
    You can choose to launch a container using Ubuntu 24.04 [`noble`] (default) or 22.04 [`jammy`] using "-u".
    The source code can be mapped using the "-w" option as the path to your workspace.
    """
    print_command_title(":computer: Odoo Development Server")

    # Set the environment variables to be used by Docker Compose.
    os.environ["DB_PORT"] = str(db_port)
    os.environ["ODOO_WORKSPACE_DIR"] = str(workspace)

    print_header(":rocket: Start Odoo Development Server")

    try:
        with TransientProgress() as progress:
            if not docker.image.exists(f"localhost/odoo-dev:{ubuntu_version.value}"):
                progress_task = progress.add_task("Building Docker image :coffee: ...", total=None)
                # Build Docker image if it wasn't already.
                output_generator = docker.compose.build(
                    [f"odoo-{ubuntu_version.value}"],
                    stream_logs=True,
                    cache=False,
                )
                for stream_type, stream_content in output_generator:
                    # Loop through every output line to check on the progress.
                    if stream_type != "stdout":
                        continue
                    if match := re.search(r"(\d+)/(\d+)\]", stream_content.decode()):
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
                print("Docker image built :white_check_mark:")

            progress_task = progress.add_task(description="Starting containers ...", total=None)
            # Start the container in the background.
            docker.compose.up([f"odoo-{ubuntu_version.value}"], detach=True, quiet=True)
            progress.update(progress_task, total=1, completed=1)
            print("Containers started :white_check_mark:\n")

        print_header(":computer: Start Session")

        # Start a bash session in the container and let the user interact with it.
        docker.compose.execute(f"odoo-{ubuntu_version.value}", ["bash"], tty=True)
        print("\nSession ended :white_check_mark:\n")

    except DockerException as e:
        stacktrace = e.stderr
        if stacktrace:
            stacktrace += f"\n\n{e.stdout}" if e.stdout else ""
        else:
            stacktrace = e.stdout
        print_error(
            f"Starting the development server failed. The command that failed was:\n\n[b]{' '.join(e.docker_command)}[/b]",
            stacktrace,
        )


@dev_app.command()
def start_db(
    port: Annotated[
        int,
        Option("--port", "-p", help="Specify the port on your local machine the PostgreSQL database should listen on."),
    ] = 5432,
):
    """
    Start a standalone PostgreSQL container for your Odoo databases.
    """
    print_command_title(":computer: PostgreSQL Server")

    # Set the environment variables to be used by Docker Compose.
    os.environ["DB_PORT"] = str(port)

    print_header(":rocket: Start PostgreSQL Server")

    try:
        with TransientProgress() as progress:
            progress_task = progress.add_task("Starting PostgreSQL container ...", total=None)
            # Start the PostgreSQL container in the background.
            docker.compose.up(["db"], detach=True, quiet=True)
            progress.update(progress_task, total=1, completed=1)
            print("PostgreSQL container started :white_check_mark:\n")
            print_panel(
                f"Host: [b]localhost[/b]\nPort: [b]{port}[/b]\nUser: [b]odoo[/b]\nPassword: [b]odoo[/b]",
                "Connection Details",
            )
    except DockerException as e:
        stacktrace = e.stderr
        if stacktrace:
            stacktrace += f"\n\n{e.stdout}" if e.stdout else ""
        else:
            stacktrace = e.stdout
        print_error(
            f"Starting the PostgreSQL server failed. The command that failed was:\n\n[b]{' '.join(e.docker_command)}[/b]",
            stacktrace,
        )


@dev_app.command()
def stop():
    """
    Stop and delete all running containers of the Odoo Development Server.
    """
    print_command_title(":computer: Odoo Development Server")

    try:
        with TransientProgress() as progress:
            progress_task = progress.add_task("Stopping containers ...", total=None)
            # Stop and delete the running containers.
            docker.compose.down(quiet=True)
            progress.update(progress_task, total=1, completed=1)
            print("Containers stopped and deleted :white_check_mark:\n")
    except DockerException as e:
        stacktrace = e.stderr
        if stacktrace:
            stacktrace += f"\n\n{e.stdout}" if e.stdout else ""
        else:
            stacktrace = e.stdout
        print_error(
            f"Stopping the development server failed. The command that failed was:\n\n[b]{' '.join(e.docker_command)}[/b]",
            stacktrace,
        )
