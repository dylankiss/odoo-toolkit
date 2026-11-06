import os
import shutil
import subprocess
from pathlib import Path

import requests
from typer import Exit

from odoo_toolkit.common import (
    TransientProgress,
    console,
    print_error,
    print_warning,
)
from odoo_toolkit.mv.config import require_version_dir
from odoo_toolkit.versions import OdooVersion, get_versions

from .image_check import get_service_images
from .images import (
    DEFAULT_UID,
    LAYOUT_LABEL,
    LAYOUT_VERSION,
    UID_LABEL,
    Arch,
    build_image,
    get_image_labels,
    get_native_arch,
    get_remote_image_layout,
)
from .proxy import get_proxy_logs, reload_proxy, write_allowed_domains
from .runtime import COMPOSE_FILE, DB_FORWARD_HOST, ROLE_LABEL, RUN_LABEL, Role, get_image_tag, get_user_ids

# The containers of odoo-toolkit 3.x, which use the same ports on your machine.
OLD_CONTAINERS = ("postgres", "mailpit", "pgadmin")
DEFAULT_DB_PORT = 5432


def get_version_dir() -> tuple[Path, OdooVersion]:
    """Get the multiverse version directory containing the current directory, and its Odoo version.

    :raises Exit: If the current directory isn't inside a supported version directory.
    """
    version_dir = require_version_dir()
    version = get_versions().get(version_dir.name)
    if not version:
        print_error(f"Odoo [b]{version_dir.name}[/b] isn't supported by Runbot, so there is no image for it.\n")
        raise Exit(1)
    return version_dir, version


def check_docker() -> None:
    """Make sure Docker is installed and running.

    :raises Exit: If it isn't.
    """
    # A container, like a dev container, has no Docker by design: the commands run on your machine.
    in_container = Path("/.dockerenv").exists() or bool(os.environ.get("REMOTE_CONTAINERS"))
    where = (
        " This looks like a container, like a dev container: run it in a terminal on your machine."
        if in_container
        else ""
    )
    if not shutil.which("docker"):
        print_error(f"This command needs Docker: https://docs.docker.com/get-started/get-docker/{where}\n")
        raise Exit(1)
    result = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, check=False
    )
    if result.returncode:
        print_error(
            "Docker can't be reached. Start it (like OrbStack or Docker Desktop), and check that `docker context ls` "
            f"points to it.{where}\n",
            result.stderr.strip(),
        )
        raise Exit(1)


def get_db_port() -> int | None:
    """Get the port PostgreSQL is published on, on your machine, if it's running."""
    result = subprocess.run(
        ["docker", "port", DB_FORWARD_HOST, "5432/tcp"], capture_output=True, text=True, check=False
    )
    port = result.stdout.strip().splitlines()[0].rpartition(":")[2] if result.stdout.strip() else ""
    return int(port) if port.isdigit() else None


