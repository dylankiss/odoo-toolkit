import re
import subprocess
from dataclasses import dataclass
from typing import Annotated

from rich.table import Table
from typer import Exit, Option, Typer, confirm

from odoo_toolkit.common import console, print_command_title, print_error, print_success

from .common import check_docker
from .images import LAYOUT_LABEL
from .runtime import RUN_LABEL

app = Typer()

# The dev containers `otk mv setup --devcontainer` configures, like `otk-19.0-dev`.
DEV_CONTAINER_NAME = r"^otk-.+-dev$"
SIZE_PATTERN = re.compile(r"^([\d.]+)\s*([kMGT]?)B$")
SIZE_UNITS = {"": 1, "k": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4}


@dataclass(frozen=True)
class Leftover:
    """A container or image of this toolkit that only takes space."""

    kind: str
    # What `docker rm` or `docker image rm` takes.
    ref: str
    name: str
    size: int


@app.command()
def clean(
    yes: Annotated[bool, Option("--yes", "-y", help="Remove them without asking.")] = False,
) -> None:
    """Remove :broom: the Docker containers and images of this toolkit that only take space.

    That's the stopped dev containers, which Visual Studio Code creates again when you open their version, and the
    earlier builds of the Odoo images that nothing uses anymore. Running containers, the current images, and your
    databases, filestores and logins, which are in volumes, stay.
    """
    print_command_title(":broom: Clean Up Docker")
    check_docker()
    leftovers = find_leftovers()
    if not leftovers:
        print_success("Nothing to clean up.\n")
        return

    table = Table(box=None, pad_edge=False)
    table.add_column("What")
    table.add_column("Name", style="bold")
    table.add_column("Size", justify="right")
    for leftover in leftovers:
        table.add_row(leftover.kind, leftover.name, format_size(leftover.size))
    console.print(table, "")

    if not yes and not confirm(f"Remove them, about {format_size(sum(item.size for item in leftovers))}?"):
        raise Exit(1)

    removed, failed = remove_leftovers(leftovers)
    if failed:
        print_error(f"Removing [b]{'[/b], [b]'.join(failed)}[/b] failed. A container may still use it.\n")
        raise Exit(1)
    print_success(f"Freed about {format_size(sum(item.size for item in removed))}.\n")


def find_leftovers() -> list[Leftover]:
    """Find the stopped containers and unused images of this toolkit.

    :return: The containers first, since an image can only be removed once no container uses it.
    """
    containers: dict[str, Leftover] = {}
    for kind, name_filter in (
        ("Dev container", f"name={DEV_CONTAINER_NAME}"),
        ("Odoo container", f"label={RUN_LABEL}"),
    ):
        for status in ("exited", "created"):
            output = _docker(
                "ps", "--all", "--size", "--filter", name_filter, "--filter", f"status={status}",
                "--format", "{{.ID}}\t{{.Names}}\t{{.Size}}",
            )  # fmt: skip
            for line in output.splitlines():
                container_id, name, size = line.split("\t")
                # The size is like `12.3MB (virtual 2.9GB)`: only the first part is the container's own.
                containers.setdefault(container_id, Leftover(kind, container_id, name, parse_size(size.split(" ")[0])))

    images: list[Leftover] = []
    output = _docker(
        "images", "--filter", "dangling=true", "--filter", f"label={LAYOUT_LABEL}",
        "--format", "{{.ID}}\t{{.CreatedSince}}\t{{.Size}}",
    )  # fmt: skip
    for line in output.splitlines():
        image_id, created, size = line.split("\t")
        short_id = image_id.removeprefix("sha256:")[:12]
        images.append(Leftover("Earlier Odoo image", image_id, f"{short_id} ({created})", parse_size(size)))
    return [*containers.values(), *images]


def remove_leftovers(leftovers: list[Leftover]) -> tuple[list[Leftover], list[str]]:
    """Remove containers and images, in that order.

    :return: The ones removed, and the names of the ones that couldn't be.
    """
    removed: list[Leftover] = []
    failed: list[str] = []
    for leftover in leftovers:
        cmd = ["rm"] if leftover.kind.endswith("container") else ["image", "rm"]
        if subprocess.run(["docker", *cmd, leftover.ref], capture_output=True, check=False).returncode:
            failed.append(leftover.name)
        else:
            removed.append(leftover)
    return removed, failed


def parse_size(size: str) -> int:
    """Parse a size as Docker prints it, like `2.31GB`, into bytes, or 0 if it can't be read."""
    if not (match := SIZE_PATTERN.match(size.strip())):
        return 0
    return int(float(match.group(1)) * SIZE_UNITS[match.group(2)])


def format_size(size: float) -> str:
    """Format a size in bytes like Docker does, like `2.3 GB`."""
    for unit in ("B", "kB", "MB", "GB"):
        if size < 1000:  # noqa: PLR2004
            return f"{size:.1f} {unit}"
        size /= 1000
    return f"{size:.1f} TB"


def _docker(*args: str) -> str:
    """Run a Docker command, and get its output, which is empty if it failed."""
    result = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    return result.stdout if not result.returncode else ""
