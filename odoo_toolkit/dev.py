import os
import re
import subprocess
import sys
from enum import Enum
from pathlib import Path
from subprocess import PIPE, CalledProcessError, Popen
from typing import Annotated

from rich.panel import Panel
from rich.progress import Progress
from typer import Option, Typer

from .common import PROGRESS_COLUMNS, app, log, logger

DOCKER_CMD = ["sudo docker" if sys.platform == "linux" else "docker"]
DOCKER_COMPOSE_CMD = DOCKER_CMD + [
    "compose",
    "--file",
    str(Path(__file__).parent / "docker" / "compose.yaml"),
]


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
    You can choose to launch a container using Ubuntu 24.04 [noble] (default) or 22.04 [jammy] using "-u".
    The source code can be mapped using the "-w" option as the path to your workspace.
    """
    log(
        Panel.fit(
            ":computer: Odoo Development Server",
            style="bold magenta",
            border_style="bold magenta",
        ),
        "",
    )
    cmd_env = os.environ | {
        "DB_PORT": str(db_port),
        "ODOO_WORKSPACE_DIR": workspace,
        "PYTHONUNBUFFERED": "1",
    }

    log(
        Panel.fit(":rocket: [bold]Start Odoo Development Server[/bold]"),
        "",
    )
    docker_cmd = None
    try:
        with Progress(*PROGRESS_COLUMNS, console=logger, transient=True) as progress:
            # Check if the image we want to use was already built.
            docker_task = progress.add_task(description="Checking image existence ...", total=None)
            docker_process = subprocess.run(
                docker_cmd := DOCKER_CMD + ["images", f"localhost/odoo-dev:{ubuntu_version.value}"],
                env=cmd_env,
                capture_output=True,
                text=True,
                check=True,
            )
            progress.update(docker_task, total=1, completed=1)
            if ubuntu_version.value not in docker_process.stdout:
                docker_task = progress.add_task(description="Building Docker image :coffee: ...", total=None)
                with Popen(
                    docker_cmd := DOCKER_COMPOSE_CMD
                    + ["--ansi", "never", "build", "--no-cache", f"odoo-{ubuntu_version.value}"],
                    env=cmd_env,
                    stderr=PIPE,
                    stdout=PIPE,
                    text=True,
                ) as p:
                    while p.poll() is None:
                        log_line = p.stdout.readline()
                        if match := re.search(r"(\d+)/(\d+)\]", log_line):
                            completed, total = match.groups()
                            completed, total = int(completed), int(total) + 1
                            progress.update(
                                docker_task,
                                description=f"Building Docker image :coffee: ({completed}/{total}) ...",
                                total=total,
                                completed=completed,
                            )
                        else:
                            # Estimated progress update per log line in the longest task (max. 5000 lines)
                            progress.update(docker_task, advance=0.0002)
                progress.update(docker_task, description="Building Docker image :coffee: ...", total=1, completed=1)
                log("Docker image built :white_check_mark:")

            docker_task = progress.add_task(description="Starting containers ...", total=None)
            subprocess.run(
                docker_cmd := DOCKER_COMPOSE_CMD + ["up", f"odoo-{ubuntu_version.value}", "--detach"],
                env=cmd_env,
                capture_output=True,
                check=True,
                text=True,
            )
            progress.update(docker_task, total=1, completed=1)
            log("Containers started :white_check_mark:\n")

    except CalledProcessError as error:
        log(
            ":exclamation_mark: [red]Starting the development server failed. The command that failed was:\n",
            "\t[bold red]" + " ".join(docker_cmd) + "\n",
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

    log(
        Panel.fit(":computer: [bold]Start Session[/bold]"),
    )
    docker_process = Popen(
        docker_cmd := DOCKER_COMPOSE_CMD
        + ["exec", "--interactive", "--tty", f"odoo-{ubuntu_version.value}", "bash"],
        env=cmd_env,
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    docker_process.communicate()
    log("\nSession ended :white_check_mark:\n")


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
    log(
        Panel.fit(
            ":computer: PostgreSQL Server",
            style="bold magenta",
            border_style="bold magenta",
        ),
        "",
    )
    cmd_env = os.environ | {
        "DB_PORT": str(port),
    }
    log(
        Panel.fit(":rocket: [bold]Start PostgreSQL Server[/bold]"),
        "",
    )
    docker_cmd = None
    try:
        with Progress(*PROGRESS_COLUMNS, console=logger, transient=True) as progress:
            docker_task = progress.add_task(description="Starting PostgreSQL container ...", total=None)
            subprocess.run(
                docker_cmd := DOCKER_COMPOSE_CMD + ["up", "db", "--detach"],
                env=cmd_env,
                capture_output=True,
                check=True,
            )
            progress.update(docker_task, total=1, completed=1)
            log("PostgreSQL container started :white_check_mark:\n")
            log(
                Panel.fit(
                    "Host: [bold]localhost[/bold]\n"
                    f"Port: [bold]{port}[/bold]\n"
                    "User: [bold]odoo[/bold]\n"
                    "Password: [bold]odoo[/bold]",
                    title="Connection Details",
                    title_align="left",
                )
            )
    except CalledProcessError as error:
        log(
            ":exclamation_mark: [red]Starting the PostgreSQL server failed. The command that failed was:\n",
            "\t[bold red]" + " ".join(docker_cmd) + "\n",
        )
        log(
            Panel(
                error.stderr.decode().strip(),
                title="Error Log",
                title_align="left",
                style="red",
                border_style="bold red",
            ),
        )


@dev_app.command()
def stop():
    """
    Stop all running containers of the Odoo Development Server.
    """
    log(
        Panel.fit(
            ":computer: Odoo Development Server",
            style="bold magenta",
            border_style="bold magenta",
        ),
        "",
    )
    try:
        with Progress(*PROGRESS_COLUMNS, console=logger, transient=True) as progress:
            docker_task = progress.add_task(description="Stopping containers ...", total=None)
            subprocess.run(
                docker_cmd := DOCKER_COMPOSE_CMD + ["down"],
                capture_output=True,
                check=True,
            )
            progress.update(docker_task, total=1, completed=1)
            log("Containers stopped :white_check_mark:\n")
    except CalledProcessError as error:
        log(
            ":exclamation_mark: [red]Stopping the development server failed. The command that failed was:\n",
            "\t[bold red]" + " ".join(docker_cmd) + "\n",
        )
        log(
            Panel(
                error.stderr.decode().strip(),
                title="Error Log",
                title_align="left",
                style="red",
                border_style="bold red",
            ),
        )
