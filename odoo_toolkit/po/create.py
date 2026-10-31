import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

from polib import POFile, pofile
from rich.console import RenderableType
from typer import Argument, Exit, Option, Typer

from odoo_toolkit.common import (
    EMPTY_LIST,
    get_error_log_panel,
    normalize_list_option,
    print,
    print_command_title,
    print_error,
    print_header,
    print_success,
    print_warning,
)

from .common import (
    ODOO_LANGUAGES,
    format_failures,
    format_po_written,
    get_nplurals,
    get_plural_forms,
    process_modules,
    select_modules,
)

app = Typer()

PLURAL_FORMS_PATTERN = re.compile(r'^"Plural-Forms: .*\\n"$', flags=re.MULTILINE)
# Matches the empty translations of a plural entry, which need one `msgstr[n]` per plural form.
EMPTY_PLURALS_PATTERN = re.compile(r'(?:^msgstr\[\d+\] ""\n)+', flags=re.MULTILINE)


@app.command()
def create(
    modules: Annotated[
        list[str],
        Argument(
            help="Create `.po` files for these Odoo modules, or either `all`, `community`, `enterprise`, "
                 "`community-l10n`, or `enterprise-l10n`.",
        ),
    ],
    languages: Annotated[
        list[str],
        Option(
            "--language",
            "-l",
            help="Create `.po` files for these language codes, or `all` available languages in Odoo.",
        ),
    ],
    exclude: Annotated[
        list[str],
        Option(
            "--exclude",
            "-x",
            help="Exclude these modules from being created, or `default` for localizations, themes, hardware and tests.",
        ),
    ] = EMPTY_LIST,
    path_filters: Annotated[
        list[Path],
        Option("--path-filter", "-f", help="Only include modules within these paths."),
    ] = EMPTY_LIST,
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
    selection = select_modules(
        modules,
        exclude=exclude,
        path_filters=path_filters,
        com_path=com_path,
        ent_path=ent_path,
        extra_addons_paths=extra_addons_paths,
    )
    if not selection.module_to_path:
        print_error("The provided modules are not available! Nothing to create ...\n")
        raise Exit(1)

    print(f"Modules to create translation files for: [b]{'[/b], [b]'.join(sorted(selection.module_to_path))}[/b]\n")
    print_header(":speech_balloon: Create Translation Files")

    # Determine all .po file languages to create.
    languages = sorted(ODOO_LANGUAGES if "all" in languages else languages)
    processed, failures = process_modules(
        selection.module_to_path,
        languages_of=lambda _: languages,
        action=_create_po_for_lang,
        description="Creating",
    )

    if not processed:
        print_warning("There were no .pot files to create translation files from.\n")
    elif failures:
        failed_str = format_failures(failures)
        print_error(f"Some translation files weren't created:\n\n{failed_str}\n")
        raise Exit(1)
    else:
        print_success("All translation files were created correctly!\n")


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
        cmd_env = os.environ | {"GETTEXTCLDRDIR": str(Path(__file__).parent / "cldr-common-48.2")}
        try:
            cmd = [
                "msginit",
                "--no-translator",
                f"--locale={lang}",
                f"--input={pot_path}",
                f"--output-file={po_path}",
            ]
            subprocess.run(cmd, env=cmd_env, capture_output=True, check=True)
            # msginit prefers its own plural rules for common languages over the CLDR ones Weblate uses, which can have
            # more plural forms, like 3 instead of 2 for French. Use the CLDR rule, with a translation per form.
            plural_forms = f'"Plural-Forms: {get_plural_forms(lang)}\\n"'
            empty_plurals = "".join(f'msgstr[{i}] ""\n' for i in range(get_nplurals(lang)))
            content = PLURAL_FORMS_PATTERN.sub(lambda _: plural_forms, po_path.read_text(encoding="utf-8"), count=1)
            po_path.write_text(EMPTY_PLURALS_PATTERN.sub(lambda _: empty_plurals, content), encoding="utf-8")
        except subprocess.CalledProcessError as e:
            return False, get_error_log_panel(e.stderr.decode().strip(), f"Creating {po_path.name} failed!")
        except OSError as e:
            return False, get_error_log_panel(str(e), f"Creating {po_path.name} failed!")
        else:
            return True, format_po_written(po_path)
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
                # Add all entries in the .pot to the .po file, with a translation per plural form of the language.
                if entry.msgid_plural:
                    entry.msgstr_plural = dict.fromkeys(range(get_nplurals(lang)), "")
                po.append(entry)
            po.save(str(po_path))
        except (OSError, ValueError) as e:
            return False, get_error_log_panel(str(e), f"Creating {po_path.name} failed!")
        else:
            return True, format_po_written(po_path)
