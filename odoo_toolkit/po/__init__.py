from typer import Typer

from .create import app as create_app
from .export import app as export_app
from .merge import app as merge_app
from .update import app as update_app

app = Typer(no_args_is_help=True)
app.add_typer(export_app)
app.add_typer(create_app)
app.add_typer(update_app)
app.add_typer(merge_app)


@app.callback()
def callback() -> None:
    """Work with :japanese_reserved_button: Odoo Translations (.po and .pot files).

    The following commands allow you to export .pot files for Odoo modules, create or update .po files according to
    their (updated) .pot files, or merge multiple .po files into one.
    """
