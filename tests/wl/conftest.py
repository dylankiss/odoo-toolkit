import pytest

from odoo_toolkit.wl import common as wl_common


def configure_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure an API token, which the Weblate client needs."""
    monkeypatch.setattr(wl_common, "WEBLATE_API_TOKEN", "secret")


@pytest.fixture
def weblate_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure an API token, which the Weblate client needs."""
    configure_token(monkeypatch)
