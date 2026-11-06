"""The domains the dev containers can reach, in your odoo-toolkit configuration file.

The dev containers have no route out except through a proxy that only lets these domains through (see
`odoo_toolkit.dev.proxy`).
"""

import re
import tomllib
from typing import Any, cast

from odoo_toolkit.common import get_config_file, write_file_atomically

# The domains a dev container can reach, until you list your own: what Claude Code needs, what Visual Studio Code needs
# to install its server and the extensions in the container, and Odoo's own hosts for its documentation and services.
# Nothing else, since anything a container can reach is a way to send data out. `*.odoo.com` is left out on purpose,
# because it includes every Odoo Online database, which anyone can create.
DEFAULT_ALLOWED_DOMAINS = (
    "api.anthropic.com",
    "claude.ai",
    "claude.com",
    "platform.claude.com",
    "marketplace.visualstudio.com",
    "*.gallerycdn.vsassets.io",
    "*.gallery.vsassets.io",
    "update.code.visualstudio.com",
    "vscode.download.prss.microsoft.com",
    "www.odoo.com",
    "accounts.odoo.com",
    "apps.odoo.com",
    "download.odoo.com",
    "iap.odoo.com",
    "iot-proxy.odoo.com",
    "iap-scraper.odoo.com",
    "iap-services.odoo.com",
    "iap-services-test.odoo.com",
    "iap-snailmail.odoo.com",
    "mail.odoo.com",
    "mergebot.odoo.com",
    "nightly.odoo.com",
    "ocn.odoo.com",
    "partner-autocomplete.odoo.com",
    "pdp.odoo.com",
    "runbot.odoo.com",
    "services.odoo.com",
    "sip.odoo.com",
    "test.odoo.com",
    "translate.odoo.com",
    "upgrade.odoo.com",
    "*.api.odoo.com",
    "*.test.odoo.com",
)
DOMAIN_PATTERN = re.compile(r"^(\*\.)?([a-z0-9-]+\.)+[a-z0-9-]+$")


def get_allowed_domains() -> list[str]:
    """Get the domains the dev containers can reach, through the proxy of `otk dev db`.

    They are `allowed_domains` in the `[devcontainer]` table of your odoo-toolkit configuration file, not of
    `multiverse.toml`, since every dev container of yours shares the proxy:

    ```toml
    [devcontainer]
    allowed_domains = ["api.anthropic.com", "claude.ai", "pypi.org", "*.github.com"]
    ```

    The list replaces the default one. A domain is a name like `github.com`, or `*.github.com` for it and its
    subdomains. Invalid domains are left out, and a configuration file that can't be read allows no domain at all, so
    a mistake never lets a container reach more than you meant to.

    :return: The domains.
    """
    try:
        with get_config_file().open("rb") as config_file:
            table = tomllib.load(config_file).get("devcontainer")
    except FileNotFoundError:
        table = None
    except (OSError, tomllib.TOMLDecodeError):
        return []
    values = cast("dict[str, Any]", table) if isinstance(table, dict) else {}
    domains = values.get("allowed_domains")
    if domains is None:
        return list(DEFAULT_ALLOWED_DOMAINS)
    if not isinstance(domains, list):
        return []
    cleaned = (domain.strip().lower() for domain in cast("list[Any]", domains) if isinstance(domain, str))
    return list(dict.fromkeys(domain for domain in cleaned if DOMAIN_PATTERN.match(domain)))


def ensure_allowed_domains_configured() -> bool:
    """Write the default allowed domains in your odoo-toolkit configuration file, if it has no list of them yet.

    That makes the list easy to customize, since it's right there to edit. It is the default list as it is now: the
    toolkit doesn't change it afterwards, so hosts it starts to need later are yours to add. The file is left alone if
    it can't be read or isn't valid TOML, or when `devcontainer` isn't a plain `[devcontainer]` table that a key can be
    added to.

    :return: Whether the list was written.
    """
    config_file = get_config_file()
    try:
        text = config_file.read_text(encoding="utf-8") if config_file.exists() else ""
        data = tomllib.loads(text)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    table = data.get("devcontainer")
    if table is not None and (not isinstance(table, dict) or "allowed_domains" in table):
        return False
    domains = "".join(f'    "{domain}",\n' for domain in DEFAULT_ALLOWED_DOMAINS)
    key = (
        "# The domains the dev containers can reach, through the proxy of `otk dev db`. Edit the list, then run\n"
        "# `otk dev egress`. `*.example.com` includes `example.com`. This is the default list as it was when it was\n"
        "# added: odoo-toolkit doesn't change it, so add any host it starts to need yourself.\n"
        f"allowed_domains = [\n{domains}]\n"
    )
    if table is None:
        # The new table comes after a blank line, if there is anything before it.
        before = text if not text or text.endswith("\n") else f"{text}\n"
        before += "\n" if before and not before.endswith("\n\n") else ""
        updated = f"{before}[devcontainer]\n{key}"
    else:
        header = re.search(r"(?m)^\[devcontainer\][ \t]*(?:#.*)?$", text)
        if not header:
            return False
        updated = f"{text[: header.end()]}\n{key}{text[header.end() :]}"
    # Only write what reads back as the default list, and keeps the rest of the file as it was.
    try:
        written = tomllib.loads(updated)
    except tomllib.TOMLDecodeError:
        return False
    if written.get("devcontainer", {}).get("allowed_domains") != list(DEFAULT_ALLOWED_DOMAINS):
        return False
    write_file_atomically(config_file, updated)
    return True
