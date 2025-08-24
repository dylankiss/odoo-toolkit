from typer import Typer

from .copy import app as copy_app

app = Typer(no_args_is_help=True)
app.add_typer(copy_app)


@app.callback()
def callback() -> None:
    """Work with :earth_africa: Odoo translations on Weblate.

    The following commands allow you to perform some operations on the Weblate server more efficiently than via the UI.

    In order to connect to the Weblate server, you need to have an API key available in the `WEBLATE_API_TOKEN` variable
    in your environment. You can do this either by providing the variable in front of the command each time, like
    `WEBLATE_API_TOKEN=wlu_XXXXXX... otk wl ...` or make the variable available to your execution environment by putting
    it in your `.bashrc`, `.zshrc` or equivalent configuration file for your shell.
    """
