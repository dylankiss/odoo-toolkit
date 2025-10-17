import os
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

from polib import POFile, pofile
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

from .common import ODOO_LANGUAGES, get_plural_forms, update_module_po

app = Typer()


@app.command()
def create(
    modules: Annotated[
        list[str],
        Argument(help="Create `.po` files for these Odoo modules, or either `all`, `community`, or `enterprise`."),
    ],
    languages: Annotated[
        list[str],
        Option(
            "--language",
            "-l",
            help="Create `.po` files for these language codes, or `all` available languages in Odoo.",
        ),
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

    > Uses the gettext `msginit` command if available.\n
    \n
    This command will provide you with a clean `.po` file per language you specified for the given modules. It basically
    copies all entries from the `.pot` file in the module and completes the metadata with the right language
    information. All generated `.po` files will be saved in the respective modules' `i18n` directories.\n
    \n
    > Without any options specified, the command is supposed to run from within the parent directory where your `odoo`
    and `enterprise` repositories are checked out with these names.
    """
    print_command_title(":memo: Odoo PO Create")

    languages = normalize_list_option(languages)

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
    if "all" in languages:
        languages = list(ODOO_LANGUAGES)
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


def _create_po_for_lang(lang: str, pot_path: Path, module_path: Path) -> tuple[bool, RenderableType]:
    """Create a .po file for the given language code and .pot file.

    :param lang: The language code to create the .po file for.
    :param pot_path: The .pot file path to get the terms from.
    :param module_path: The path to the module.
    :return: A tuple containing `True` if the creation succeeded and `False` if it didn't, and the message to render.
    """
    po_path = module_path / "i18n" / f"{lang}.po"
    if po_path.is_file():
        return True, f"[d]{po_path.parent}{os.sep}[/d][b]{po_path.name}[/b] (Already exists)"

    if shutil.which("msginit"):
        # We prefer to use the `msginit` command if available, as it is faster than using `polib`.
        cmd_env = os.environ | {"GETTEXTCLDRDIR": str(Path(__file__).parent / "cldr-common-47")}
        try:
            cmd = [
                "msginit",
                "--no-translator",
                f"--locale={lang}",
                f"--input={pot_path}",
                f"--output-file={po_path}",
            ]
            subprocess.run(cmd, env=cmd_env, capture_output=True, check=True)
        except subprocess.CalledProcessError as e:
            return False, get_error_log_panel(e.stderr.decode().strip(), f"Creating {po_path.name} failed!")
        else:
            return True, f"[d]{po_path.parent}{os.sep}[/d][b]{po_path.name}[/b] :white_check_mark:"
    else:
        # Fallback to using `polib` if `msginit` is not available.
        try:
            po = POFile()
            pot = pofile(pot_path)
            po.header = pot.header
            po.metadata = pot.metadata.copy()
            # Set the correct language and plural forms in the .po file.
            po.metadata.update({"Language": lang, "Plural-Forms": get_plural_forms(lang)})
            for entry in pot:
                # Just add all entries in the .pot to the .po file.
                po.append(entry)
            po.save(str(po_path))
        except (OSError, ValueError) as e:
            return False, get_error_log_panel(str(e), f"Creating {po_path.name} failed!")
        else:
            return True, f"[d]{po_path.parent}{os.sep}[/d][b]{po_path.name}[/b] :white_check_mark:"
