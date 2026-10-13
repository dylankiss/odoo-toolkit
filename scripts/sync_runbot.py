"""Synchronize the Odoo version registry and Dockerfiles with runbot.

Fetches every supported Odoo version from runbot's versions page, stores the Dockerfile runbot uses for it verbatim in
`odoo_toolkit/docker/runbot/` and regenerates `odoo_toolkit/versions.json`.

Usage: `uv run scripts/sync_runbot.py`
"""

import html
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import requests

from odoo_toolkit.common import HTTP_HEADERS
from odoo_toolkit.versions import VERSIONS_FILE, version_sort_key

RUNBOT_URL = "https://runbot.odoo.com"
VERSIONS_URL = f"{RUNBOT_URL}/runbot/versions"
DOCKERFILE_URL = f"{RUNBOT_URL}/runbot/dockerfile"
DOCKERFILES_DIR = Path(__file__).parent.parent / "odoo_toolkit" / "docker" / "runbot"
# Older versions are no longer supported.
MIN_VERSION = (18.0, 0.0)
TIMEOUT = 30

ROW_PATTERN = re.compile(r"<tr(?:\s[^>]*)?>(.*?)</tr>", re.DOTALL)
CELL_PATTERN = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
HEADER_PATTERN = re.compile(r"<th[^>]*>(.*?)</th>", re.DOTALL)
DOCKERFILE_LINK_PATTERN = re.compile(r'href="/runbot/dockerfile\?id=(\d+)"[^>]*>\s*([^<]+?)\s*<')
FROM_PATTERN = re.compile(r"^FROM\s+ubuntu:(\S+)", re.MULTILINE)
VERSION_NAME_PATTERN = re.compile(r"master|saas-\d+\.\d+|\d+\.\d+")


@dataclass(frozen=True)
class RunbotVersion:
    """An Odoo version as listed on runbot's versions page."""

    name: str
    dockerfile_id: int
    image: str
    python: str


def parse_versions_page(page: str) -> list[RunbotVersion]:
    """Parse the Odoo versions and their Dockerfiles from runbot's versions page.

    :param page: The HTML of the versions page.
    :return: The listed versions.
    """
    headers = [_text(header).lower() for header in HEADER_PATTERN.findall(page)]
    # Find the Python column by its header, in case the columns change. The version is always the first one.
    python_column = next((i for i, header in enumerate(headers) if "python" in header), None)
    if python_column is None:
        msg = "Runbot's versions page has no Python column anymore. Has the page layout changed?"
        raise ValueError(msg)
    versions: list[RunbotVersion] = []
    for row in ROW_PATTERN.findall(page):
        link = DOCKERFILE_LINK_PATTERN.search(row)
        if not link:
            continue
        cells = [_text(cell) for cell in CELL_PATTERN.findall(row)]
        python = re.match(r"\d+\.\d+", cells[python_column]) if len(cells) > python_column else None
        versions.append(
            RunbotVersion(
                name=cells[0],
                dockerfile_id=int(link.group(1)),
                image=link.group(2),
                python=python.group(0) if python else "",
            ),
        )
    return versions


def _text(cell: str) -> str:
    """Get the text of an HTML table cell."""
    return html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()


def parse_ubuntu_release(dockerfile: str) -> str:
    """Get the Ubuntu release a Dockerfile is based on, like `noble`.

    :raises ValueError: If the Dockerfile isn't based on Ubuntu.
    """
    match = FROM_PATTERN.search(dockerfile)
    if not match:
        msg = "The Dockerfile is not based on an Ubuntu image."
        raise ValueError(msg)
    return match.group(1)


def fetch(url: str, params: dict[str, str | int] | None = None) -> str:
    """Fetch a page from runbot as text."""
    response = requests.get(url, params=params, headers=HTTP_HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.text


def build_registry(versions: list[RunbotVersion], dockerfiles: dict[str, str]) -> dict[str, dict[str, str]]:
    """Build the version registry, sorted from oldest to newest version.

    :param versions: The versions listed on runbot.
    :param dockerfiles: The Dockerfiles per runbot image.
    :return: The registry, mapping each version to its image, Ubuntu release and Python version.
    """
    registry: dict[str, dict[str, str]] = {}
    for version in sorted(versions, key=lambda v: version_sort_key(v.name)):
        if not version.python:
            print(f"Skipping {version.name}: runbot lists no Python version.", file=sys.stderr)
            continue
        registry[version.name] = {
            "image": version.image,
            "ubuntu": parse_ubuntu_release(dockerfiles[version.image]),
            "python": version.python,
        }
    return registry


def write_dockerfiles(dockerfiles: dict[str, str], used_images: set[str]) -> None:
    """Write the Dockerfiles of the used images verbatim, and remove the ones no version uses anymore."""
    DOCKERFILES_DIR.mkdir(parents=True, exist_ok=True)
    for image in used_images:
        (DOCKERFILES_DIR / f"{image}.Dockerfile").write_text(dockerfiles[image], encoding="utf-8")
    for stale in DOCKERFILES_DIR.glob("*.Dockerfile"):
        if stale.stem not in used_images:
            stale.unlink()


def main() -> int:
    """Synchronize the registry and Dockerfiles with runbot."""
    versions = [
        version
        for version in parse_versions_page(fetch(VERSIONS_URL))
        # Names that aren't versions sort last, like `master`, so only keep the ones that look like versions.
        if version_sort_key(version.name) >= MIN_VERSION and VERSION_NAME_PATTERN.fullmatch(version.name)
    ]
    if not versions:
        print("No supported versions found on runbot. Has the page layout changed?", file=sys.stderr)
        return 1

    # Fetch everything before writing anything, so a network error can't leave a half-updated registry.
    dockerfiles: dict[str, str] = {}
    for version in versions:
        if version.image not in dockerfiles:
            dockerfiles[version.image] = fetch(DOCKERFILE_URL, params={"id": version.dockerfile_id})

    registry = build_registry(versions, dockerfiles)
    if removed := sorted(set(json.loads(VERSIONS_FILE.read_text(encoding="utf-8"))) - set(registry)):
        print(f"No longer on runbot: {', '.join(removed)}. Check that this is expected.", file=sys.stderr)
    write_dockerfiles(dockerfiles, {info["image"] for info in registry.values()})
    VERSIONS_FILE.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")

    for name, info in registry.items():
        print(f"{name:<12} {info['image']:<14} ubuntu:{info['ubuntu']:<10} python {info['python']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
