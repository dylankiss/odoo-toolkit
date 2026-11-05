import re
from pathlib import Path

import pytest

from odoo_toolkit.dev import images
from odoo_toolkit.dev.images import (
    RUNBOT_DOCKERFILES_DIR,
    get_changed_images,
    get_extra_packages,
    get_image_tags,
    get_images,
    parse_image_layouts,
    render_dockerfile,
    render_packages_layer,
    split_instructions,
)
from odoo_toolkit.versions import get_versions


def chrome_not_built_for_arm64(_: str) -> str:
    return "155.0.8059.39-1"


@pytest.mark.parametrize("image", get_images())
def test_split_instructions_round_trips(image: str) -> None:
    dockerfile = (RUNBOT_DOCKERFILES_DIR / f"{image}.Dockerfile").read_text()
    assert "".join(split_instructions(dockerfile)) == dockerfile


def test_split_instructions_keeps_continued_instructions_together() -> None:
    dockerfile = "# comment\nFROM ubuntu:noble\n\nRUN a \\\n    # inner comment\n    && b\nUSER odoo\n"
    assert split_instructions(dockerfile) == [
        "# comment\n",
        "FROM ubuntu:noble\n",
        "\n",
        "RUN a \\\n    # inner comment\n    && b\n",
        "USER odoo\n",
    ]


@pytest.mark.parametrize("image", get_images())
def test_render_amd64_only_adds_to_runbot(image: str) -> None:
    dockerfile = render_dockerfile(image, "amd64")
    runbot = (RUNBOT_DOCKERFILES_DIR / f"{image}.Dockerfile").read_text()

    # Every Runbot line is kept as is, in the same order.
    remaining = iter(dockerfile.splitlines())
    assert all(any(line == rendered for rendered in remaining) for line in runbot.splitlines())
    assert "ARG USERNAME=odoo" in dockerfile
    assert dockerfile.index("RUN userdel --remove ubuntu") < dockerfile.index("groupadd")
    assert "debugpy" in dockerfile
    # Odoo's `--dev=reload` needs a file watcher.
    assert "watchdog" in dockerfile


@pytest.mark.parametrize("image", get_images())
def test_render_arm64_downloads_no_amd64_packages(image: str) -> None:
    dockerfile = render_dockerfile(image, "arm64", get_chrome_version=chrome_not_built_for_arm64)

    code = [line for line in dockerfile.splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in code if re.search(r"amd64|x86_64", line)]
    assert "wkhtmltox_0.12.6.1-2.jammy_arm64.deb" in dockerfile
    assert "google-chrome-stable_155.0.8059.39-1_arm64.deb" in dockerfile
    assert "paper-muncher_" not in dockerfile
    assert "RUN sed -i '/^libsass/d' /tmp/requirements.txt && python3 -m pip install" in dockerfile


def test_render_arm64_keeps_pinned_chrome_when_built_for_arm64() -> None:
    dockerfile = render_dockerfile("Docker19", "arm64", get_chrome_version=lambda pinned: pinned)
    assert "isn't built for arm64" not in dockerfile
    assert re.search(r"google-chrome-stable_[\d.]+-\d+_arm64\.deb", dockerfile)


def test_image_tags_cover_every_version() -> None:
    tags = [tag for image in get_images() for tag in get_image_tags(image)]
    assert sorted(tags) == sorted(f"dylankiss/odoo-dev:{version}" for version in get_versions())


def test_parse_image_layouts() -> None:
    # A single-architecture image of odoo-toolkit 3.x, without the label.
    old = '{"architecture": "amd64", "os": "linux", "config": {"Labels": {"maintainer": "x"}}}'
    assert parse_image_layouts(old) == {"amd64": None}
    # A multi-architecture image of this toolkit.
    new = (
        '{"linux/amd64": {"config": {"Labels": {"io.github.dylankiss.odoo-toolkit.layout": "2"}}},'
        ' "linux/arm64": {"config": {"Labels": {"io.github.dylankiss.odoo-toolkit.layout": "2"}}},'
        ' "unknown/unknown": {}}'
    )
    assert parse_image_layouts(new) == {"amd64": "2", "arm64": "2"}


def test_get_changed_images() -> None:
    previous = {name: version.image for name, version in get_versions().items()}

    # A changed Runbot Dockerfile only rebuilds its image.
    assert get_changed_images(["odoo_toolkit/docker/runbot/Docker19.Dockerfile", "README.md"], previous) == ["Docker19"]
    # Nothing the images are built from changed.
    assert get_changed_images(["README.md"], previous) == []
    # The development layer is in every image.
    assert get_changed_images(["odoo_toolkit/docker/dev.Dockerfile"], previous) == list(
        dict.fromkeys(previous.values())
    )
    # A new version needs the tag of its image.
    new_version = next(reversed(previous))
    del previous[new_version]
    assert get_changed_images([], previous) == [get_versions()[new_version].image]


@pytest.mark.parametrize("image", get_images())
def test_render_installs_extra_packages_before_the_debugger(image: str) -> None:
    dockerfile = render_dockerfile(image, "amd64")

    assert "EXTRA_PACKAGES" not in dockerfile
    apt = dockerfile.index("# Extra system packages")
    pip = dockerfile.index("# Extra Python packages")
    assert apt < pip < dockerfile.index("pip install --no-cache-dir debugpy")


def test_extra_packages_combine_common_and_image_sections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    packages = tmp_path / "packages.toml"
    packages.write_text('[common]\napt = ["a"]\npip = ["x"]\n[Docker17]\napt = ["a", "b"]\n')
    monkeypatch.setattr(images, "PACKAGES_FILE", packages)

    assert get_extra_packages("Docker17") == (["a", "b"], ["x"])
    assert get_extra_packages("Docker19") == (["a"], ["x"])
    packages.write_text("")
    assert render_packages_layer("Docker19") == ""


def test_changing_the_packages_rebuilds_every_image() -> None:
    previous = {name: version.image for name, version in get_versions().items()}
    assert get_changed_images(["odoo_toolkit/docker/packages.toml"], previous) == get_images()
