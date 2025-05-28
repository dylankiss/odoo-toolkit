import os
from pathlib import Path
from typing import Annotated

from polib import POFile
from rich.console import RenderableType
from rich.tree import Tree
from typer import Argument, Exit, Option, Typer

from odoo_toolkit.common import (
    EMPTY_LIST,
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
def create(
    modules: Annotated[
        list[str],
        Argument(help="Create `.po` files for these Odoo modules, or either `all`, `community`, or `enterprise`."),
    ],
    languages: Annotated[
        list[Lang],
        Option("--languages", "-l", help="Create `.po` files for these languages, or `all`.", case_sensitive=False),
    ],
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
    """Create Odoo translation files (`.po`) according to their `.pot` files.

    This command will provide you with a clean `.po` file per language you specified for the given modules. It basically
    copies all entries from the `.pot` file in the module and completes the metadata with the right language
    information. All generated `.po` files will be saved in the respective modules' `i18n` directories.\n
    \n
    > Without any options specified, the command is supposed to run from within the parent directory where your `odoo`
    and `enterprise` repositories are checked out with these names.
    """
    print_command_title(":memo: Odoo PO Create")

    module_to_path = get_valid_modules_to_path_mapping(
        modules=modules,
        com_path=com_path,
        ent_path=ent_path,
        extra_addons_paths=extra_addons_paths,
    )

    if not module_to_path:
        print_error("The provided modules are not available! Nothing to create ...")
        raise Exit

    modules = sorted(module_to_path.keys())
    print(f"Modules to create translation files for: [b]{'[/b], [b]'.join(modules)}[/b]\n")

    print_header(":speech_balloon: Create Translation Files")

    # Determine all .po file languages to create.
    if Lang.ALL in languages:
        languages = [lang for lang in Lang if lang != Lang.ALL]
    languages = sorted(languages)

    status = None
    with TransientProgress() as progress:
        progress_task = progress.add_task("Creating .po files", total=len(modules))
        for module in modules:
            progress.update(progress_task, description=f"Creating .po files for [b]{module}[/b]")
            module_tree = Tree(f"[b]{module}[/b]")
            create_status = update_module_po(
                action=_create_po_for_lang,
                module=module,
                languages=languages,
                module_path=module_to_path[module],
                module_tree=module_tree,
            )
            print(module_tree, "")
            status = Status.PARTIAL if status and status != create_status else create_status
            progress.advance(progress_task, 1)

    match status:
        case Status.FAILURE:
            print_error("No translation files were created!\n")
        case Status.PARTIAL:
            print_warning("Some translation files were created correctly, while others weren't!\n")
        case Status.SUCCESS:
            print_success("All translation files were created correctly!\n")
        case _:
            pass


def _create_po_for_lang(lang: Lang, pot: POFile, module_path: Path) -> tuple[bool, RenderableType]:
    """Create a .po file for the given language and .pot file.

    :param lang: The language to create the .po file for.
    :param pot: The .pot file to get the terms from.
    :param module_path: The path to the module.
    :return: A tuple containing `True` if the creation succeeded and `False` if it didn't, and the message to render.
    """
    po_file = module_path / "i18n" / f"{lang.value}.po"
    if po_file.is_file():
        return True, f"[d]{po_file.parent}{os.sep}[/d][b]{po_file.name}[/b] (Already exists)"

    po = POFile()
    po.header = pot.header
    po.metadata = pot.metadata.copy()
    # Set the correct language and plural forms in the .po file.
    po.metadata.update({"Language": lang.value, "Plural-Forms": LANG_TO_PLURAL_RULES.get(lang, "")})
    for entry in pot:
        # Just add all entries in the .pot to the .po file.
        po.append(entry)
    try:
        po.save(str(po_file))
    except (OSError, ValueError) as e:
        return False, get_error_log_panel(str(e), f"Creating {po_file.name} failed!")
    else:
        return True, f"[d]{po_file.parent}{os.sep}[/d][b]{po_file.name}[/b] :white_check_mark:"
