import pytest

from odoo_toolkit.common import USER_AGENT, VERSION
from odoo_toolkit.wl import common as wl_common


def test_user_agent_names_the_toolkit_and_its_version() -> None:
    assert VERSION != "unknown"
    assert f"odoo-toolkit/{VERSION} (+https://github.com/dylankiss/odoo-toolkit)" == USER_AGENT


def test_weblate_api_sends_user_agent_and_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wl_common, "WEBLATE_API_TOKEN", "secret")

    api = wl_common.WeblateApi()

    for session in (api.session, api.json_session):
        assert session.headers["User-Agent"] == USER_AGENT
        assert session.headers["Authorization"] == "Bearer secret"
