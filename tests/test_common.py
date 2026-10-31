from odoo_toolkit.common import normalize_list_option


def test_normalize_list_option_drops_empty_items() -> None:
    assert normalize_list_option(["fr, nl,", "de"]) == ["fr", "nl", "de"]
