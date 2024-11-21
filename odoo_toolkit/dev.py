import os
import re
import subprocess
import sys
from collections.abc import Iterable
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
UBUNTU_VERSIONS = [
    ("noble", "Ubuntu 24.04 (Noble Numbat) - Recommended for Odoo >= 18.0"),
    ("jammy", "Ubuntu 22.04 (Jammy Jellyfish) - Recommended for Odoo < 18.0"),
]


def complete_ubuntu_version(incomplete: str) -> Iterable[tuple[str, str]]:
    return filter(lambda v: v[0].startswith(incomplete), UBUNTU_VERSIONS)


dev_app = Typer(no_args_is_help=True)
app.add_typer(dev_app, name="dev")


@dev_app.callback()
def callback() -> None:
    """
    Work with an Odoo Development Server using Docker.
    """


@dev_app.command()
def start(
    workspace: Annotated[
        Path,
        Option(
            "--workspace",
            "-w",
            help="The path to your development workspace. This will be mounted in the container's /code directory.",
        ),
    ] = Path("~/code/odoo"),
    ubuntu_version: Annotated[
        str,
        Option(
            "--ubuntu-version",
            "-u",
            help='The Ubuntu version to run in this container: "noble" or "jammy".',
            autocompletion=complete_ubuntu_version,
        ),
    ] = "noble",
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
        "ODOO_WORKSPACE_DIR": workspace,
    }

    log(
        Panel.fit(":rocket: [bold]Start Odoo Development Server[/bold]"),
        "",
    )
    docker_cmd = None
    try:
        with Progress(*PROGRESS_COLUMNS, console=logger, transient=True) as progress:
            # Stop the potential running containers
            docker_task = progress.add_task(description="Stopping containers ...", total=None)
            subprocess.run(
                docker_cmd := DOCKER_COMPOSE_CMD + ["down"],
                env=cmd_env,
                capture_output=True,
                check=True,
            )
            progress.update(docker_task, total=1, completed=1)

            # Check if the image we want to use was already built.
            docker_task = progress.add_task(description="Checking image existence ...", total=None)
            docker_process = subprocess.run(
                docker_cmd := DOCKER_CMD + ["images", f"localhost/odoo-dev:{ubuntu_version}"],
                env=cmd_env,
                capture_output=True,
                text=True,
                check=True,
            )
            progress.update(docker_task, total=1, completed=1)
            if ubuntu_version not in docker_process.stdout:
                docker_task = progress.add_task(description="Building Docker image ...", total=None)
                with Popen(
                    docker_cmd := DOCKER_COMPOSE_CMD
                    + ["--ansi", "never", "build", "--no-cache", f"odoo-{ubuntu_version}"],
                    env=cmd_env,
                    stderr=PIPE,
                    stdout=PIPE,
                ) as p:
                    while p.poll() is None:
                        log_line = p.stdout.readline().decode("utf-8")
                        if match := re.search(r"(\d+)/(\d+)\]", log_line):
                            completed, total = match.groups()
                            progress.update(
                                docker_task,
                                description=f"Building Docker image ({completed}/{total}) ...",
                                total=int(total),
                                completed=int(completed),
                            )
                        else:
                            # Estimated progress update per log line in the longest task (max. 5000 lines)
                            progress.update(docker_task, advance=0.0002)
                progress.update(docker_task, description="Building Docker image ...", total=1, completed=1)

            docker_task = progress.add_task(description="Starting containers ...", total=None)
            subprocess.run(
                docker_cmd := DOCKER_COMPOSE_CMD + ["up", f"odoo-{ubuntu_version}", "--detach"],
                env=cmd_env,
                capture_output=True,
                check=True,
            )
            progress.update(docker_task, total=1, completed=1)

    except CalledProcessError as error:
        log(
            ":exclamation_mark: [red]Starting the development server failed. The command that failed was:\n",
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
        return

    log(
        Panel.fit(":computer: [bold]Start Session[/bold]"),
    )
    docker_cmd = DOCKER_COMPOSE_CMD + [
        "exec",
        f"odoo-{ubuntu_version}",
        "bash",
    ]
    os.system(f"ODOO_WORKSPACE_DIR={workspace!s} {' '.join(docker_cmd)}")
    log("\nSession ended :white_check_mark:\n")


@dev_app.command()
def stop(
    workspace: Annotated[
        Path,
        Option(
            "--workspace",
            "-w",
            help="The path to your development workspace.",
        ),
    ] = Path("~/code/odoo"),
):
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
    cmd_env = os.environ | {
        "ODOO_WORKSPACE_DIR": workspace,
    }
    try:
        with Progress(*PROGRESS_COLUMNS, console=logger, transient=True) as progress:
            docker_task = progress.add_task(description="Stopping containers ...", total=None)
            subprocess.run(
                docker_cmd := DOCKER_COMPOSE_CMD + ["down"],
                env=cmd_env,
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
        return
