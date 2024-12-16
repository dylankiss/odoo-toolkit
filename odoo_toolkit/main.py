from typing import Annotated

from typer import Exit, Option

from .common import app, print
from .dev import dev_app
from .multiverse import multiverse
from .po import update_po
from .pot import export_pot

VERSION = "0.1.22"


@app.callback(invoke_without_command=True)
def main(version: Annotated[bool, Option("--version", help="Show the version and exit.")] = False):
    """
    🧰 Odoo Toolkit

    This toolkit contains several useful tools for Odoo development.
    """
    if version:
        print(f"Odoo Toolkit {VERSION}")
        raise Exit()
