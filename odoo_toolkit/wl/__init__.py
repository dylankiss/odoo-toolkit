from typer import Typer

from .copy import app as copy_app

app = Typer(no_args_is_help=True)
app.add_typer(copy_app)


@app.callback()
def callback() -> None:
    """Sync translations with Weblate."""
