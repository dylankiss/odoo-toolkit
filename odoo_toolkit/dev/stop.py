from python_on_whales import DockerException
from typer import Exit, Typer

from odoo_toolkit.common import TransientProgress, print_command_title, print_error, print_success

from .common import DOCKER

app = Typer()


@app.command()
def stop() -> None:
    """Stop and delete all running containers of the Odoo Development Server.

    This is useful if you want to build a new version of the container, or you want the container to have the latest
    version of `odoo-toolkit`.\n
    \n
    Running this is also necessary if you updated the `odoo-toolkit` package on your local machine. If not, your
    container won't be able to mount the configuration files.
    """
    print_command_title(":computer: Odoo Development Server")

    try:
        with TransientProgress() as progress:
            progress_task = progress.add_task("Stopping containers ...", total=None)
            # Stop and delete the running containers.
            DOCKER.compose.down(quiet=True)
            progress.update(progress_task, total=1, completed=1)
            print_success("Containers stopped and deleted\n")
    except DockerException as e:
        print_error(
            "Stopping the development server failed. The command that failed was:\n\n"
            f"[b]{' '.join(e.docker_command)}[/b]",
            "\n\n".join(o for o in (e.stderr, e.stdout) if o),
        )
        raise Exit from e
