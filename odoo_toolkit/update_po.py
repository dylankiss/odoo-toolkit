import os
import re
import subprocess
from pathlib import Path
from subprocess import CalledProcessError
from typing import Annotated

from rich.panel import Panel
from rich.progress import track
from rich.tree import Tree
from typer import Argument, Option

from .common import app, log


@app.command()
def update_po(
    modules: Annotated[
        list[str], Argument(help='The Odoo modules to update or either "all", "community", or "enterprise"')
    ],
    com_path: Annotated[
        Path,
        Option(
            "--com-path",
            "-c",
            help="The path to the Odoo Community repo",
        ),
    ] = Path("odoo"),
    ent_path: Annotated[
        Path,
        Option(
            "--ent-path",
            "-e",
            help="The path to the Odoo Enterprise repo",
        ),
    ] = Path("enterprise"),
):
    """
    Update Odoo translation files (.po) according to a new version of its .pot file.
    """
    log(
        Panel.fit(
            ":arrows_counterclockwise: Odoo PO Update",
            style="bold magenta",
            border_style="bold magenta",
        ),
        "",
    )

    # Compute the paths to all Odoo modules.
    base_modules_path = com_path.expanduser().resolve() / "odoo" / "addons"
    com_modules_path = com_path.expanduser().resolve() / "addons"
    ent_modules_path = ent_path.expanduser().resolve()

    # Determine all modules belonging to base, community or enterprise.
    base_modules = {f.parent.name for f in base_modules_path.glob("*/__manifest__.py")}
    com_modules = {f.parent.name for f in com_modules_path.glob("*/__manifest__.py")}
    ent_modules = {f.parent.name for f in ent_modules_path.glob("*/__manifest__.py")}
    all_modules = base_modules | com_modules | ent_modules

    # Determine all modules to update.
    if len(modules) == 1 and modules[0] == "all":
        modules_to_update = all_modules
    elif len(modules) == 1 and modules[0] == "community":
        modules_to_update = base_modules | com_modules
    elif len(modules) == 1 and modules[0] == "enterprise":
        modules_to_update = ent_modules
    elif len(modules) == 1:
        modules_to_update = set(modules[0].split(",")) & all_modules
    else:
        modules_to_update = {re.sub(r",", "", m) for m in modules if m in all_modules}

    if not modules_to_update:
        log(":exclamation_mark: [red]The provided modules are not available! Nothing to update ...\n")
        return

    log(f"Modules to update: [bold]{'[/bold], [bold]'.join(sorted(modules_to_update))}[/bold]\n")

    # Map each module to its directory.
    modules_to_path_mapping = {
        module: path
        for modules, path in [
            (base_modules & modules_to_update, base_modules_path),
            (com_modules & modules_to_update, com_modules_path),
            (ent_modules & modules_to_update, ent_modules_path),
        ]
        for module in modules
    }

    log(Panel.fit(":speech_balloon: [bold]Update Translations"))
    modules = sorted(modules_to_update)
    success = failure = False

    for module in modules:
        update_tree = Tree(f"[bold]{module}")
        i18n_path = modules_to_path_mapping[module] / module / "i18n"
        if not i18n_path.exists():
            continue
        po_files = sorted(i18n_path.glob("*.po"))
        if not po_files:
            update_tree.add("No translation files found!")
            log(update_tree, "")
            continue
        pot_file = i18n_path / f"{module}.pot"
        for po_file in track(po_files, description=f"Updating [bold]{module}", transient=True):
            try:
                msgmerge = subprocess.run(
                    [
                        "msgmerge",
                        "--no-fuzzy-matching",
                        "-q",
                        po_file,
                        pot_file,
                    ],
                    capture_output=True,
                    check=True,
                )
            except CalledProcessError as error:
                failure = True
                update_tree.add(
                    Panel(
                        error.stderr.strip(),
                        title=f"Updating {po_file.name} failed during msgmerge!",
                        title_align="left",
                        style="red",
                        border_style="bold red",
                    )
                )
                continue
            try:
                subprocess.run(
                    [
                        "msgattrib",
                        "--no-fuzzy",
                        "--no-obsolete",
                        "-o",
                        po_file,
                    ],
                    input=msgmerge.stdout,
                    check=True,
                )
                success = True
                update_tree.add(f"[dim]{po_file.parent}{os.sep}[/dim][bold]{po_file.name}[/bold] :white_check_mark:")
            except CalledProcessError as error:
                failure = True
                update_tree.add(
                    Panel(
                        error.stderr.strip(),
                        title=f"Updating {po_file.name} failed during msgattrib!",
                        title_align="left",
                        style="red",
                        border_style="bold red",
                    )
                )
                continue

        log(update_tree, "")

    if not success and failure:
        log(":exclamation_mark: [red]All translation files failed to update!\n")
    elif success and failure:
        log(":warning: [yellow]Some translation files were updated correctly, while others failed!\n")
    else:
        log(":white_check_mark: [green]All translation files were updated correctly!\n")
