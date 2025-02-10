from typer import Typer

from .setup import app as setup_app

app = Typer(no_args_is_help=True)
app.add_typer(setup_app)


@app.callback()
def callback() -> None:
    """Work with an :ringed_planet: Odoo Multiverse environment.

    The following commands allow you to set up and Odoo Multiverse environment and perform several useful actions inside
    the environment.
    """
