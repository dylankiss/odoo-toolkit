from .common import app
from .dev import dev_app
from .export_pot import export_pot
from .update_po import update_po


@app.callback()
def callback() -> None:
    """
    🧰 Odoo Toolkit

    This toolkit contains several useful tools for Odoo development.
    """
