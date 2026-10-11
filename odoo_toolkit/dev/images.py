"""Render the Dockerfiles of the development images from Runbot's Dockerfiles.

Runbot's Dockerfiles are stored verbatim in `docker/runbot/` and only build for amd64. This module adapts them to the
target architecture, declares the build arguments Runbot passes implicitly, and appends the development layer.
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import requests

from odoo_toolkit.versions import get_versions

Arch = Literal["amd64", "arm64"]

DOCKER_REPOSITORY = "dylankiss/odoo-dev"
DOCKER_DIR = Path(__file__).parent.parent / "docker"
RUNBOT_DOCKERFILES_DIR = DOCKER_DIR / "runbot"
DEV_LAYER_FILE = DOCKER_DIR / "dev.Dockerfile"

CHROME_POOL_URL = "https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable"
CHROME_PACKAGES_URL = "https://dl.google.com/linux/chrome/deb/dists/stable/main/binary-{arch}/Packages"
CHROME_DEB_PATTERN = re.compile(r"google-chrome-stable_(?P<version>[^_/\s]+)_amd64\.deb")
TIMEOUT = 30

# The user the images run as. UID 1000 matches the first user on most Linux machines, which avoids permission issues
# on mounted source code.
BUILD_ARGS = "ARG USERNAME=odoo\nARG USERUID=1000\nARG USERGID=1000\n"
# Where Runbot's Dockerfiles download the Paper Muncher package to, which only exists for amd64.
PAPER_MUNCHER_DEB = "/tmp/paper.deb"  # noqa: S108 (a path inside the image, not on this machine)


def split_instructions(dockerfile: str) -> list[str]:
    """Split a Dockerfile into blocks, each being one instruction (with its continuation lines), comment or blank line.

    :param dockerfile: The Dockerfile contents.
    :return: The blocks, which joined together give back the original Dockerfile.
    """
    blocks: list[str] = []
    current: list[str] = []
    for line in dockerfile.splitlines(keepends=True):
        is_comment_or_blank = not line.strip() or line.lstrip().startswith("#")
        if not current and is_comment_or_blank:
            blocks.append(line)
            continue
        current.append(line)
        # Comments and blank lines inside a continued instruction don't end it.
        if not is_comment_or_blank and not line.rstrip("\n").endswith("\\"):
            blocks.append("".join(current))
            current = []
    if current:
        blocks.append("".join(current))
    return blocks


def get_arm64_chrome_version(pinned_version: str) -> str:
    """Get the Chrome version to install on arm64: the pinned version if Google builds it for arm64, else the latest.

    Google only started building Chrome for arm64 recently, so older pinned versions don't exist for it.

    :param pinned_version: The version Runbot pins, like `150.0.7871.114-1`.
    :return: The version to install.
    """
    pinned = requests.head(f"{CHROME_POOL_URL}/google-chrome-stable_{pinned_version}_arm64.deb", timeout=TIMEOUT)
    if pinned.ok:
        return pinned_version
    packages = requests.get(CHROME_PACKAGES_URL.format(arch="arm64"), timeout=TIMEOUT)
    packages.raise_for_status()
    match = re.search(r"^Package: google-chrome-stable\n(?:.+\n)*?Version: (\S+)$", packages.text, re.MULTILINE)
    if not match:
        msg = "Could not find the latest arm64 version of Chrome."
        raise ValueError(msg)
    return match.group(1)


def render_dockerfile(
    image: str,
    arch: Arch,
    get_chrome_version: Callable[[str], str] = get_arm64_chrome_version,
) -> str:
    """Render the Dockerfile of a development image for an architecture.

    :param image: The Runbot image name, like `Docker19`.
    :param arch: The architecture to build for.
    :param get_chrome_version: Returns the Chrome version to install on arm64, given the version Runbot pins.
    :raises ValueError: If the rendered arm64 Dockerfile would still download amd64 packages.
    :return: The Dockerfile contents.
    """
    runbot_dockerfile = (RUNBOT_DOCKERFILES_DIR / f"{image}.Dockerfile").read_text(encoding="utf-8")
    blocks: list[str] = []
    seen_from = False
    for block in split_instructions(runbot_dockerfile):
        instruction = block.lstrip().split(maxsplit=1)[0].upper() if block.strip() else ""
        if instruction == "RUN" and "groupadd" in block and "${USERNAME}" in block:
            # Ubuntu 24.04 and later come with an `ubuntu` user taking UID 1000.
            blocks.append("RUN userdel --remove ubuntu 2>/dev/null || true\n")
        if arch == "arm64":
            arm64_block = _adapt_block_to_arm64(block, get_chrome_version)
            if arm64_block is None:
                continue
            block = arm64_block  # noqa: PLW2901
        blocks.append(block)
        if instruction == "FROM" and not seen_from:
            seen_from = True
            blocks.append(f"\n# Declare the build arguments Runbot passes implicitly.\n{BUILD_ARGS}")

    dockerfile = "".join(blocks)
    if arch == "arm64":
        leftovers = [
            line for line in dockerfile.splitlines() if not line.lstrip().startswith("#") and re.search(r"amd64|x86_64", line)
        ]
        if leftovers:
            msg = "The arm64 Dockerfile still downloads amd64 packages:\n" + "\n".join(leftovers)
            raise ValueError(msg)

    dev_layer = DEV_LAYER_FILE.read_text(encoding="utf-8")
    return f"# Generated by odoo-toolkit from Runbot's {image} Dockerfile for {arch}.\n{dockerfile.rstrip()}\n\n{dev_layer}"


def _adapt_block_to_arm64(block: str, get_chrome_version: Callable[[str], str]) -> str | None:
    """Adapt one Dockerfile block to arm64.

    :return: The adapted block, or None if it must be left out.
    """
    if PAPER_MUNCHER_DEB in block:
        # Paper Muncher is only built for amd64, so leave out downloading and installing it. Odoo falls back to
        # wkhtmltopdf without it.
        return None
    if block.startswith("RUN") and "pip install" in block and "-r /tmp/requirements.txt" in block:
        # Odoo pins libsass only to get a wheel, which doesn't exist for arm64 Linux, and its source build is broken
        # since Python 3.14. The `python3-libsass` package from `debian/control` provides it instead.
        return (
            "# libsass has no arm64 wheel, so use the python3-libsass package installed from debian/control.\n"
            + block.replace("RUN ", "RUN sed -i '/^libsass/d' /tmp/requirements.txt && ", 1)
        )
    if chrome := CHROME_DEB_PATTERN.search(block):
        pinned_version = chrome.group("version")
        version = get_chrome_version(pinned_version)
        block = block.replace(chrome.group(0), f"google-chrome-stable_{version}_arm64.deb")
        if version != pinned_version:
            block = f"# Runbot pins Chrome {pinned_version}, which isn't built for arm64.\n{block}"
        return block
    return block.replace("_amd64.deb", "_arm64.deb").replace("-linux-x86_64-", "-linux-aarch64-")


def get_image_tags(image: str) -> list[str]:
    """Get the Docker tags of an image: one per Odoo version that uses it.

    :param image: The Runbot image name, like `Docker191`.
    :return: The tags, like `dylankiss/odoo-dev:saas-19.1`.
    """
    return [f"{DOCKER_REPOSITORY}:{version.name}" for version in get_versions().values() if version.image == image]


def get_images() -> list[str]:
    """Get the Runbot image names used by the supported versions, from oldest to newest."""
    return list(dict.fromkeys(version.image for version in get_versions().values()))
