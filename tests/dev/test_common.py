import subprocess
from typing import Any

import pytest
from typer import Exit

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


class FakeImageCommands:
    """Stand in for `docker image inspect` and `docker image rm`, with an image whose ID changes on pull."""

    def __init__(self, image_id: str | None, *, pulled_id: str | None = None, fail: bool = False) -> None:
        self.image_id = image_id
        # The ID after `docker pull` or `docker compose up --pull always`, unless it fails.
        self.pulled_id = pulled_id
        self.fail = fail
        self.removed: list[str] = []

    def run(self, cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["docker", "pull"] or cmd[:2] == ["docker", "compose"]:
            if self.fail:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="manifest unknown")
            self.image_id = self.pulled_id
        if cmd[:3] == ["docker", "image", "inspect"]:
            found = self.image_id is not None
            return subprocess.CompletedProcess(cmd, 0 if found else 1, stdout=f"{self.image_id}\n" if found else "")
        if cmd[:3] == ["docker", "image", "rm"]:
            self.removed.append(cmd[3])
        return subprocess.CompletedProcess(cmd, 0, stdout="")


def test_pull_removes_the_build_it_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    docker = FakeImageCommands("sha256:old", pulled_id="sha256:new")
    monkeypatch.setattr(dev_common.subprocess, "run", docker.run)

    assert dev_common._pull(TAG, "arm64")  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert docker.removed == ["sha256:old"]


def test_a_failed_pull_removes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    docker = FakeImageCommands("sha256:old", fail=True)
    monkeypatch.setattr(dev_common.subprocess, "run", docker.run)

    assert not dev_common._pull(TAG, "arm64")  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert docker.removed == []


def test_start_services_removes_the_builds_a_pull_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    docker = FakeImageCommands("sha256:old", pulled_id="sha256:new")
    monkeypatch.setattr(dev_common.subprocess, "run", docker.run)
    monkeypatch.setattr(dev_common, "check_docker", lambda: None)

    def service_images() -> list[str]:
        return ["postgres:17"]

    monkeypatch.setattr(dev_common, "get_service_images", service_images)

    dev_common.start_services(pull=True)

    assert docker.removed == ["sha256:old"]


def test_a_pull_removes_the_build_it_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    docker = FakeImageCommands("sha256:old")
    monkeypatch.setattr(dev_common.subprocess, "run", docker.run)

    docker.image_id = "sha256:new"
    dev_common.remove_replaced_image(TAG, "sha256:old")
    # Nothing replaced, or nothing there before: nothing to remove.
    dev_common.remove_replaced_image(TAG, "sha256:new")
    dev_common.remove_replaced_image(TAG, None)

    assert docker.removed == ["sha256:old"]


def test_check_docker_says_why_docker_cant_be_reached(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stderr="Cannot connect to the Docker daemon at unix:///x.sock")

    def which(_: str) -> str:
        return "/usr/bin/docker"

    monkeypatch.setattr(dev_common.shutil, "which", which)
    monkeypatch.setattr(dev_common.subprocess, "run", run)
    monkeypatch.setenv("REMOTE_CONTAINERS", "true")

    with pytest.raises(dev_common.Exit):
        dev_common.check_docker()

    output = " ".join(capsys.readouterr().err.split())
    # Docker's own reason, and that a dev container has no Docker.
    assert "Cannot connect to the Docker daemon" in output
    assert "run it in a terminal on your machine" in output


def test_check_docker_says_when_docker_isnt_installed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def which(_: str) -> None:
        return None

    monkeypatch.setattr(dev_common.shutil, "which", which)
    monkeypatch.setenv("REMOTE_CONTAINERS", "true")

    with pytest.raises(dev_common.Exit):
        dev_common.check_docker()

    output = " ".join(capsys.readouterr().err.split())
    assert "This command needs Docker" in output
    assert "run it in a terminal on your machine" in output


def test_start_services_shows_what_the_proxy_logged_when_starting_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1 if "up" in cmd else 0, "", "compose failed")

    monkeypatch.setattr(dev_common.subprocess, "run", fake_run)
    monkeypatch.setattr(dev_common, "check_docker", lambda: None)
    monkeypatch.setattr(dev_common, "get_proxy_logs", lambda: "FATAL: bad squid.conf")

    with pytest.raises(Exit):
        dev_common.start_services()

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "FATAL: bad squid.conf" in output
    assert "compose failed" in output


def test_start_services_restarts_the_proxy_when_a_domain_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    restarts: list[bool] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def reload_proxy(*, restart: bool = False) -> bool:
        restarts.append(restart)
        return True

    monkeypatch.setattr(dev_common.subprocess, "run", fake_run)
    monkeypatch.setattr(dev_common, "check_docker", lambda: None)
    monkeypatch.setattr(dev_common, "write_allowed_domains", lambda: (dev_common.Path("/x"), True))
    monkeypatch.setattr(dev_common, "reload_proxy", reload_proxy)

    dev_common.start_services()

    assert restarts == [True]
