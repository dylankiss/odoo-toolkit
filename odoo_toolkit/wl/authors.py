"""The translators you upload translations for, configured in your odoo-toolkit configuration file.

```toml
# ~/.config/odoo-toolkit/config.toml
[weblate.authors]
alice = { name = "Alice Martin", email = "alice.martin@example.com" }
```
"""

import tomllib
from typing import Annotated, Any, cast

from rich.table import Table
from typer import Exit, Option, Typer

from odoo_toolkit.common import get_config_file, print, print_command_title, print_error, print_warning

app = Typer()


class ConfigError(Exception):
    """The configuration file is invalid."""


def get_weblate_settings(table: str) -> dict[str, Any]:
    """Get a table of the `[weblate]` settings in your configuration file, like `authors`.

    :param table: The name of the table, inside `[weblate]`.
    :raises ConfigError: If the configuration file isn't valid TOML, or the table isn't a table.
    :return: The table, which is empty if it isn't configured.
    """
    config_file = get_config_file()
    if not config_file.is_file():
        return {}
    try:
        config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        msg = f"{config_file} is not valid TOML: {e}"
        raise ConfigError(msg) from e
    weblate = config.get("weblate", {})
    values: object = cast("dict[str, Any]", weblate).get(table, {}) if isinstance(weblate, dict) else {}
    if not isinstance(values, dict):
        msg = f"[weblate.{table}] in {config_file} must be a table."
        raise ConfigError(msg)
    return cast("dict[str, Any]", values)


def get_authors() -> dict[str, tuple[str, str]]:
    """Get the configured authors, by their short name.

    :raises ConfigError: If the configuration file is invalid.
    :return: The name and email of every author.
    """
    config_file = get_config_file()
    authors: dict[str, tuple[str, str]] = {}
    for key, value in get_weblate_settings("authors").items():
        settings = cast("dict[str, Any]", value) if isinstance(value, dict) else {}
        name, email = settings.get("name"), settings.get("email")
        if not isinstance(name, str) or not isinstance(email, str) or "@" not in email:
            msg = f'Invalid author `{key}` in {config_file}: use `{key} = {{ name = "Full Name", email = "..." }}`.'
            raise ConfigError(msg)
        authors[key] = (name, email)
    return authors


def complete_author(incomplete: str) -> list[tuple[str, str]]:
    """Complete the short names of the configured authors, showing their full name."""
    try:
        authors = get_authors()
    except ConfigError:
        return []
    return [(key, name) for key, (name, _) in sorted(authors.items()) if key.startswith(incomplete)]


AuthorOption = Annotated[
    str | None,
    Option(
        "--author",
        "-a",
        help="The author of the uploaded translations: the short name of a translator in your configuration file "
        "(see `otk wl authors`), or a full name. If not set, the user of the API token is the author.",
        autocompletion=complete_author,
    ),
]
EmailOption = Annotated[
    str | None,
    Option(
        "--email",
        "-e",
        help="The author's email, for an author that isn't in your configuration file. Weblate finds the author's "
        "account by it.",
    ),
]


def resolve_author(author: str | None, email: str | None) -> dict[str, str]:
    """Get the author fields of a translation upload.

    :param author: The short name of a configured author, or a full name.
    :param email: The author's email, which overrides the configured one.
    :raises Exit: If the configuration file is invalid.
    :return: The `author_name` and `author_email` to upload with, if any.
    """
    try:
        authors = get_authors()
    except ConfigError as e:
        print_error(str(e))
        raise Exit(1) from e
    name, configured_email = authors.get(author, (author, None)) if author else (None, None)
    email = email or configured_email
    if author and author not in authors and not email:
        print_warning(
            f"[b]{author}[/b] isn't in your configuration file, and has no [b]--email[/b], so Weblate can't find the "
            "account to credit.\n",
        )
    return {key: value for key, value in (("author_name", name), ("author_email", email)) if value}


@app.command()
def authors() -> None:
    """List the translators you can upload translations for, with `--author`.

    They are configured in your odoo-toolkit configuration file (`~/.config/odoo-toolkit/config.toml`), by a short
    name, like:\n
    \n
    `[weblate.authors]`\n
    `alice = { name = "Alice Martin", email = "alice.martin@example.com" }`
    """
    print_command_title(":busts_in_silhouette: Odoo Weblate Authors")
    config_file = get_config_file()
    try:
        configured = get_authors()
    except ConfigError as e:
        print_error(str(e))
        raise Exit(1) from e
    if not configured:
        print_warning(f"There are no authors in [u]{config_file}[/u] yet. See [b]otk wl authors --help[/b].\n")
        return
    table = Table(box=None, pad_edge=False)
    table.add_column("Author", style="bold")
    table.add_column("Name")
    table.add_column("Email")
    for key, (name, email) in sorted(configured.items()):
        table.add_row(key, name, email)
    print(table, "")
    print(f"[d]From [u]{config_file}[/u][/d]\n")
