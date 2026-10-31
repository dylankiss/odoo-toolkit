"""Synchronize the usage, arguments and options of every command in the README with the command itself.

Only the `### Usage` line and the `### Arguments` and `### Options` lists of each `## otk …` section are written, so the
rest of the README, like the explanations and the examples, stays as it is.

Usage:
    uv run scripts/sync_readme.py           # Update the README
    uv run scripts/sync_readme.py --check   # Fail if the README isn't up to date, like in CI
"""

import argparse
import difflib
import re
import sys
from collections.abc import Iterator
from pathlib import Path

from typer import Context
from typer.core import TyperGroup
from typer.main import get_command

from odoo_toolkit.main import app

README_FILE = Path(__file__).parent.parent / "README.md"


def get_commands(command: object, ctx: Context) -> Iterator[tuple[str, Context]]:
    """Get every command that isn't a group, by its full name, like `otk dev run`, with its context."""
    if not isinstance(command, TyperGroup):
        yield ctx.command_path, ctx
        return
    for name in command.list_commands(ctx):
        if sub := command.get_command(ctx, name):
            yield from get_commands(sub, sub.make_context(name, [], parent=ctx, resilient_parsing=True))


def format_entry(name: str, help_text: str) -> str:
    """Format an argument or option as a list item, with the defaults in code like the rest of the README."""
    help_text = re.sub(r"\[default: (.+?)\]$", r"[default: `\1`]", help_text)
    help_text = help_text.replace("[required]", "**[required]**")
    return f"* `{name}`: {help_text}" if help_text else f"* `{name}`"


def get_argument_name(name: str, nargs: int, *, required: bool) -> str:
    """Get the name of an argument as the README shows it, like `MODULES...`, or `[VERSION]` if it's optional."""
    name = name.upper() + ("..." if nargs != 1 else "")
    return name if required else f"[{name}]"


def render_usage(command_path: str, ctx: Context) -> str:
    """Render the usage line of a command, like `$ otk po export [OPTIONS] MODULES...`."""
    arguments = [
        get_argument_name(param.human_readable_name, param.nargs, required=param.required)
        for param in ctx.command.get_params(ctx)
        if param.param_type_name == "argument"
    ]
    # Like `otk dev run`, which passes what comes after `--` to Odoo.
    extra = ["[-- ODOO OPTIONS]"] if ctx.allow_extra_args else []
    return " ".join(["$", command_path, "[OPTIONS]", *arguments, *extra])


def render_lists(ctx: Context) -> tuple[list[str], dict[str | None, list[str]]]:
    """Render the arguments and the options of a command, like `--help` shows them.

    :return: The arguments, and the options per panel of `--help`, where None is the main one.
    """
    arguments: list[str] = []
    options: dict[str | None, list[str]] = {None: []}
    for param in ctx.command.get_params(ctx):
        if not (record := param.get_help_record(ctx)):
            continue
        if param.param_type_name == "argument":
            name = get_argument_name(param.human_readable_name, param.nargs, required=param.required)
            arguments.append(format_entry(name, record[1]))
        else:
            panel: str | None = getattr(param, "rich_help_panel", None)
            options.setdefault(panel, []).append(format_entry(*record))
    # `--help` comes last in the main panel, before the other panels.
    options[None].sort(key=lambda entry: entry.startswith("* `--help`"))
    return arguments, options


def sync_section(section: str, command_path: str, ctx: Context) -> str:
    """Write the usage, arguments and options of a command into its README section."""
    usage = render_usage(command_path, ctx)
    section = re.sub(
        rf"(### Usage\n\n```console\n)\$ {re.escape(command_path)}\b[^\n]*", lambda m: m.group(1) + usage, section,
    )
    arguments, options = render_lists(ctx)
    blocks = [f"### Arguments\n\n{''.join(f'{entry}\n' for entry in arguments)}"] if arguments else []
    blocks += [
        f"{'### Options' if panel is None else f'**{panel}**:'}\n\n{''.join(f'{entry}\n' for entry in entries)}"
        for panel, entries in options.items()
    ]
    section = re.sub(r"### Arguments\n\n(?:\* [^\n]*\n)+\n?", "", section)
    return re.sub(
        r"### Options\n\n(?:\* [^\n]*\n)+(?:\n\*\*[^\n]+\*\*:\n\n(?:\* [^\n]*\n)+)*",
        lambda _: "\n".join(blocks),
        section,
    )


def sync_readme(readme: str) -> tuple[str, list[str]]:
    """Synchronize every command section of the README.

    :return: The new README, and the commands that have no section in it.
    """
    root = get_command(app)
    missing: list[str] = []
    for command_path, ctx in get_commands(root, root.make_context("otk", [], resilient_parsing=True)):
        heading = f"## `{command_path}`\n"
        start = readme.find(heading)
        if start < 0:
            missing.append(command_path)
            continue
        end = readme.find("\n## ", start + len(heading))
        end = len(readme) if end < 0 else end + 1
        readme = readme[:start] + sync_section(readme[start:end], command_path, ctx) + readme[end:]
    return readme, missing


def main() -> int:
    """Update the README, or check that it's up to date."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Fail if the README isn't up to date, instead of writing.")
    args = parser.parse_args()

    current = README_FILE.read_text(encoding="utf-8")
    updated, missing = sync_readme(current)
    for command_path in missing:
        print(f"The README has no section for `{command_path}`.", file=sys.stderr)
    if updated == current:
        return 1 if missing else 0
    if args.check:
        sys.stdout.writelines(
            difflib.unified_diff(
                current.splitlines(keepends=True), updated.splitlines(keepends=True), "README.md", "README.md (synced)",
            ),
        )
        print("\nThe README isn't up to date. Run `uv run scripts/sync_readme.py`.", file=sys.stderr)
        return 1
    README_FILE.write_text(updated, encoding="utf-8")
    print("README.md updated.")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
