import json
from pathlib import Path

import pytest
import requests
from typer.testing import CliRunner

from odoo_toolkit import update_check
from odoo_toolkit.common import VERSION
from odoo_toolkit.main import app


class FakeResponse:
    def __init__(self, version: str) -> None:
        self.version = version

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, dict[str, str]]:
        return {"info": {"version": self.version}}


def test_is_newer() -> None:
    assert update_check.is_newer("4.2.0", "4.1.2")
    assert update_check.is_newer("5.0", "4.9.9")
    assert not update_check.is_newer("4.1.2", "4.1.2")
    assert not update_check.is_newer("4.1.1", "4.1.2")
    # Development versions and unknown ones are never told about a new version.
    assert not update_check.is_newer("4.2.0", "4.1.3.dev2+gabc123")
    assert not update_check.is_newer("4.2.0", "unknown")


class FakeDistribution:
    def __init__(self, direct_url: str | None) -> None:
        self.direct_url = direct_url

    def read_text(self, filename: str) -> str | None:
        return self.direct_url if filename == "direct_url.json" else None


def test_is_installed_from_index(monkeypatch: pytest.MonkeyPatch) -> None:
    def from_pypi(_name: str) -> FakeDistribution:
        return FakeDistribution(None)

    def from_folder(_name: str) -> FakeDistribution:
        return FakeDistribution('{"url": "file:///code/odoo-toolkit", "dir_info": {"editable": true}}')

    def missing(name: str) -> FakeDistribution:
        raise update_check.PackageNotFoundError(name)

    monkeypatch.setattr(update_check, "distribution", from_pypi)
    assert update_check.is_installed_from_index()
    # A linked or Git install only has the version it was installed with, so it says nothing about new releases.
    monkeypatch.setattr(update_check, "distribution", from_folder)
    assert not update_check.is_installed_from_index()
    monkeypatch.setattr(update_check, "distribution", missing)
    assert not update_check.is_installed_from_index()


def test_get_latest_version_asks_pypi_once_a_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_file = tmp_path / "cache" / "latest-version.json"
    requested: list[str] = []

    def get(url: str, **_kwargs: object) -> FakeResponse:
        requested.append(url)
        return FakeResponse("4.2.0")

    monkeypatch.setattr(update_check.requests, "get", get)

    assert update_check.get_latest_version(cache_file, now=1000) == "4.2.0"
    assert json.loads(cache_file.read_text()) == {"checked": 1000, "version": "4.2.0"}
    # Within a day, the remembered version is used.
    assert update_check.get_latest_version(cache_file, now=1000 + update_check.CHECK_INTERVAL - 1) == "4.2.0"
    assert len(requested) == 1
    assert update_check.get_latest_version(cache_file, now=1000 + update_check.CHECK_INTERVAL) == "4.2.0"
    assert len(requested) == 2


def test_get_latest_version_without_pypi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def get(_url: str, **_kwargs: object) -> FakeResponse:
        raise requests.ConnectionError

    monkeypatch.setattr(update_check.requests, "get", get)

    assert update_check.get_latest_version(tmp_path / "latest-version.json") is None
    assert not (tmp_path / "latest-version.json").exists()


def test_version_is_printed_on_stdout() -> None:
    result = CliRunner().invoke(app, ["--version"])

    # So `$(otk --version)` gets it, while the other output goes to stderr.
    assert result.exit_code == 0
    assert result.stdout.strip() == VERSION