def start_services(*, db_port: int | None = None, pgadmin: bool = False, pull: bool = False) -> None:
    """Start the shared services, and wait until PostgreSQL accepts connections.

    :param db_port: The port to publish PostgreSQL on, or None to keep the one it runs on (5432 at first). Changing it
        restarts the forwarder, which interrupts the connections to PostgreSQL.
    :param pgadmin: Whether to start pgAdmin as well.
    :param pull: Whether to pull newer builds of the images first. A service whose image changed is recreated.
    :raises Exit: If the services couldn't be started.
    """
    check_docker()
    db_port = db_port or get_db_port() or DEFAULT_DB_PORT
    egress_dir, domains_removed = write_allowed_domains()
    cmd = ["docker", "compose", "--file", str(COMPOSE_FILE)]
    if pgadmin:
        cmd += ["--profile", "pgadmin"]
    cmd += ["up", "--detach", "--wait", "--quiet-pull", *(["--pull", "always"] if pull else [])]
    previous_ids = {image: get_image_id(image) for image in get_service_images()} if pull else {}
    with TransientProgress() as progress:
        progress.add_task("Starting PostgreSQL and Mailpit", total=None)
        result = subprocess.run(
            cmd,
            env=os.environ | {"OTK_DB_PORT": str(db_port), "OTK_EGRESS_DIR": str(egress_dir)},
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode:
        hint = ""
        if _get_running_containers(OLD_CONTAINERS):
            hint = (
                "\nThe containers of odoo-toolkit 3.x use the same ports. Stop them with "
                "[b]docker compose --project-name odoo-dev down[/b] (your data stays).\n"
            )
        if proxy_logs := get_proxy_logs():
            hint += f"\nThe proxy of the dev containers logged:\n{proxy_logs}\n"
        print_error(f"Starting the services failed.{hint}", result.stderr.strip())
        raise Exit(1)
    # A proxy that was already running reads the domains you allow now.
    reload_proxy(restart=domains_removed)
    # The services were recreated from the new builds, so the earlier ones aren't used anymore.
    for image, previous_id in previous_ids.items():
        remove_replaced_image(image, previous_id)


def stop_services() -> str | None:
    """Stop the shared services. Their data stays in their volumes.

    :return: Why stopping them failed, or None if it worked.
    """
    result = subprocess.run(
        ["docker", "compose", "--file", str(COMPOSE_FILE), "--profile", "pgadmin", "down"],
        capture_output=True,
        text=True,
        check=False,
    )
    return (result.stderr.strip() or "Docker Compose failed.") if result.returncode else None


def ensure_image(version: OdooVersion, *, build: bool, arch: Arch, pull: bool = False) -> None:
    """Make sure the image of an Odoo version is available locally, building or pulling it if needed.

    :param version: The Odoo version.
    :param build: Whether to build the image on this machine instead of pulling it.
    :param arch: The architecture to run.
    :param pull: Whether to pull the latest image, even if there is one locally.
    :raises Exit: If the image isn't available.
    """
    check_docker()
    tag = get_image_tag(version)
    user_ids = get_user_ids()
    # On Linux, the image must run as your user, while the ones on Docker Hub run as 1000.
    uid = user_ids[0] if user_ids else DEFAULT_UID

    def is_usable() -> bool:
        labels = get_image_labels(tag, arch) or {}
        return labels.get(LAYOUT_LABEL) == LAYOUT_VERSION and labels.get(UID_LABEL, str(DEFAULT_UID)) == str(uid)

    if build:
        _build(version, arch, user_ids if uid != DEFAULT_UID else None)
    elif uid != DEFAULT_UID:
        if pull:
            print_warning(
                f"The images on Docker Hub run as user ID {DEFAULT_UID}, but yours is {uid}, so there is nothing to "
                "pull. Rebuild yours with [b]--build[/b].\n",
            )
    # Only pull an image of this toolkit: pulling replaces what the tag points to locally, like an image you built.
    elif pull or not is_usable():
        if get_remote_image_layout(tag, arch) != LAYOUT_VERSION:
            if pull:
                print_warning(f"Docker Hub has no {arch} image of [b]{tag}[/b] for this version of the toolkit.\n")
        elif not _pull(tag, arch) and not is_usable():
            # Why is already printed.
            raise Exit(1)
    if not is_usable():
        reason = (
            f"The images on Docker Hub run as user ID {DEFAULT_UID}, but yours is {uid}."
            if uid != DEFAULT_UID
            else f"There is no {arch} image for Odoo [b]{version.name}[/b] for this version of the toolkit yet."
        )
        print_error(f"{reason} Build one on your machine with [b]--build[/b].\n")
        raise Exit(1)


def _build(version: OdooVersion, arch: Arch, user_ids: tuple[int, int] | None) -> None:
    """Build the image of an Odoo version on this machine.

    :param version: The Odoo version.
    :param arch: The architecture to build for.
    :param user_ids: The user and group IDs the image runs as, or None for the ones of the images on Docker Hub.
    :raises Exit: If building failed.
    """
    emulated = " It runs emulated on this machine, so this takes long." if arch != get_native_arch() else ""
    console.print(f"Building [b]{get_image_tag(version)}[/b] for {arch}.{emulated}\n")
    try:
        build_image(version.image, arch, user_ids=user_ids)
    except subprocess.CalledProcessError as e:
        print_error("Building the image failed. See the output above.")
        raise Exit(1) from e
    except (requests.RequestException, ValueError) as e:
        # Adapting the image to arm64 needs to look up what Google publishes, and can't adapt everything.
        print_error("Preparing the image failed.", str(e))
        raise Exit(1) from e


def _pull(tag: str, arch: Arch) -> bool:
    """Pull an image, showing why it failed if it did.

    :return: Whether it was pulled.
    """
    previous_id = get_image_id(tag)
    with TransientProgress() as progress:
        progress.add_task(f"Pulling {tag} for {arch}", total=None)
        result = subprocess.run(
            ["docker", "pull", "--quiet", "--platform", f"linux/{arch}", tag],
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode:
        print_error(f"Pulling [b]{tag}[/b] failed.", result.stderr.strip())
        return False
    remove_replaced_image(tag, previous_id)
    return True


def get_image_id(image: str) -> str | None:
    """Get the ID of an image on this machine, or None if it isn't there."""
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image], capture_output=True, text=True, check=False
    )
    return (result.stdout.strip() or None) if not result.returncode else None


def remove_replaced_image(image: str, previous_id: str | None) -> None:
    """Remove the build an image pointed to before a pull, which now only takes space.

    A build that a container still uses, like a dev container created from it, stays: `otk dev clean` removes it once
    that container is gone.

    :param image: The image, like `dylankiss/odoo-dev:19.0`.
    :param previous_id: Its ID before the pull, or None if it wasn't there.
    """
    if previous_id and previous_id != get_image_id(image):
        subprocess.run(["docker", "image", "rm", previous_id], capture_output=True, check=False)


def get_run_containers(version: str | None = None, role: Role | None = "run") -> list[str]:
    """Get the names of the running containers of this toolkit.

    :param version: Only the ones of this Odoo version, or None for all of them.
    :param role: Only the ones for this, like running Odoo, or None for all of them.
    :return: The container names.
    """
    filters = ["--filter", f"label={RUN_LABEL}={version}" if version else f"label={RUN_LABEL}"]
    if role:
        filters += ["--filter", f"label={ROLE_LABEL}={role}"]
    result = subprocess.run(
        ["docker", "ps", *filters, "--format", "{{.Names}}"], capture_output=True, text=True, check=False
    )
    return result.stdout.split()


def _get_running_containers(names: tuple[str, ...]) -> list[str]:
    result = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, check=False)
    return [name for name in result.stdout.split() if name in names]
