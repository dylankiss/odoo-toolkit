import json
import os
import time
from contextlib import suppress
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from threading import Thread

import requests
from typer import Context

from .common import (
    HTTP_HEADERS,
    VERSION,
    console,
)

LATEST_VERSION_URL = "https://pypi.org/pypi/odoo-toolkit/json"
# How long a check stays valid, so PyPI is asked once a day at most.
CHECK_INTERVAL = 24 * 60 * 60
# How long a command waits for a check that's still running when it ends, to not slow it down.
WAIT_FOR_CHECK = 1.0
# Set it to anything to never check for a new version of the toolkit, or of the Docker images of `otk dev`.
DISABLE_VARIABLE = "OTK_NO_UPDATE_CHECK"
# Seconds to wait for an answer: a check is only a courtesy.
REQUEST_TIMEOUT = 3
# How long a failed check is remembered, so being offline doesn't slow down every command.
RETRY_AFTER = 60 * 60


def is_installed_from_index() -> bool:
    """Check whether the toolkit was installed from PyPI, and not from a folder or Git repository, like for development.

    Those installs are recorded in `direct_url.json`, and their version is only the one from when they were installed.
    """
    try:
        return distribution("odoo-toolkit").read_text("direct_url.json") is None
    except PackageNotFoundError:
        return False


def get_cache_dir() -> Path:
    """Get the folder the toolkit remembers what it checked in, in `$XDG_CACHE_HOME` or `~/.cache`."""
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "odoo-toolkit"


def get_cache_file() -> Path:
    """Get the file the latest known version is remembered in."""
    return get_cache_dir() / "latest-version.json"


def is_check_disabled() -> bool:
    """Check whether nothing should be checked: in scripts, in CI, for shell completion, or if you turned it off."""
    return bool(
        os.environ.get(DISABLE_VARIABLE)
        or os.environ.get("CI")
        or "_OTK_COMPLETE" in os.environ
        or not console.is_terminal,
    )


def parse_version(version: str) -> tuple[int, ...] | None:
    """Parse a plain release version like `4.1.2`, or return None for anything else, like a development version."""
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def is_newer(latest: str, current: str) -> bool:
    """Check whether a release version is newer than the current one, which can be a development version."""
    latest_parts, current_parts = parse_version(latest), parse_version(current)
    return bool(latest_parts and current_parts and latest_parts > current_parts)


def get_latest_version(cache_file: Path, *, now: float | None = None) -> str | None:
    """Get the latest release on PyPI, from the file if it was checked in the last day, or else from PyPI.

    :param cache_file: The file the latest version is remembered in.
    :param now: The current time in seconds, or None for the actual time.
    :return: The latest version, or None if it can't be found.
    """
    now = time.time() if now is None else now
    with suppress(OSError, ValueError, KeyError, TypeError):
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        if now - float(cached["checked"]) < CHECK_INTERVAL:
            return None if cached["version"] is None else str(cached["version"])
    try:
        response = requests.get(LATEST_VERSION_URL, headers=HTTP_HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        latest: str | None = str(response.json()["info"]["version"])
        checked = now
    except (requests.RequestException, ValueError, KeyError, TypeError):
        # Remembered for a while too, so being offline doesn't slow down every command.
        latest, checked = None, now - CHECK_INTERVAL + RETRY_AFTER
    with suppress(OSError):
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"checked": checked, "version": latest}), encoding="utf-8")
    return latest


def check_for_update(ctx: Context) -> None:
    """Check for a new version in the background, and tell about it when the command is done.

    Nothing is checked in scripts, in CI, for shell completion, or for a toolkit that isn't installed from PyPI, like a
    development version.
    """
    if is_check_disabled() or parse_version(VERSION) is None or not is_installed_from_index():
        return

    latest: list[str] = []

    def check() -> None:
        if version := get_latest_version(get_cache_file()):
            latest.append(version)

    thread = Thread(target=check, daemon=True)
    thread.start()

    def notify() -> None:
        thread.join(WAIT_FOR_CHECK)
        if latest and is_newer(latest[0], VERSION):
            console.print(
                f"\n[dim]A new version of odoo-toolkit is available: {VERSION} → [b]{latest[0]}[/b]. "
                "Upgrade with [b]uv tool upgrade odoo-toolkit[/b] or [b]pipx upgrade odoo-toolkit[/b].[/dim]",
            )

    ctx.call_on_close(notify)
