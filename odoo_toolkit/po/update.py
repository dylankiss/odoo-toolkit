import os
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

from polib import pofile
from rich.console import RenderableType
from rich.tree import Tree
from typer import Argument, Exit, Option, Typer

from odoo_toolkit.common import (
    EMPTY_LIST,
    Status,
    TransientProgress,
    get_error_log_panel,
    get_valid_modules_to_path_mapping,
    normalize_list_option,
    print,
    print_command_title,
    print_error,
    print_header,
    print_success,
    print_warning,
)

from .common import update_module_po

app = Typer()


@app.command()
def update(
    modules: Annotated[
        list[str],
        Argument(help="Update `.po` files for these Odoo modules, or either `all`, `community`, or `enterprise`."),
    ],
    languages: Annotated[
        list[str],
        Option("--language", "-l", help="Update `.po` files for these language codes, or `all`."),
    ] = ["all"],  # noqa: B006
    com_path: Annotated[
        Path,
        Option(
            "--com-path",
            "-c",
            help="Specify the path to your Odoo Community repository.",
        ),
    ] = Path("odoo"),
    ent_path: Annotated[
        Path,
        Option(
            "--ent-path",
            "-e",
            help="Specify the path to your Odoo Enterprise repository.",
        ),
    ] = Path("enterprise"),
    extra_addons_paths: Annotated[
        list[Path],
        Option(
            "--addons-path",
            "-a",
            help="Specify extra addons paths if your modules are not in Community or Enterprise.",
        ),
    ] = EMPTY_LIST,
) -> None:
    """Update Odoo translation files (`.po`) according to a new version of their `.pot` files.

    > Uses the gettext `msgmerge` and `msgattrib` commands if available.\n
    \n
    This command will update the `.po` files for the provided modules according to a new `.pot` file you might have
    exported in their `i18n` directory.\n
    \n
    > Without any options specified, the command is supposed to run from within the parent directory where your `odoo`
    and `enterprise` repositories are checked out with these names.
    """
    print_command_title(":arrows_counterclockwise: Odoo PO Update")

    languages = sorted(normalize_list_option(languages))

    module_to_path = get_valid_modules_to_path_mapping(
        modules=modules,
        com_path=com_path,
        ent_path=ent_path,
        extra_addons_paths=extra_addons_paths,
    )

    if not module_to_path:
        print_error("The provided modules are not available! Nothing to update ...")
        raise Exit

    modules = sorted(module_to_path.keys())
    print(f"Modules to update translation files for: [b]{'[/b], [b]'.join(modules)}[/b]\n")

    print_header(":speech_balloon: Update Translation Files")

    status = None
    failed_langs_per_module: dict[str, list[str]] = {}
    with TransientProgress() as progress:
        progress_task = progress.add_task("Updating .po files", total=len(modules))
        for module in modules:
            progress.update(progress_task, description=f"Updating .po files for [b]{module}[/b]")
            if "all" in languages:
                module_languages = sorted([
                    lang.stem
                    for lang in (module_to_path[module] / "i18n").glob("*.po")
                    if lang.is_file()
                ])
            else:
                module_languages = sorted([
                    lang for lang in languages if (module_to_path[module] / "i18n" / f"{lang}.po").is_file()
                ])
            module_tree = Tree(f"[b]{module}[/b]")
            update_status, failed_langs = update_module_po(
                action=_update_po_for_lang,
                module=module,
                languages=module_languages,
                module_path=module_to_path[module],
                module_tree=module_tree,
            )
            if failed_langs:
                failed_langs_per_module[module] = failed_langs
            print(module_tree, "")
            status = Status.PARTIAL if status and status != update_status else update_status
            progress.advance(progress_task, 1)

    failed_langs_per_module_str = "\n".join(
        f"- [b]{module}[/b]: {', '.join(langs)}" for module, langs in failed_langs_per_module.items()
    )
    match status:
        case Status.SUCCESS:
            print_success("All translation files were updated correctly!\n")
        case Status.PARTIAL:
            print_warning(
                f"Some translation files were updated correctly, while others weren't!\n\n"
                f"{failed_langs_per_module_str}\n",
            )
        case _:
            print_error("No translation files were updated!\n")


def _update_po_for_lang(lang: str, pot_path: Path, module_path: Path) -> tuple[bool, RenderableType]:
    """Update a .po file for the given language and .pot file.

    :param lang: The language code to update the .po file for.
    :param pot_path: The .pot file to get the terms from.
    :param module_path: The path to the module.
    :return: A tuple containing `True` if the update succeeded and `False` if it didn't, and the message to render.
    """
    po_path = module_path / "i18n" / f"{lang}.po"

    if shutil.which("msgmerge") and shutil.which("msgattrib"):
        # We prefer to use the `msgmerge` command if available, as it is faster than `polib`.
        try:
            # Merge the .po file with the .pot file to update all terms.
            cmd = [
                "msgmerge",
                "--update",
                "--backup=none",
                "--no-fuzzy-matching",
                f"--lang={lang}",
                str(po_path),
                str(pot_path),
            ]
            subprocess.run(cmd, capture_output=True, check=True)
            # Remove entries that are obsolete.
            cmd = [
                "msgattrib",
                "--no-obsolete",
                f"--output-file={po_path}",
                "--sort-output",
                str(po_path),
            ]
            subprocess.run(cmd, capture_output=True, check=True)
        except subprocess.CalledProcessError as e:
            return False, get_error_log_panel(e.stderr.decode().strip(), f"Updating {po_path.name} failed!")
        else:
            return True, f"[d]{po_path.parent}{os.sep}[/d][b]{po_path.name}[/b] :white_check_mark:"
    else:
        # Fallback to using `polib` if `msgmerge` is not available.
        try:
            po = pofile(po_path)
            pot = pofile(pot_path)
            # Merge the .po file with the .pot file to update all terms.
            po.merge(pot)
            # Remove entries that are obsolete.
            po[:] = [entry for entry in po if not entry.obsolete]
            po.save()
        except (OSError, ValueError) as e:
            return False, get_error_log_panel(str(e), f"Updating {po_path.name} failed!")
        else:
            return True, f"[d]{po_path.parent}{os.sep}[/d][b]{po_path.name}[/b] :white_check_mark:"
