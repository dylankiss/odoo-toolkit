from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Annotated

from typer import Exit, Option, Typer

from .common import print
from .dev import app as dev_app
from .multiverse import app as multiverse_app
from .po import app as po_app
from .tx import app as tx_app

# The main app to register all the commands with.
app = Typer(no_args_is_help=True, rich_markup_mode="markdown")
app.add_typer(po_app, name="po")
app.add_typer(dev_app, name="dev")
app.add_typer(tx_app, name="tx")
app.add_typer(multiverse_app)


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
