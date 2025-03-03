from typer import Typer

from .reset import app as reset_app
from .setup import app as setup_app
from .switch import app as switch_app

app = Typer(no_args_is_help=True)
app.add_typer(setup_app)
app.add_typer(reset_app)
app.add_typer(switch_app)


@app.callback()
def callback() -> None:
    """Work with an :ringed_planet: Odoo Multiverse environment.

    The following commands allow you to set up and Odoo Multiverse environment and perform several useful actions inside
    the environment.
    """
