"""Check whether the Docker images the dev tools use have a newer build on Docker Hub.

The images are pulled once and then kept, so a rebuild of an image, like the Odoo ones when Runbot changes, would go
unnoticed. The registry is asked once a day per image, and the answer is compared with what is pulled.
"""

import json
import os
import re
import subprocess
import time
from contextlib import suppress
from pathlib import Path
from threading import Lock, Thread
from typing import cast

import requests

from odoo_toolkit.common import HTTP_HEADERS, console, print
from odoo_toolkit.update_check import CHECK_INTERVAL, DISABLE_VARIABLE, get_cache_file
from odoo_toolkit.versions import OdooVersion

from .runtime import COMPOSE_FILE, get_image_tag

TOKEN_URL = "https://auth.docker.io/token"  # noqa: S105 (where to ask for a token, not a token)
REGISTRY_URL = "https://registry-1.docker.io/v2"
MANIFEST_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)
# How long a command waits for the registry before it goes on without a notice, to not slow it down.
WAIT_FOR_CHECK = 2.0
REQUEST_TIMEOUT = 3
# How long a failed check is remembered, so being offline doesn't slow down every command.
RETRY_AFTER = 60 * 60
_cache_lock = Lock()


def get_cache_path() -> Path:
    """Get the file the digests of the registry are remembered in."""
    return get_cache_file().parent / "image-digests.json"


def parse_image(image: str) -> tuple[str, str] | None:
    """Split an image on Docker Hub into its repository and tag, or return None for any other image.

    Images from other registries, from your own machine and ones pinned to a digest can't be compared this way.
    """
    if "@" in image:
        return None
    name, tag = image, ""
    # A colon in the last part is the tag, and one before it is the port of a registry.
    if ":" in image.rpartition("/")[2]:
        name, _, tag = image.rpartition(":")
    first = name.split("/")[0]
    if "/" in name and ("." in first or ":" in first or first == "localhost"):
        return None
    return (name if "/" in name else f"library/{name}"), tag or "latest"


def get_local_digests(image: str) -> set[str]:
    """Get the digests an image on this machine was pulled with, which is empty if it was built here or isn't there."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        # Docker isn't installed or can't be run.
        return set()
    if result.returncode:
        return set()
    try:
        digests = cast("list[str] | None", json.loads(result.stdout))
    except ValueError:
        return set()
    return {digest.rpartition("@")[2] for digest in digests or []}


def get_registry_digest(image: str, *, now: float | None = None) -> str | None:
    """Get the digest of the newest build of an image on Docker Hub, remembered for a day.

    :param image: The image, like `dylankiss/odoo-dev:19.0`.
    :param now: The current time in seconds, or None for the actual time.
    :return: The digest, or None if the registry can't be reached or the image isn't on Docker Hub.
    """
    if not (parsed := parse_image(image)):
        return None
    now = time.time() if now is None else now
    cache_path = get_cache_path()
    with _cache_lock:
        cache: dict[str, dict[str, object]] = {}
        with suppress(OSError, ValueError):
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        with suppress(KeyError, TypeError, ValueError):
            if now - float(str(cache[image]["checked"])) < CHECK_INTERVAL:
                digest = cache[image]["digest"]
                return None if digest is None else str(digest)

    name, tag = parsed
    try:
        token = requests.get(
            TOKEN_URL,
            params={"service": "registry.docker.io", "scope": f"repository:{name}:pull"},
            headers=HTTP_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        token.raise_for_status()
        response = requests.head(
            f"{REGISTRY_URL}/{name}/manifests/{tag}",
            headers={
                **HTTP_HEADERS,
                "Authorization": f"Bearer {token.json()['token']}",
                "Accept": ", ".join(MANIFEST_TYPES),
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        digest = response.headers["Docker-Content-Digest"]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        # Offline or the registry is down: don't ask again on every command, but soon.
        _remember(cache_path, image, None, now - CHECK_INTERVAL + RETRY_AFTER)
        return None

    _remember(cache_path, image, digest, now)
    return digest


def _remember(cache_path: Path, image: str, digest: str | None, checked: float) -> None:
    """Remember the digest of an image, or that it couldn't be found, without losing the other images."""
    with _cache_lock:
        cache: dict[str, dict[str, object]] = {}
        # Other images may have been remembered while this one was asked for.
        with suppress(OSError, ValueError):
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        cache[image] = {"checked": checked, "digest": digest}
        with suppress(OSError):
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache), encoding="utf-8")


def is_outdated(image: str, *, now: float | None = None) -> bool:
    """Check whether the registry has a newer build of an image than the one pulled on this machine."""
    if not (local := get_local_digests(image)):
        # Built on this machine, or not pulled: there is nothing to compare.
        return False
    registry = get_registry_digest(image, now=now)
    return bool(registry) and registry not in local


def get_service_images() -> list[str]:
    """Get the images of the shared services, like PostgreSQL and Mailpit, from their Compose file."""
    try:
        return re.findall(r"^\s+image:\s*(\S+)\s*$", COMPOSE_FILE.read_text(encoding="utf-8"), re.MULTILINE)
    except OSError:
        return []


def find_outdated_images(images: list[str], *, now: float | None = None) -> list[str]:
    """Find the images that have a newer build, checking them at the same time and giving up after a short while."""
    outdated: dict[str, bool] = {}

    def check(image: str) -> None:
        # A check is a courtesy, so whatever goes wrong in it must never show up as an error.
        with suppress(Exception):
            outdated[image] = is_outdated(image, now=now)

    threads = [Thread(target=check, args=(image,), daemon=True) for image in images]
    deadline = time.monotonic() + WAIT_FOR_CHECK
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return [image for image in images if outdated.get(image)]


def is_check_disabled() -> bool:
    """Check whether nothing should be checked: in scripts, in CI, for shell completion, or if you turned it off."""
    return bool(
        os.environ.get(DISABLE_VARIABLE)
        or os.environ.get("CI")
        or "_OTK_COMPLETE" in os.environ
        or not console.is_terminal,
    )


def notify_outdated_images(version: OdooVersion | None = None) -> None:
    """Tell about newer builds of the Odoo image of a version, if one is given, and of the shared services.

    :param version: The Odoo version, or None to only check the services.
    """
    if is_check_disabled():
        return
    odoo_image = get_image_tag(version) if version else None
    for image in find_outdated_images([*([odoo_image] if odoo_image else []), *get_service_images()]):
        if image == odoo_image:
            print(
                f"[dim]A newer build of the [b]{version.name if version else ''}[/b] image is available. Get it with "
                "[b]otk dev run --pull[/b] or [b]otk dev shell --pull[/b], and rebuild your dev container.[/dim]",
            )
        else:
            print(f"[dim]A newer build of [b]{image}[/b] is available. Get it with [b]otk dev db --pull[/b].[/dim]")
