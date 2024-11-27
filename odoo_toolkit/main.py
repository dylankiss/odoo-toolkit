from .common import app
from .dev import dev_app
from .pot import export_pot
from .po import update_po


@app.callback()
def callback() -> None:
    """
    🧰 Odoo Toolkit

    This toolkit contains several useful tools for Odoo development.
    """
