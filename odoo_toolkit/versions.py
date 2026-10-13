import json
import math
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

# The registry of supported Odoo versions, generated from runbot by `scripts/sync_runbot.py`.
VERSIONS_FILE = Path(__file__).parent / "versions.json"


@dataclass(frozen=True)
class OdooVersion:
    """A supported Odoo version and the Runbot environment it runs in."""

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
