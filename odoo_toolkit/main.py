from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Annotated

from typer import Exit, Option

from .common import app, print
from .dev import dev_app  # noqa: F401
from .multiverse import multiverse  # noqa: F401
from .po import create_po, merge_po, update_po  # noqa: F401
from .pot import export_pot  # noqa: F401


@app.callback(invoke_without_command=True)
def main(*, version: Annotated[bool, Option("--version", help="Show the version and exit.")] = False) -> None:
    """🧰 Odoo Toolkit

    This toolkit contains several useful tools for Odoo development.
    """  # noqa: D400, D415
    if version:
        try:
            print(package_version("odoo-toolkit"))
        except PackageNotFoundError:
            print("Version could not be detected")
        raise Exit
