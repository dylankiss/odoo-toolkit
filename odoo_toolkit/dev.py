import os
import subprocess
import sys
from pathlib import Path
from subprocess import CalledProcessError
from typing import Annotated

from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from typer import Option, Typer

from .common import app, log

DOCKER_CMD = ["sudo docker" if sys.platform == "linux" else "docker"]
DOCKER_COMPOSE_CMD = DOCKER_CMD + [
    "compose",
    "--file",
    str(Path(__file__).parent / "docker" / "compose.yaml"),
]
UBUNTU_VERSIONS = ["noble", "jammy"]


def complete_ubuntu_version(incomplete: str):
    return list(filter(lambda v: v.startswith(incomplete), UBUNTU_VERSIONS))


dev_app = Typer(no_args_is_help=True)
app.add_typer(dev_app, name="dev")


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
    docker_containers_to_kill = [f"odoo-{version}" for version in UBUNTU_VERSIONS if version != ubuntu_version]
    docker_cmd = None
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            # Check if other Odoo Dev Containers are still running.
            # They would use the same ports and cause the docker compose up command to fail.
            docker_task = progress.add_task(description="Checking running containers ...", total=None)
            docker_process = subprocess.run(
                docker_cmd := DOCKER_COMPOSE_CMD + ["ps"], env=cmd_env, capture_output=True, text=True, check=True
            )
            docker_containers_to_kill = list(filter(lambda c: c in docker_process.stdout, docker_containers_to_kill))
            progress.update(docker_task, total=1, completed=1)

            # Kill possible running Odoo Dev Containers.
            if docker_containers_to_kill:
                docker_task = progress.add_task(description="Terminating conflicting containers ...", total=None)
                subprocess.run(
                    docker_cmd := DOCKER_COMPOSE_CMD + ["down"] + docker_containers_to_kill,
                    env=cmd_env,
                    capture_output=True,
                    check=True,
                )
                progress.update(docker_task, total=1, completed=1)

            # Check if the container we want to start was already built.
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
                docker_task = progress.add_task(
                    description="Building Docker image (go grab a coffee :coffee:) ...", total=None
                )
                subprocess.run(
                    docker_cmd := DOCKER_COMPOSE_CMD + ["build", f"odoo-{ubuntu_version}"],
                    env=cmd_env,
                    capture_output=True,
                    check=True,
                )
                progress.update(docker_task, total=1, completed=1)

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
    log(
        "",
        Panel.fit(":stop_sign: [bold]End Session[/bold]"),
        "",
    )


@dev_app.command()
def stop():
    pass


# if __name__ == "__main__":
#     dev_app()
