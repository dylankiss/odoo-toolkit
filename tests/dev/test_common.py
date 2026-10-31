import subprocess
from typing import Any

import pytest

from odoo_toolkit.dev import common as dev_common
from odoo_toolkit.versions import get_versions

TAG = "dylankiss/odoo-dev:19.0"


class FakeDocker:
    """Stand in for the Docker commands `ensure_image` runs."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        local: dict[str, str] | None,
        remote: str | None,
        pull_fails: bool = False,
    ) -> None:
        self.local = local
        self.remote = remote
        self.pull_fails = pull_fails
        self.pulled: list[str] = []
        self.built: list[Any] = []
        monkeypatch.setattr(dev_common, "check_docker", lambda: None)
        monkeypatch.setattr(dev_common, "get_image_labels", self.labels)
        monkeypatch.setattr(dev_common, "get_remote_image_layout", self.remote_layout)
        monkeypatch.setattr(dev_common, "_pull", self.pull)
        monkeypatch.setattr(dev_common, "build_image", self.build)

    def labels(self, *_: object) -> dict[str, str] | None:
        return self.local

    def remote_layout(self, *_: object) -> str | None:
        return self.remote

    def pull(self, tag: str, _: str) -> bool:
        self.pulled.append(tag)
        if self.pull_fails:
            return False
        self.local = {dev_common.LAYOUT_LABEL: self.remote or ""}
        return True

    def build(self, image: str, arch: str, *, user_ids: tuple[int, int] | None) -> None:
        self.built.append((image, arch, user_ids))
        self.local = {dev_common.LAYOUT_LABEL: "2", dev_common.UID_LABEL: str(user_ids[0] if user_ids else 1000)}


def test_image_is_pulled_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev_common, "get_user_ids", lambda: None)
    docker = FakeDocker(monkeypatch, local=None, remote="2")

    dev_common.ensure_image(get_versions()["19.0"], build=False, arch="arm64")
    dev_common.ensure_image(get_versions()["19.0"], build=False, arch="arm64")

    assert docker.pulled == [TAG]
    # Unless asked to pull the latest one.
    dev_common.ensure_image(get_versions()["19.0"], build=False, arch="arm64", pull=True)
    assert docker.pulled == [TAG, TAG]


def test_old_images_are_never_pulled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev_common, "get_user_ids", lambda: None)
    docker = FakeDocker(monkeypatch, local=None, remote=None)

    with pytest.raises(dev_common.Exit):
        dev_common.ensure_image(get_versions()["19.0"], build=False, arch="arm64")
    assert docker.pulled == []


def test_a_failed_pull_says_so_once(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(dev_common, "get_user_ids", lambda: None)
    FakeDocker(monkeypatch, local=None, remote="2", pull_fails=True)

    with pytest.raises(dev_common.Exit):
        dev_common.ensure_image(get_versions()["19.0"], build=False, arch="arm64")
    # The pull printed why it failed, so there's no second message saying there is no image.
    assert "There is no" not in capsys.readouterr().err


def test_a_failed_pull_keeps_the_image_you_have(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev_common, "get_user_ids", lambda: None)
    FakeDocker(monkeypatch, local={dev_common.LAYOUT_LABEL: "2"}, remote="2", pull_fails=True)

    dev_common.ensure_image(get_versions()["19.0"], build=False, arch="arm64", pull=True)


def test_preparing_an_image_can_fail_without_a_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev_common, "get_user_ids", lambda: None)
    FakeDocker(monkeypatch, local=None, remote=None)

    def build(*_: object, **__: object) -> None:
        msg = "still downloads amd64 packages"
        raise ValueError(msg)

    monkeypatch.setattr(dev_common, "build_image", build)

    with pytest.raises(dev_common.Exit):
        dev_common.ensure_image(get_versions()["19.0"], build=True, arch="arm64")


def test_linux_users_other_than_1000_build_their_own_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev_common, "get_user_ids", lambda: (1001, 1001))
    docker = FakeDocker(monkeypatch, local={dev_common.LAYOUT_LABEL: "2"}, remote="2")

    with pytest.raises(dev_common.Exit):
        dev_common.ensure_image(get_versions()["19.0"], build=False, arch="amd64")
    # The images on Docker Hub run as 1000, so pulling doesn't help.
    assert docker.pulled == []

    dev_common.ensure_image(get_versions()["19.0"], build=True, arch="amd64")
    assert docker.built == [("Docker19", "amd64", (1001, 1001))]
    dev_common.ensure_image(get_versions()["19.0"], build=False, arch="amd64")


def test_get_db_port(monkeypatch: pytest.MonkeyPatch) -> None:
    def docker_port(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, stdout="127.0.0.1:5433\n")

    monkeypatch.setattr(subprocess, "run", docker_port)
    assert dev_common.get_db_port() == 5433
