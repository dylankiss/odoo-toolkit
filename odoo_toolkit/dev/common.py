import os
import subprocess
from pathlib import Path

from typer import Exit

from odoo_toolkit.common import TransientProgress, print, print_error
from odoo_toolkit.mv.config import find_version_dir
from odoo_toolkit.versions import OdooVersion, get_versions

from .images import LAYOUT_VERSION, Arch, build_image, get_image_layout, get_native_arch, get_remote_image_layout
from .runtime import COMPOSE_FILE, RUN_LABEL, get_image_tag

# The containers of odoo-toolkit 3.x, which use the same ports on your machine.
OLD_CONTAINERS = ("postgres", "mailpit", "pgadmin")


def get_version_dir() -> tuple[Path, OdooVersion]:
    """Get the multiverse version directory containing the current directory, and its Odoo version.

    :raises Exit: If the current directory isn't inside a supported version directory.
    """
    version_dir = find_version_dir()
    if not version_dir:
        print_error("Run this command inside an Odoo Multiverse version directory (like [u]~/code/odoo-mv/19.0[/u]).\n")
        raise Exit(1)
    version = get_versions().get(version_dir.name)
    if not version:
        print_error(f"Odoo [b]{version_dir.name}[/b] isn't supported by Runbot, so there is no image for it.\n")
        raise Exit(1)
    return version_dir, version


def start_services(*, db_port: int = 5432, pgadmin: bool = False) -> None:
    """Start the shared services, and wait until PostgreSQL accepts connections.

    :raises Exit: If the services couldn't be started.
    """
    cmd = ["docker", "compose", "--file", str(COMPOSE_FILE)]
    if pgadmin:
        cmd += ["--profile", "pgadmin"]
    cmd += ["up", "--detach", "--wait", "--quiet-pull"]
    with TransientProgress() as progress:
        progress.add_task("Starting PostgreSQL and Mailpit", total=None)
        result = subprocess.run(
            cmd, env=os.environ | {"OTK_DB_PORT": str(db_port)}, capture_output=True, text=True, check=False,
        )
    if result.returncode:
        hint = ""
        if _get_running_containers(OLD_CONTAINERS):
            hint = (
                "\nThe containers of odoo-toolkit 3.x use the same ports. Stop them with "
                "[b]docker compose --project-name odoo-dev down[/b] (your data stays).\n"
            )
        print_error(f"Starting the services failed.{hint}", result.stderr.strip())
        raise Exit(1)


def stop_services() -> None:
    """Stop the shared services. Their data stays in their volumes."""
    subprocess.run(
        ["docker", "compose", "--file", str(COMPOSE_FILE), "--profile", "pgadmin", "down"],
        capture_output=True,
        check=False,
    )


def ensure_image(version: OdooVersion, *, build: bool, arch: Arch) -> None:
    """Make sure the image of an Odoo version is available locally, building or pulling it if needed.

    :param version: The Odoo version.
    :param build: Whether to build the image on this machine instead of pulling it.
    :param arch: The architecture to run.
    :raises Exit: If the image isn't available.
    """
    tag = get_image_tag(version)
    if build:
        emulated = " It runs emulated on this machine, so this takes long." if arch != get_native_arch() else ""
        print(f"Building [b]{tag}[/b] for {arch}.{emulated}\n")
        try:
            build_image(version.image, arch)
        except subprocess.CalledProcessError as e:
            print_error("Building the image failed. See the output above.")
            raise Exit(1) from e
    # Only pull an image of this toolkit: pulling replaces what the tag points to locally, like an image you built.
    if get_image_layout(tag, arch) is None and get_remote_image_layout(tag, arch) == LAYOUT_VERSION:
        with TransientProgress() as progress:
            progress.add_task(f"Pulling {tag} for {arch}", total=None)
            subprocess.run(
                ["docker", "pull", "--quiet", "--platform", f"linux/{arch}", tag], capture_output=True, check=False,
            )
    if get_image_layout(tag, arch) != LAYOUT_VERSION:
        print_error(
            f"There is no {arch} image for Odoo [b]{version.name}[/b] for this version of the toolkit yet. "
            "Build it on your machine with [b]--build[/b].\n",
        )
        raise Exit(1)


def get_run_containers(version: str | None = None) -> list[str]:
    """Get the names of the running Odoo containers, of one version or all of them."""
    label = f"{RUN_LABEL}={version}" if version else RUN_LABEL
    result = subprocess.run(
        ["docker", "ps", "--filter", f"label={label}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.split()


def _get_running_containers(names: tuple[str, ...]) -> list[str]:
    result = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, check=False)
    return [name for name in result.stdout.split() if name in names]
