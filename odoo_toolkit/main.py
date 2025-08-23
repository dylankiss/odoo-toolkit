from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Annotated

from typer import Exit, Option, Typer

from .common import print
from .dev import app as dev_app
from .mv import app as mv_app
from .po import app as po_app
from .wl import app as wl_app

# The main app to register all the commands with.
app = Typer(no_args_is_help=True, rich_markup_mode="markdown")
app.add_typer(po_app, name="po")
app.add_typer(dev_app, name="dev")
app.add_typer(mv_app, name="mv")
app.add_typer(wl_app, name="wl")


@app.callback(invoke_without_command=True)
def main(version: Annotated[bool, Option("--version", help="Show the version and exit.")] = False) -> None:
    """🧰 Odoo Toolkit

    This toolkit contains several useful tools for Odoo development.
    """  # noqa: D400, D415
    if version:
        try:
            print(package_version("odoo-toolkit"))
        except PackageNotFoundError:
            print("Version could not be detected")
        raise Exit
