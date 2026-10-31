from pathlib import Path
from typing import Annotated

from typer import Argument, Exit, Typer

from odoo_toolkit.common import print_command_title, print_error, print_success, print_warning

from .config import get_linked_repos, require_version_dir
from .setup import configure_language_server, link_repo, update_scanned_repos

app = Typer()


@app.command()
def link(
    repo: Annotated[str, Argument(help="The shared or pinned repository to link, like `odoofin`.")],
) -> None:
    """Link :link: a shared or pinned repository into the current Odoo Multiverse version directory.

    Pinned repositories live in the version they run on. Link them into another version when you work on
    code that interacts with them, and unlink them again with `otk mv unlink` when you're done. To always link a pinned
    repository into a version, add the version to its `also_in` setting in `multiverse.toml`.
    """
    print_command_title(":link: Link Repository")
    version_dir = require_version_dir()
    if not (version_dir.parent / repo / ".git").exists():
        print_error(f"[b]{repo}[/b] isn't cloned in [u]{version_dir.parent}[/u]. Set it up with [b]otk mv setup[/b].\n")
        raise Exit(1)
    if not link_repo(version_dir, repo):
        print_error(f"[u]{version_dir / repo}[/u] already exists as a regular folder.\n")
        raise Exit(1)
    if not (version_dir / repo).is_symlink():
        print_success(f"[b]{repo}[/b] lives in [u]{version_dir}[/u] already, since it runs on this version.\n")
        return
    _update_configs(version_dir)
    print_success(f"Linked [b]{repo}[/b] into [u]{version_dir}[/u].\n")


@app.command()
def unlink(
    repo: Annotated[str, Argument(help="The linked repository to remove from this version directory.")],
) -> None:
    """Unlink :broken_chain: a shared or pinned repository from the current Odoo Multiverse version directory.

    Only the link is removed: the repository itself stays, and other versions keep using it.
    """
    print_command_title(":broken_chain: Unlink Repository")
    version_dir = require_version_dir()
    if repo not in get_linked_repos(version_dir):
        if (version_dir / repo / ".git").is_dir():
            print_error(f"[b]{repo}[/b] lives in [u]{version_dir}[/u], since it runs on this version.\n")
        else:
            print_error(f"[b]{repo}[/b] isn't linked into [u]{version_dir}[/u].\n")
        raise Exit(1)
    (version_dir / repo).unlink()
    _update_configs(version_dir)
    print_success(f"Unlinked [b]{repo}[/b] from [u]{version_dir}[/u].\n")


def _update_configs(version_dir: Path) -> None:
    """Update what depends on the repositories in a version directory: Source Control and the Odoo language server.

    The dev container's configuration can't be changed from inside it, so it's only updated on request.
    """
    update_scanned_repos(version_dir)
    if (version_dir / "odools.toml").is_file():
        configure_language_server(version_dir)
    if (version_dir / ".devcontainer" / "devcontainer.json").is_file():
        print_warning(
            "The dev container still has the repositories it had. Update it with [b]otk mv setup --update-config[/b] "
            "on your machine, and rebuild it.\n",
        )
