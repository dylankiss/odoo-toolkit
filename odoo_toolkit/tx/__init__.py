from typer import Typer

from .add import app as add_app

app = Typer(no_args_is_help=True)
app.add_typer(add_app)


@app.callback()
def callback() -> None:
    """Work with :earth_africa: Transifex.

    The following commands allow you to modify the Transifex config files.
    """
