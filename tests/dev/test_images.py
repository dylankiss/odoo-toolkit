import re

import pytest

from odoo_toolkit.dev.images import (
    RUNBOT_DOCKERFILES_DIR,
    get_image_tags,
    get_images,
    render_dockerfile,
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
