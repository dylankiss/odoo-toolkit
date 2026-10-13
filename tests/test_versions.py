import importlib.util
from pathlib import Path

import pytest

from odoo_toolkit.versions import get_versions, version_sort_key

SCRIPT = Path(__file__).parent.parent / "scripts" / "sync_runbot.py"
spec = importlib.util.spec_from_file_location("sync_runbot", SCRIPT)
assert spec
assert spec.loader
sync_runbot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync_runbot)


def test_version_sort_key_orders_saas_and_master() -> None:
    versions = ["master", "saas-19.2", "20.0", "18.0", "19.0", "saas-19.10"]
    assert sorted(versions, key=version_sort_key) == ["18.0", "19.0", "saas-19.2", "saas-19.10", "20.0", "master"]


def test_registry_is_sorted_and_complete() -> None:
    versions = get_versions()
    assert list(versions) == sorted(versions, key=version_sort_key)
    assert "master" in versions
    for version in versions.values():
        assert version.image
        assert version.ubuntu
        assert version.python
        assert (SCRIPT.parent.parent / "odoo_toolkit" / "docker" / "runbot" / f"{version.image}.Dockerfile").is_file()


VERSIONS_PAGE = """
<table class="table">
  <thead><tr><th>Version</th><th>Docker file</th><th>Host</th><th>os_version</th><th>python_version</th></tr></thead>
  <tr class="o_row">
    <td> master </td>
    <td><a class="" href="/runbot/dockerfile?id=52">DockerMaster</a></td>
    <td>116</td>
    <td> Ubuntu 26.04 LTS </td><td> 3.14.4 </td><td> 150.0.7871.114 </td>
  </tr>
  <tr>
    <td> saas-19.2 </td>
    <td><a class="" href="/runbot/dockerfile?id=148">Docker191</a></td>
    <td>116</td>
    <td> Ubuntu 24.04.4 LTS </td><td> 3.12.3 </td><td> 150.0.7871.114 </td>
  </tr>
  <tr>
    <td> 16.0 </td>
    <td><a class="" href="/runbot/dockerfile?id=31">Docker16</a></td>
    <td></td><td></td><td></td><td></td>
  </tr>
</table>
"""


def test_parse_versions_page() -> None:
    assert sync_runbot.parse_versions_page(VERSIONS_PAGE) == [
        sync_runbot.RunbotVersion(name="master", dockerfile_id=52, image="DockerMaster", python="3.14"),
        sync_runbot.RunbotVersion(name="saas-19.2", dockerfile_id=148, image="Docker191", python="3.12"),
        sync_runbot.RunbotVersion(name="16.0", dockerfile_id=31, image="Docker16", python=""),
    ]


def test_parse_ubuntu_release() -> None:
    assert sync_runbot.parse_ubuntu_release("# FROM ubuntu:noble\nFROM ubuntu:noble\n\nENV LANG C.UTF-8\n") == "noble"
    with pytest.raises(ValueError, match="not based on an Ubuntu image"):
        sync_runbot.parse_ubuntu_release("FROM debian:bookworm\n")
