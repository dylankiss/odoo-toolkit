import subprocess
from typing import cast, get_args

from typer import Exit, Typer

from odoo_toolkit.common import TransientProgress, console, print_command_title, print_error, print_success
from odoo_toolkit.versions import get_versions

from .common import check_docker, ensure_image, get_db_port, get_image_id, remove_replaced_image, start_services
from .image_check import get_local_digests, get_service_images
from .images import DOCKER_REPOSITORY, Arch
from .runtime import get_image_tag

app = Typer()

PGADMIN_CONTAINER = "otk-pgadmin"


@app.command()
def pull() -> None:
    """Pull :arrow_down: the newer builds of the Docker images of this toolkit you have.

    That's the Odoo image of every version you used, and the images of the shared services. The services that are
    running are recreated from their new image, which keeps their data. The earlier builds are removed, unless a
    container still uses them. Images you built yourself (with `--build`) stay as they are.
    """
    print_command_title(":arrow_down: Pull Images")
    check_docker()
    updated, failed = pull_odoo_images()
    service_updated, service_failed = pull_service_images()
    updated += service_updated
    failed += service_failed
    if updated:
        console.print(f"Updated: [b]{'[/b], [b]'.join(updated)}[/b]\n")
        if any(image.startswith(f"{DOCKER_REPOSITORY}:") for image in updated):
            console.print(
                "Restart the Odoo servers that are running, and rebuild your dev containers, to use the new Odoo "
                "images. Run [b]otk dev clean[/b] later to remove the earlier builds they still use.\n",
            )
    if failed:
        print_error(f"Pulling [b]{'[/b], [b]'.join(failed)}[/b] failed. See above.\n")
        raise Exit(1)
    print_success("Your images are up to date.\n" if not updated else "Pulled the newer builds.\n")


def get_local_odoo_images() -> list[tuple[str, Arch]]:
    """Get the Odoo images on this machine, like `dylankiss/odoo-dev:19.0`, with their architecture.

    The tags of a Runbot image for one architecture, like `dylankiss/odoo-dev:docker19-arm64`, which building an image
    adds next to the ones of its versions, are left out.
    """
    result = subprocess.run(
        ["docker", "image", "ls", DOCKER_REPOSITORY, "--format", "{{.Tag}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    images: list[tuple[str, Arch]] = []
    arch_suffixes = tuple(f"-{arch}" for arch in get_args(Arch))
    for tag in dict.fromkeys(result.stdout.split()):
        if tag == "<none>" or tag.endswith(arch_suffixes):
            continue
        image = f"{DOCKER_REPOSITORY}:{tag}"
        arch = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Architecture}}", image],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if arch in get_args(Arch):
            images.append((image, cast("Arch", arch)))
    return images


def pull_odoo_images() -> tuple[list[str], list[str]]:
    """Pull the newer builds of the Odoo images on this machine.

    :return: The images that were updated, and the ones that failed.
    """
    versions = {get_image_tag(version): version for version in get_versions().values()}
    updated: list[str] = []
    failed: list[str] = []
    for image, arch in get_local_odoo_images():
        version = versions.get(image)
        if not version:
            console.print(f"Skipping [b]{image}[/b]: this version of odoo-toolkit doesn't support it.")
            continue
        if not get_local_digests(image):
            console.print(f"Skipping [b]{image}[/b]: you built it on this machine. Rebuild it with [b]--build[/b].")
            continue
        previous_id = get_image_id(image)
        try:
            ensure_image(version, build=False, arch=arch, pull=True)
        except Exit:
            failed.append(image)
            continue
        if get_image_id(image) != previous_id:
            updated.append(image)
    return updated, failed


def pull_service_images() -> tuple[list[str], list[str]]:
    """Pull the newer builds of the images of the shared services on this machine, and recreate the running services.

    :return: The images that were updated, and the ones that failed.
    """
    images = [image for image in get_service_images() if get_image_id(image)]
    previous_ids = {image: get_image_id(image) for image in images}
    failed: list[str] = []
    for image in images:
        with TransientProgress() as progress:
            progress.add_task(f"Pulling {image}", total=None)
            result = subprocess.run(["docker", "pull", "--quiet", image], capture_output=True, text=True, check=False)
        if result.returncode:
            print_error(f"Pulling [b]{image}[/b] failed.", result.stderr.strip())
            failed.append(image)
    updated = [image for image in images if get_image_id(image) != previous_ids[image]]
    if updated and get_db_port() is not None:
        # Docker Compose recreates the services whose image changed.
        start_services(pgadmin=_is_running(PGADMIN_CONTAINER))
    for image in updated:
        remove_replaced_image(image, previous_ids[image])
    return updated, failed


def _is_running(container: str) -> bool:
    result = subprocess.run(
        ["docker", "ps", "--filter", f"name=^{container}$", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return container in result.stdout.split()
