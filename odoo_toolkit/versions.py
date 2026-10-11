import json
import math
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

# The registry of supported Odoo versions, generated from runbot by `scripts/sync_runbot.py`.
VERSIONS_FILE = Path(__file__).parent / "versions.json"

# Matches the Odoo version a branch is based on, like `19.0`, `saas-19.2` or `master`, optionally followed by a dash
# and a feature name, like `master-fix-abc`.
BRANCH_VERSION_PATTERN = re.compile(r"^(master|saas-\d+\.\d+|\d+\.\d+)(?:-|$)")


@dataclass(frozen=True)
class OdooVersion:
    """A supported Odoo version and the runbot environment it runs in."""

    name: str
    image: str
    ubuntu: str
    python: str


@cache
def get_versions() -> dict[str, OdooVersion]:
    """Get every supported Odoo version, from oldest to newest."""
    with VERSIONS_FILE.open(encoding="utf-8") as versions_file:
        data: dict[str, dict[str, str]] = json.load(versions_file)
    return {
        name: OdooVersion(name=name, image=info["image"], ubuntu=info["ubuntu"], python=info["python"])
        for name, info in data.items()
    }


def version_sort_key(version: str) -> tuple[float, float]:
    """Get a key to sort Odoo versions by, with `master` sorting last.

    :param version: The Odoo version, like `19.0`, `saas-19.2` or `master`.
    :return: The sort key.
    """
    match = re.fullmatch(r"(?:saas-)?(\d+)\.(\d+)", version)
    if not match:
        return (math.inf, math.inf)
    return (float(match.group(1)), float(match.group(2)))


def get_branch_version_name(branch: str) -> str | None:
    """Get the Odoo version a branch is based on, like `19.0` for `19.0-fix-abc`.

    :param branch: The branch name.
    :return: The Odoo version, or None if the branch name doesn't start with one.
    """
    match = BRANCH_VERSION_PATTERN.match(branch)
    return match.group(1) if match else None


def resolve_version(branch: str) -> OdooVersion | None:
    """Get the supported Odoo version a branch is based on.

    :param branch: The branch name, like `saas-19.2` or `master-fix-abc`.
    :return: The Odoo version, or None if the branch isn't based on a supported version.
    """
    name = get_branch_version_name(branch)
    return get_versions().get(name) if name else None
