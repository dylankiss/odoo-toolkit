from odoo_toolkit.common import USER_AGENT, VERSION


def test_user_agent_names_the_toolkit_and_its_version() -> None:
    assert VERSION != "unknown"
    assert f"odoo-toolkit/{VERSION} (+https://github.com/dylankiss/odoo-toolkit)" == USER_AGENT

