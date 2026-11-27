import subprocess

import pytest
from typer import Exit
from typer.testing import CliRunner

from odoo_toolkit.dev import pull
from odoo_toolkit.main import app
from odoo_toolkit.versions import OdooVersion


class FakeDocker:
    """Stand in for the Docker commands of `otk dev pull`, with images that get a new ID when they're pulled."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        odoo_tags: str = "19.0\n17.0\n9.0\ndocker19-arm64\n<none>\n",
        services: tuple[str, ...] = ("pgvector/pgvector:pg18", "axllent/mailpit:v1.27"),
        newer: tuple[str, ...] = (),
        built_here: tuple[str, ...] = (),
        failing: tuple[str, ...] = (),
        db_running: bool = False,
    ) -> None:
        self.ids = {f"dylankiss/odoo-dev:{tag}": "old" for tag in odoo_tags.split() if tag != "<none>"}
        self.inspected: list[str] = []
        self.ids |= dict.fromkeys(services, "old")
        self.newer = newer
        self.built_here = built_here
        self.failing = failing
        self.odoo_tags = odoo_tags
        self.pulled: list[str] = []
        self.removed: list[tuple[str, str | None]] = []
        self.started: list[bool] = []
        monkeypatch.setattr(pull.subprocess, "run", self.run)
        monkeypatch.setattr(pull, "check_docker", lambda: None)
        monkeypatch.setattr(pull, "get_image_id", self.ids.get)
        monkeypatch.setattr(pull, "get_local_digests", self.digests)
        monkeypatch.setattr(pull, "get_service_images", lambda: [*services, "dpage/pgadmin4:9"])
        monkeypatch.setattr(pull, "ensure_image", self.ensure_image)
        monkeypatch.setattr(pull, "remove_replaced_image", self.remove_replaced_image)
        monkeypatch.setattr(pull, "get_db_port", lambda: 5432 if db_running else None)
        monkeypatch.setattr(pull, "start_services", self.start_services)

    def digests(self, image: str) -> set[str]:
        return set() if image in self.built_here else {"sha256:pulled"}

    def pull(self, image: str) -> bool:
        self.pulled.append(image)
        if image in self.failing:
            return False
        if image in self.newer:
            self.ids[image] = "new"
        return True

    def ensure_image(self, version: OdooVersion, *, build: bool, arch: str, pull: bool) -> None:
        assert not build
        assert pull
        assert arch == "arm64"
        if not self.pull(f"dylankiss/odoo-dev:{version.name}"):
            raise Exit(1)

    def remove_replaced_image(self, image: str, previous_id: str | None) -> None:
        self.removed.append((image, previous_id))

    def start_services(self, *, pgadmin: bool) -> None:
        self.started.append(pgadmin)

    def run(self, cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if cmd[:3] == ["docker", "image", "ls"]:
            return subprocess.CompletedProcess(cmd, 0, self.odoo_tags, "")
        if cmd[:3] == ["docker", "image", "inspect"]:
            self.inspected.append(cmd[-1])
            return subprocess.CompletedProcess(cmd, 0, "arm64\n", "")
        if cmd[:2] == ["docker", "pull"]:
            ok = self.pull(cmd[-1])
            return subprocess.CompletedProcess(cmd, 0 if ok else 1, "", "" if ok else "manifest unknown")
        if cmd[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(cmd, 0, "otk-pgadmin\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def test_pull_updates_the_images_you_have(monkeypatch: pytest.MonkeyPatch) -> None:
    docker = FakeDocker(monkeypatch, newer=("dylankiss/odoo-dev:19.0", "axllent/mailpit:v1.27"))

    result = CliRunner().invoke(app, ["dev", "pull"])

    assert result.exit_code == 0, result.output
    # pgAdmin isn't on this machine, and 9.0 isn't supported anymore.
    assert docker.pulled == [
        "dylankiss/odoo-dev:19.0",
        "dylankiss/odoo-dev:17.0",
        "pgvector/pgvector:pg18",
        "axllent/mailpit:v1.27",
    ]
    assert "Skipping dylankiss/odoo-dev:9.0" in result.output
    # The tag of a build for one architecture isn't a version.
    assert "docker19-arm64" not in result.output
    assert "dylankiss/odoo-dev:docker19-arm64" not in docker.inspected
    assert "Updated: dylankiss/odoo-dev:19.0, axllent/mailpit:v1.27" in result.output
    assert "rebuild your dev containers" in result.output
    # The earlier build of a service goes, while the one of an Odoo image is removed when it's pulled.
    assert docker.removed == [("axllent/mailpit:v1.27", "old")]
    # The services aren't running, so they aren't started.
    assert docker.started == []


def test_pull_recreates_the_running_services_whose_image_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    docker = FakeDocker(monkeypatch, odoo_tags="", newer=("pgvector/pgvector:pg18",), db_running=True)

    result = CliRunner().invoke(app, ["dev", "pull"])

    assert result.exit_code == 0, result.output
    assert docker.started == [True]
    assert "rebuild your dev containers" not in result.output


def test_pull_says_when_everything_is_up_to_date(monkeypatch: pytest.MonkeyPatch) -> None:
    docker = FakeDocker(monkeypatch, db_running=True)

    result = CliRunner().invoke(app, ["dev", "pull"])

    assert result.exit_code == 0, result.output
    assert "Your images are up to date" in result.output
    assert docker.started == []
    assert docker.removed == []


def test_pull_leaves_the_images_you_built_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    docker = FakeDocker(monkeypatch, odoo_tags="19.0\n", built_here=("dylankiss/odoo-dev:19.0",))

    result = CliRunner().invoke(app, ["dev", "pull"])

    assert result.exit_code == 0, result.output
    assert "you built it on this machine" in result.output
    assert "dylankiss/odoo-dev:19.0" not in docker.pulled


def test_pull_fails_when_an_image_does(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDocker(monkeypatch, odoo_tags="19.0\n", failing=("dylankiss/odoo-dev:19.0", "pgvector/pgvector:pg18"))

    result = CliRunner().invoke(app, ["dev", "pull"])

    assert result.exit_code == 1
    assert "manifest unknown" in result.output
    assert "Pulling dylankiss/odoo-dev:19.0, pgvector/pgvector:pg18 failed" in result.output
