from typer import Typer

from .db import app as db_app
from .run import app as run_app
from .stop import app as stop_app

app = Typer(no_args_is_help=True)
app.add_typer(run_app)
app.add_typer(stop_app)
app.add_typer(db_app)


@app.callback()
def callback() -> None:
    """Run :computer: Odoo in :whale: Docker, in the same environment as Runbot.

    Run these commands inside an Odoo Multiverse version directory (like `19.0`). Odoo runs in a container per run,
    with your source code mounted, next to shared PostgreSQL and Mailpit containers.
    \n\n
    These tools require Docker to be installed on your system.
    """
