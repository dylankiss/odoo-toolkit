from typer import Typer

from .start import app as start_app
from .start_db import app as start_db_app
from .stop import app as stop_app

app = Typer(no_args_is_help=True)
app.add_typer(start_app)
app.add_typer(start_db_app)
app.add_typer(stop_app)


@app.callback()
def callback() -> None:
    """Launch an :computer: Odoo Development Server using :whale: Docker.

    The following commands allow you to automatically start and stop a fully configured Docker container to run your
    Odoo server(s) during development.
    \n\n
    These tools require Docker Desktop to be installed on your system.
    \n\n
    The Docker container is configured to resemble Odoo's CI or production servers and thus tries to eliminate
    discrepancies between your local system and the CI or production server.
    """
