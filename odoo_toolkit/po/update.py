import os
from pathlib import Path
from typing import Annotated

from polib import POFile, pofile
from rich.console import RenderableType
from rich.tree import Tree
from typer import Argument, Exit, Option, Typer

from odoo_toolkit.common import (
    Status,
    TransientProgress,
    get_error_log_panel,
    get_valid_modules_to_path_mapping,
    print,
    print_command_title,
    print_error,
    print_header,
    print_success,
    print_warning,
)

from .common import LANG_TO_PLURAL_RULES, Lang, update_module_po

app = Typer()


@app.command()
def update(
    modules: Annotated[
        list[str],
        Argument(help='Update .po files for these Odoo modules, or either "all", "community", or "enterprise".'),
    ],
    languages: Annotated[
        list[Lang],
        Option("--languages", "-l", help='Update .po files for these languages, or "all".', case_sensitive=False),
    ] = [Lang.ALL],  # noqa: B006
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
) -> None:
    """Update Odoo translation files (.po) according to a new version of their .pot files.

    This command will update the .po files for the provided modules according to a new .pot file you might have exported
    in their `i18n` directory.\n
    \n
    > Without any options specified, the command is supposed to run from within the parent directory where your `odoo`
    and `enterprise` repositories are checked out with these names.
    """
    print_command_title(":arrows_counterclockwise: Odoo PO Update")

    module_to_path = get_valid_modules_to_path_mapping(
        modules=modules,
        com_path=com_path,
        ent_path=ent_path,
    )

    if not module_to_path:
        print_error("The provided modules are not available! Nothing to update ...")
        raise Exit

    modules = sorted(module_to_path.keys())
    print(f"Modules to update translation files for: [b]{'[/b], [b]'.join(modules)}[/b]\n")

    print_header(":speech_balloon: Update Translation Files")

    # Determine all .po file languages to update.
    if Lang.ALL in languages:
        languages = [lang for lang in Lang if lang != Lang.ALL]
    languages = sorted(languages)

    status = None
    for module in TransientProgress().track(modules, description="Updating .po files ..."):
        module_languages = [
            lang for lang in languages if (module_to_path[module] / "i18n" / f"{lang.value}.po").is_file()
        ]
        module_tree = Tree(f"[b]{module}[/b]")
        update_status = update_module_po(
            action=_update_po_for_lang,
            module=module,
            languages=module_languages,
            module_path=module_to_path[module],
            module_tree=module_tree,
        )
        print(module_tree, "")
        status = Status.PARTIAL if status and status != update_status else update_status

    match status:
        case Status.FAILURE:
            print_error("No translation files were updated!\n")
        case Status.PARTIAL:
            print_warning("Some translation files were updated correctly, while others weren't!\n")
        case Status.SUCCESS:
            print_success("All translation files were updated correctly!\n")


def _update_po_for_lang(lang: Lang, pot: POFile, module_path: Path) -> tuple[bool, RenderableType]:
    """Update a .po file for the given language and .pot file.

    :param lang: The language to update the .po file for.
    :type lang: :class:`odoo_toolkit.po.common.Lang`
    :param pot: The .pot file to get the terms from.
    :type pot: :class:`polib.POFile`
    :param module_path: The path to the module.
    :type module_path: :class:`pathlib.Path`
    :return: A tuple containing `True` if the update succeeded and `False` if it didn't, and the message to render.
    :rtype: tuple[bool, :class:`rich.console.RenderableType`]
    """
    try:
        po_file = module_path / "i18n" / f"{lang.value}.po"
        po = pofile(po_file)
        # Update the .po header and metadata.
        po.header = pot.header
        po.metadata.update({"Language": lang.value, "Plural-Forms": LANG_TO_PLURAL_RULES.get(lang, "")})
        # Merge the .po file with the .pot file to update all terms.
        po.merge(pot)
        # Remove entries that are obsolete or fuzzy.
        po[:] = [entry for entry in po if not entry.obsolete and not entry.fuzzy]
        # Sort the entries before saving, in the same way as `msgmerge -s`.
        po.sort(key=lambda entry: (entry.msgid, entry.msgctxt or ""))
        po.save()
    except (OSError, ValueError) as e:
        return False, get_error_log_panel(str(e), f"Updating {po_file.name} failed!")
    else:
        color = "orange" if po.untranslated_entries() else "green"
        return (
            True,
            f"[d]{po_file.parent}{os.sep}[/d][b]{po_file.name}[/b] :white_check_mark: "
            f"[{color}]({po.percent_translated()}% translated)[/{color}]",
        )
