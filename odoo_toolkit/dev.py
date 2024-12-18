import os
import re
from enum import Enum
from pathlib import Path
from typing import Annotated

from python_on_whales import DockerClient, DockerException
from typer import Exit, Option, Typer

from .common import (
    TransientProgress,
    app,
    print,
    print_command_title,
    print_error,
    print_header,
    print_panel,
    print_success,
)

# Initialize the Docker client with the correct compose file.
docker = DockerClient(compose_files=[Path(__file__).parent / "docker" / "compose.yaml"])


class UbuntuVersion(str, Enum):
    """Ubuntu versions available as Docker images."""

    NOBLE = "noble"
    JAMMY = "jammy"


dev_app = Typer(no_args_is_help=True, rich_markup_mode="markdown")
app.add_typer(dev_app, name="dev")


@dev_app.callback()
def callback() -> None:
    """Run an Odoo Development Server using Docker.

    The following commands allow you to automatically start and stop a fully configured Docker container to run your
    Odoo server(s) during development.
    \n\n
    These tools require Docker Desktop to be installed on your system.
    \n\n
    The Docker container is configured to resemble Odoo's CI or production servers and thus tries to eliminate
    discrepancies between your local system and the CI or production server.
    """


@dev_app.command()
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
    *,
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
    location `/code` and allows live code updates during local development.
    \n\n
    You can choose to launch a container using Ubuntu 24.04 [`-u noble`] (default, recommended starting from version
    18.0) or 22.04 [`-u jammy`] (for earlier versions).
    \n\n
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
            if rebuild or not docker.image.exists(f"localhost/odoo-dev:{ubuntu_version.value}"):
                progress_task = progress.add_task("Building Docker image :coffee: ...", total=None)
                # Build Docker image if it wasn't already or when forced.
                output_generator = docker.compose.build(
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
            docker.compose.up([f"odoo-{ubuntu_version.value}"], detach=True, quiet=True)
            progress.update(progress_task, total=1, completed=1)
            print_success("Containers started\n")

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
            "Starting the development server failed. The command that failed was:\n\n"
            f"[b]{' '.join(e.docker_command)}[/b]",
            stacktrace,
        )
        raise Exit from e


@dev_app.command()
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
            docker.compose.up(["db"], detach=True, quiet=True)
            progress.update(progress_task, total=1, completed=1)
            print_success("PostgreSQL container started\n")
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
            "Starting the PostgreSQL server failed. The command that failed was:\n\n"
            f"[b]{' '.join(e.docker_command)}[/b]",
            stacktrace,
        )
        raise Exit from e


@dev_app.command()
def stop() -> None:
    """Stop and delete all running containers of the Odoo Development Server.

    This is useful if you want to build a new version of the container, or you want the container to have the latest
    version of `odoo-toolkit`.
    \n\n
    Running this is also necessary if you updated the `odoo-toolkit` package on your local machine. If not, your
    container won't be able to mount the configuration files.
    """
    print_command_title(":computer: Odoo Development Server")

    try:
        with TransientProgress() as progress:
            progress_task = progress.add_task("Stopping containers ...", total=None)
            # Stop and delete the running containers.
            docker.compose.down(quiet=True)
            progress.update(progress_task, total=1, completed=1)
            print_success("Containers stopped and deleted\n")
    except DockerException as e:
        stacktrace = e.stderr
        if stacktrace:
            stacktrace += f"\n\n{e.stdout}" if e.stdout else ""
        else:
            stacktrace = e.stdout
        print_error(
            "Stopping the development server failed. The command that failed was:\n\n"
            f"[b]{' '.join(e.docker_command)}[/b]",
            stacktrace,
        )
        raise Exit from e
