import importlib.util
import runpy
from pathlib import Path
from typing import Any

import pytest
import requests

from odoo_toolkit.mv import configure as mv_configure
from odoo_toolkit.mv.configure import IMAGE_REQUIREMENTS_DIR, parse_requirements
from odoo_toolkit.versions import get_versions

SCRIPT = Path(__file__).parent.parent / "scripts" / "sync_requirements.py"
spec = importlib.util.spec_from_file_location("sync_requirements", SCRIPT)
assert spec
assert spec.loader
sync_requirements = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync_requirements)


@pytest.mark.parametrize(
    ("debian", "upstream"),
    [("2:0.4.27-3", "0.4.27"), ("0.9.9+dfsg-1", "0.9.9"), ("3.4.8-1ubuntu2.4", "3.4.8"), ("20220319-1", "20220319")],
)
def test_get_upstream_version(debian: str, upstream: str) -> None:
    assert sync_requirements.get_upstream_version(debian) == upstream


def test_get_upstream_version_rejects_versions_without_numbers() -> None:
    with pytest.raises(ValueError, match="upstream version"):
        sync_requirements.get_upstream_version("abc")


def test_render_requirements_pins_apt_packages_and_keeps_pip_ones() -> None:
    config = {
        "common": {"apt": ["python3-ldap", "python3-usb"], "pip": ["cn2an"]},
        "Docker17": {"apt": ["python3-qrcode"]},
        "pypi": {"python3-ldap": "python-ldap", "python3-usb": "pyusb"},
        "versions": {"python3-qrcode": "8.0"},
    }
    versions = {"python3-ldap": "3.2.0-4build1", "python3-usb": "1:1.2.1-1", "python3-qrcode": "7.3.1-1"}

    def get_version(_: str, package: str) -> str:
        return versions.get(package, "1.0-1")

    rendered = sync_requirements.render_requirements("Docker17", "jammy", config, get_version)

    lines = [line for line in rendered.splitlines() if not line.startswith("#")]
    assert "pyusb==1.2.1 # python3-usb" in [" ".join(line.split()) for line in lines]
    assert "python-ldap==3.2.0 # python3-ldap" in [" ".join(line.split()) for line in lines]
    # A version in `[versions]` wins over the one of Ubuntu.
    assert "qrcode==8.0 # python3-qrcode" in [" ".join(line.split()) for line in lines]
    # The comments are aligned.
    assert len({line.index("#") for line in lines if "#" in line}) == 1
    assert lines[-1] == "cn2an"
    # Docker19's own section doesn't apply to Docker17.
    assert sync_requirements.render_requirements("Docker19", "noble", config, get_version).count("qrcode") == 0


def test_runbot_packages_leave_out_build_tools_and_urls() -> None:
    packages, phonenumbers = sync_requirements.get_runbot_packages("DockerMaster")

    assert "python3-websocket" in packages
    assert not {"python3-dev", "python3-pip", "python3-venv"} & set(packages)
    assert phonenumbers == "8.12.57"


@pytest.mark.parametrize("image", sorted({version.image for version in get_versions().values()}))
def test_every_image_has_pinned_requirements(image: str) -> None:
    requirements = parse_requirements([IMAGE_REQUIREMENTS_DIR / f"{image}.requirements"])

    assert "websocket-client" in " ".join(requirements)
    # The pip-only packages have no Ubuntu version to pin.
    assert all(
        "==" in requirement for requirement in requirements if not requirement.startswith(("cn2an", "jingtrang"))
    )


class Response:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_get_ubuntu_version_reads_the_version_of_the_package_page(monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []

    def fake_get(url: str, **_: object) -> Response:
        urls.append(url)
        return Response("<h1>Package: python3-ldap (3.4.4-1build2)</h1>")

    sync_requirements.get_ubuntu_version.cache_clear()
    monkeypatch.setattr(sync_requirements.requests, "get", fake_get)

    assert sync_requirements.get_ubuntu_version("noble", "python3-ldap") == "3.4.4-1build2"
    assert urls == ["https://packages.ubuntu.com/noble/python3-ldap"]


def test_get_ubuntu_version_refuses_a_package_the_release_does_not_have(monkeypatch: pytest.MonkeyPatch) -> None:
    sync_requirements.get_ubuntu_version.cache_clear()

    def fake_get(_: str, **__: object) -> Response:
        return Response("<h1>Error</h1>")

    monkeypatch.setattr(sync_requirements.requests, "get", fake_get)

    with pytest.raises(ValueError, match="no package python3-nothing"):
        sync_requirements.get_ubuntu_version("noble", "python3-nothing")


def test_main_writes_the_requirements_of_every_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync_requirements, "IMAGE_REQUIREMENTS_DIR", tmp_path / "requirements")

    def fake_render(image: str, release: str, _: dict[str, Any]) -> str:
        return f"# {image} {release}\n"

    monkeypatch.setattr(sync_requirements, "render_requirements", fake_render)

    assert sync_requirements.main() == 0

    images = {version.image for version in get_versions().values()}
    assert {path.stem for path in (tmp_path / "requirements").glob("*.requirements")} == images
    assert (tmp_path / "requirements" / "Docker19.requirements").read_text() == "# Docker19 noble\n"


def test_running_the_script_writes_the_requirements(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, **_: object) -> Response:
        package = url.rsplit("/", 1)[1]
        return Response(f"<h1>Package: {package} (1.2.3-1)</h1>")

    # The script runs as it does from the command line, in a folder that isn't the one of the toolkit.
    monkeypatch.setattr(mv_configure, "IMAGE_REQUIREMENTS_DIR", tmp_path / "requirements")
    monkeypatch.setattr(requests, "get", fake_get)

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(SCRIPT), run_name="__main__")

    assert exit_info.value.code == 0
    requirements = (tmp_path / "requirements" / "Docker19.requirements").read_text()
    assert "python-ldap==1.2.3" in requirements
