import os
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from typer import Context, Exit, Option, Typer

from odoo_toolkit.common import EMPTY_LIST, normalize_list_option, print, print_error, print_warning
from odoo_toolkit.mv.config import get_repo_dirs
from odoo_toolkit.versions import OdooVersion

from .common import ensure_image, get_run_containers, get_version_dir, start_services
from .images import Arch, get_native_arch
from .runtime import (
    RunOptions,
    build_run_command,
    check_mountable,
    find_free_port,
    get_addons_paths,
    get_git_identity,
    get_ssh_agent,
    is_port_free,
)

app = Typer()


class Platform(StrEnum):
    """The architectures the images are built for."""

    AMD64 = "amd64"
    ARM64 = "arm64"


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run(
    context: Context,
    port: Annotated[int, Option("--port", "-p", help="The port to reach Odoo on, on your machine.")] = 8069,
    debug: Annotated[
        bool, Option("--debug", help="Start Odoo under the debugger, waiting for Visual Studio Code to attach."),
    ] = False,
    debug_port: Annotated[int, Option("--debug-port", help="The port of the debugger, on your machine.")] = 5678,
    upgrade: Annotated[
        bool, Option("--upgrade", help="Add the upgrade scripts of `upgrade` and `upgrade-util` to the upgrade path."),
    ] = False,
    community: Annotated[
        bool, Option("--community", help="Only load the modules of Odoo Community (`odoo/addons`)."),
    ] = False,
    exclude: Annotated[
        list[str],
        Option(
            "--exclude",
            "-x",
            help="Leave these repositories out of the addons path, like `enterprise` or `odoofin,design-themes`.",
        ),
    ] = EMPTY_LIST,
    build: Annotated[bool, Option("--build", help="Build the image on your machine instead of pulling it.")] = False,
    pull: Annotated[bool, Option("--pull", help="Pull the latest image, even if you have one already.")] = False,
    db_port: Annotated[
        int | None,
        Option(
            "--db-port",
            help="The port to reach PostgreSQL on, on your machine. Defaults to the one it runs on, or 5432. Changing "
            "it restarts PostgreSQL.",
        ),
    ] = None,
    platform: Annotated[
        Platform | None,
        Option(
            "--platform",
            help="Run the image of this architecture. On Apple Silicon, `amd64` runs emulated and slower, but has what "
            "only exists for amd64, like Paper Muncher. Defaults to the architecture of this machine.",
        ),
    ] = None,
) -> None:
    """Run :rocket: Odoo for the current Odoo Multiverse version directory, in Docker.

    Run this command anywhere inside one of the multiverse version directories (like `19.0`). It starts PostgreSQL and
    Mailpit if needed, and runs Odoo in a container with the same environment as Runbot, removed when Odoo stops
    (`Ctrl+C`).\n
    \n
    The addons path contains every repository of the version directory with modules in it, like `enterprise`,
    `odoo/addons`, and linked repositories like `odoofin`. Use `--community` to only load Odoo Community, or
    `--exclude` to leave out repositories. Pass any other Odoo options after `--`, like `otk dev run -- -d mydb -i sale`.
    """
    version_dir, version = get_version_dir()
    exclude = normalize_list_option(exclude)
    if unknown := sorted(set(exclude) - {path.name for path in get_repo_dirs(version_dir, include_linked=True)}):
        print_warning(f"There is no [b]{'[/b], [b]'.join(unknown)}[/b] in [u]{version_dir}[/u] to leave out.\n")

    for name, wanted in (("--port", port), ("--debug-port", debug_port if debug else None)):
        if wanted and not is_port_free(wanted):
            free = find_free_port(wanted + 1)
            suggestion = f" Try [b]{name} {free}[/b]." if free else ""
            print_error(f"Port [b]{wanted}[/b] is already in use, probably by another Odoo.{suggestion}\n")
            raise Exit(1)

    arch = platform.value if platform else get_native_arch()
    # Visual Studio Code's debug task only waits for Odoo if it sees this line within 5 seconds, so print it before
    # pulling the image or starting the services. It must not wrap, to match.
    print(f"otk: starting Odoo {version.name} ({arch}) on http://localhost:{port}", highlight=False, soft_wrap=True)
    _prepare(version_dir, version, arch=arch, build=build, pull=pull, db_port=db_port)

    options = RunOptions(
        version_dir=version_dir,
        version=version,
        port=port,
        debug_port=debug_port if debug else None,
        upgrade=upgrade,
        odoo_args=list(context.args),
        arch=arch,
        exclude=exclude,
        community=community,
    )
    command = build_run_command(
        options,
        multiverse_dir=version_dir.parent,
        git_identity=get_git_identity(),
        tty=sys.stdin.isatty(),
        ssh_agent=get_ssh_agent(),
    )
    addons_paths = get_addons_paths(version_dir, exclude=exclude, community=community)
    addons = ", ".join(str(path.relative_to(version_dir)) for path in addons_paths)
    print(f"otk: addons path: {addons}", highlight=False, soft_wrap=True)
    _exec(command)


@app.command()
def shell(
    build: Annotated[bool, Option("--build", help="Build the image on your machine instead of pulling it.")] = False,
    pull: Annotated[bool, Option("--pull", help="Pull the latest image, even if you have one already.")] = False,
    platform: Annotated[
        Platform | None,
        Option("--platform", help="Use the image of this architecture. Defaults to the architecture of this machine."),
    ] = None,
) -> None:
    """Open a :computer: shell in the Odoo container of the current Odoo Multiverse version directory.

    If Odoo is running for this version, the shell opens in its container. Otherwise it opens in a new container with
    the same environment, removed when you exit the shell.
    """
    version_dir, version = get_version_dir()
    tty = sys.stdin.isatty()
    if running := get_run_containers(version.name):
        _exec(["docker", "exec", "--interactive", *(["--tty"] if tty else []), running[0], "bash"])

    arch = platform.value if platform else get_native_arch()
    _prepare(version_dir, version, arch=arch, build=build, pull=pull)
    command = build_run_command(
        RunOptions(version_dir=version_dir, version=version, port=None, command=["bash"], arch=arch, role="shell"),
        multiverse_dir=version_dir.parent,
        git_identity=get_git_identity(),
        tty=tty,
        ssh_agent=get_ssh_agent(),
    )
    _exec(command)


def _prepare(
    version_dir: Path,
    version: OdooVersion,
    *,
    arch: Arch,
    build: bool,
    pull: bool,
    db_port: int | None = None,
) -> None:
    """Make sure the multiverse can be mounted, the image is available, and the services are running.

    :raises Exit: If one of them isn't possible.
    """
    if problem := check_mountable(version_dir.parent):
        print_error(f"{problem}\n")
        raise Exit(1)
    ensure_image(version, build=build, arch=arch, pull=pull)
    start_services(db_port=db_port)


def _exec(command: list[str]) -> None:
    """Replace this process with the command, so the terminal and `Ctrl+C` behave as if you ran it yourself."""
    sys.stdout.flush()
    sys.stderr.flush()
    os.execvp(command[0], command)  # noqa: S606
