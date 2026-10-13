import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from odoo_toolkit.common import normalize_list_option
from odoo_toolkit.main import app
from odoo_toolkit.po.common import get_cldr_lang, get_odoo_lang
from odoo_toolkit.wl import common as wl_common
from odoo_toolkit.wl import update_teams as wl_update_teams
from odoo_toolkit.wl.config import WeblateConfig


def test_normalize_list_option_drops_empty_items() -> None:
    assert normalize_list_option(["fr, nl,", "de"]) == ["fr", "nl", "de"]


def test_language_codes_of_weblate_and_odoo() -> None:
    assert get_cldr_lang("zh_CN") == "zh_Hans"
    assert get_odoo_lang("zh_Hans") == "zh_CN"
    assert get_odoo_lang("sr_Latn") == "sr@latin"
    assert get_odoo_lang("fr") == "fr"


def test_config_forgets_the_languages_of_modules_without_them(tmp_path: Path) -> None:
    module = tmp_path / "sale"
    (module / "i18n").mkdir(parents=True)
    (module / "i18n" / "sale.pot").write_text("")
    config = WeblateConfig(tmp_path / ".weblate.json")
    config.update_module(module, "odoo-19", ["fr"])
    config.update_module(module, "odoo-19", [])
    config.save()

    component = json.loads((tmp_path / ".weblate.json").read_text())["projects"]["odoo-19"][0]
    assert "language_regex" not in component


class FakeTeamsApi:
    """Answer the requests of `update-teams`, and remember the changes."""

    def __init__(self, teams: list[dict[str, Any]]) -> None:
        self.teams = teams
        self.changes: list[tuple[str, str]] = []

    def get_generator(self, _: object, endpoint: str, **__: object) -> list[dict[str, Any]]:
        if endpoint == wl_common.WEBLATE_GROUPS_ENDPOINT:
            return self.teams
        return [{"id": 7, "slug": "odoo-19", "name": "Reviewer"}]

    def request(self, _: object, method: str, endpoint: str, **__: object) -> dict[str, Any]:
        self.changes.append((method, endpoint))
        return {}


@pytest.fixture
def teams_api(monkeypatch: pytest.MonkeyPatch) -> FakeTeamsApi:
    api = FakeTeamsApi([
        {"id": 1, "name": "French", "project_selection": 0, "language_selection": 0, "defining_project": None},
        {"id": 2, "name": "French all", "project_selection": 1, "language_selection": 0, "defining_project": None},
    ])
    monkeypatch.setattr(wl_update_teams, "WeblateApi", lambda: api)
    return api


def test_update_teams_leaves_teams_with_all_projects_alone(teams_api: FakeTeamsApi) -> None:
    result = CliRunner().invoke(app, ["wl", "update-teams", "French*", "-p", "+odoo-19"])

    assert result.exit_code == 0, result.output
    # Only the team with its own list of projects gets the project, and no selection is changed.
    assert teams_api.changes == [("POST", "/api/groups/1/projects/")]
    assert "has access to all projects" in result.output


def test_update_teams_changes_nothing_for_unknown_projects(teams_api: FakeTeamsApi) -> None:
    result = CliRunner().invoke(app, ["wl", "update-teams", "French*", "-p", "+odoo-91"])

    assert result.exit_code == 1
    assert "odoo-91" in result.output
    assert teams_api.changes == []
