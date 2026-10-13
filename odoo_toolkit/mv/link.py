from pathlib import Path
from typing import Annotated

from typer import Argument, Exit, Typer

from odoo_toolkit.common import print_command_title, print_error, print_success

from .config import find_version_dir, get_linked_repos
from .setup import link_repo, update_scanned_repos

app = Typer()


@app.command()
def link(
    repo: Annotated[str, Argument(help="The shared or pinned repository to link, like `odoofin`.")],
) -> None:
    """Link :link: a shared or pinned repository into the current Odoo Multiverse version directory.

    Pinned repositories are only linked into the version they run on. Link them into another version when you work on
    code that interacts with them, and unlink them again with `otk mv unlink` when you're done. To always link a pinned
    repository into a version, add the version to its `also_in` setting in `multiverse.toml`.
    """
    print_command_title(":link: Link Repository")
    version_dir = _get_version_dir()
    if not (version_dir.parent / repo / ".git").exists():
        print_error(f"[b]{repo}[/b] isn't cloned in [u]{version_dir.parent}[/u]. Set it up with [b]otk mv setup[/b].\n")
        raise Exit(1)
    if not link_repo(version_dir, repo):
        print_error(f"[u]{version_dir / repo}[/u] already exists as a regular folder.\n")
        raise Exit(1)
    update_scanned_repos(version_dir)
    print_success(f"Linked [b]{repo}[/b] into [u]{version_dir}[/u].\n")


@app.command()
def unlink(
    repo: Annotated[str, Argument(help="The linked repository to remove from this version directory.")],
) -> None:
    """Unlink :broken_chain: a shared or pinned repository from the current Odoo Multiverse version directory.

    Only the link is removed: the repository itself stays, and other versions keep using it.
    """
    print_command_title(":broken_chain: Unlink Repository")
    version_dir = _get_version_dir()
    if repo not in get_linked_repos(version_dir):
        print_error(f"[b]{repo}[/b] isn't linked into [u]{version_dir}[/u].\n")
        raise Exit(1)
    (version_dir / repo).unlink()
    update_scanned_repos(version_dir)
    print_success(f"Unlinked [b]{repo}[/b] from [u]{version_dir}[/u].\n")


def _get_version_dir() -> Path:
    version_dir = find_version_dir()
    if not version_dir:
        print_error("Run this command inside an Odoo Multiverse version directory (like [u]~/code/odoo-mv/19.0[/u]).\n")
        raise Exit(1)
    return version_dir
