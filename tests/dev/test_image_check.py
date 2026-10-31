from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from odoo_toolkit.dev import image_check
from odoo_toolkit.dev.image_check import (
    find_outdated_images,
    get_registry_digest,
    get_service_images,
    is_outdated,
    parse_image,
)

IMAGE = "dylankiss/odoo-dev:19.0"


class FakeResponse:
    def __init__(self, data: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> None:
        self.data = data or {}
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return

    def json(self) -> dict[str, str]:
        return self.data


class FakeRegistry:
    """Answer the token and manifest requests of Docker Hub, and count them."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, digest: str | None = "sha256:new") -> None:
        self.digest = digest
        self.requests: list[str] = []
        monkeypatch.setattr(image_check.requests, "get", self.get)
        monkeypatch.setattr(image_check.requests, "head", self.head)

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.requests.append(url)
        assert kwargs["params"] == {"service": "registry.docker.io", "scope": "repository:dylankiss/odoo-dev:pull"}
        return FakeResponse({"token": "abc"})

    def head(self, url: str, **kwargs: object) -> FakeResponse:
        self.requests.append(url)
        headers = kwargs["headers"]
        assert isinstance(headers, dict)
        assert headers["Authorization"] == "Bearer abc"
        if self.digest is None:
            raise requests.ConnectionError
        return FakeResponse(headers={"Docker-Content-Digest": self.digest})


@pytest.fixture(autouse=True)
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        ("dylankiss/odoo-dev:19.0", ("dylankiss/odoo-dev", "19.0")),
        ("pgvector/pgvector:pg18", ("pgvector/pgvector", "pg18")),
        ("postgres", ("library/postgres", "latest")),
        ("ghcr.io/owner/image:1", None),
        ("localhost/odoo:1", None),
        ("registry:5000/odoo:1", None),
        ("dylankiss/odoo-dev@sha256:abc", None),
    ],
)
def test_parse_image_only_knows_docker_hub(image: str, expected: tuple[str, str] | None) -> None:
    assert parse_image(image) == expected


def test_the_digest_of_the_registry_is_remembered_for_a_day(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = FakeRegistry(monkeypatch)

    assert get_registry_digest(IMAGE, now=1000.0) == "sha256:new"
    assert len(registry.requests) == 2
    # Within the day, the registry isn't asked again, even if it has a new build, and after it, it is.
    registry.digest = "sha256:newer"
    assert get_registry_digest(IMAGE, now=1000.0 + 3600) == "sha256:new"
    assert len(registry.requests) == 2
    assert get_registry_digest(IMAGE, now=1000.0 + 25 * 3600) == "sha256:newer"


def test_an_unreachable_registry_gives_no_digest_and_is_not_asked_again_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = FakeRegistry(monkeypatch, digest=None)

    assert get_registry_digest(IMAGE, now=1000.0) is None
    asked = len(registry.requests)
    # Offline, every command would otherwise wait for the registry again.
    assert get_registry_digest(IMAGE, now=1000.0 + 600) is None
    assert len(registry.requests) == asked
    # After an hour, it tries again, and finds the registry back.
    registry.digest = "sha256:new"
    assert get_registry_digest(IMAGE, now=1000.0 + 2 * 3600) == "sha256:new"


def test_without_docker_there_are_no_local_digests(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*_: object, **__: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(image_check.subprocess, "run", run)

    assert image_check.get_local_digests(IMAGE) == set()


def test_a_failing_check_is_swallowed(monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]) -> None:
    def is_outdated(_image: str, **_: object) -> bool:
        raise RuntimeError

    monkeypatch.setattr(image_check, "is_outdated", is_outdated)

    assert find_outdated_images(["a/a:1"]) == []
    assert "Traceback" not in capfd.readouterr().err


@pytest.mark.parametrize(
    ("local", "registry", "expected"),
    [
        ({"sha256:old"}, "sha256:new", True),
        ({"sha256:new"}, "sha256:new", False),
        ({"sha256:old", "sha256:new"}, "sha256:new", False),
        # Built on this machine, or not pulled: nothing to compare.
        (set(), "sha256:new", False),
        ({"sha256:old"}, None, False),
    ],
)
def test_an_image_is_outdated_when_the_registry_has_another_build(
    monkeypatch: pytest.MonkeyPatch, local: set[str], registry: str | None, expected: bool,
) -> None:

    def get_local_digests(_image: str) -> set[str]:
        return local

    def get_registry_digest(_image: str, **_: object) -> str | None:
        return registry

    monkeypatch.setattr(image_check, "get_local_digests", get_local_digests)
    monkeypatch.setattr(image_check, "get_registry_digest", get_registry_digest)

    assert is_outdated(IMAGE) is expected


def test_find_outdated_images_checks_all_of_them(monkeypatch: pytest.MonkeyPatch) -> None:
    outdated = {"a/a:1", "c/c:3"}

    def is_outdated(image: str, **_: object) -> bool:
        return image in outdated

    monkeypatch.setattr(image_check, "is_outdated", is_outdated)

    assert find_outdated_images(["a/a:1", "b/b:2", "c/c:3"]) == ["a/a:1", "c/c:3"]


def test_find_outdated_images_does_not_wait_for_a_slow_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_check, "WAIT_FOR_CHECK", 0.05)

    def slow(_image: str, **_: object) -> bool:
        image_check.time.sleep(1)
        return True

    monkeypatch.setattr(image_check, "is_outdated", slow)

    assert find_outdated_images(["a/a:1"]) == []


def test_the_service_images_come_from_the_compose_file() -> None:
    images = get_service_images()

    assert "axllent/mailpit:v1.27" in images
    assert all(":" in image for image in images)


def test_nothing_is_checked_in_scripts_or_when_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("OTK_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(image_check, "console", SimpleNamespace(is_terminal=True))
    assert not image_check.is_check_disabled()

    monkeypatch.setenv("OTK_NO_UPDATE_CHECK", "1")
    assert image_check.is_check_disabled()
    monkeypatch.delenv("OTK_NO_UPDATE_CHECK")
    monkeypatch.setenv("CI", "true")
    assert image_check.is_check_disabled()
    monkeypatch.delenv("CI")
    monkeypatch.setattr(image_check, "console", SimpleNamespace(is_terminal=False))
    assert image_check.is_check_disabled()


def test_the_notice_names_the_command_to_update(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:

    def is_check_disabled() -> bool:
        return False

    def get_service_images() -> list[str]:
        return ["axllent/mailpit:v1.27"]

    def find_outdated_images(images: list[str], **_: object) -> list[str]:
        return images

    monkeypatch.setattr(image_check, "is_check_disabled", is_check_disabled)
    monkeypatch.setattr(image_check, "get_service_images", get_service_images)
    monkeypatch.setattr(image_check, "find_outdated_images", find_outdated_images)

    image_check.notify_outdated_images(None)

    # The console wraps long lines.
    output = " ".join(capsys.readouterr().err.split())
    assert "axllent/mailpit:v1.27" in output
    assert "otk dev db --pull" in output
