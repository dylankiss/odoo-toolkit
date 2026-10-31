import shlex
import shutil
import subprocess
from functools import partial
from pathlib import Path
from typing import Annotated

from polib import POEntry, pofile
from rich.console import RenderableType
from rich.markup import escape
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

from .common import format_failures, format_po_written, process_modules, select_modules

app = Typer()


@app.command()
def update(
    modules: Annotated[
        list[str],
        Argument(
            help="Update `.po` files for these Odoo modules, or either `all`, `community`, `enterprise`, "
            "`community-l10n`, or `enterprise-l10n`.",
        ),
    ],
    languages: Annotated[
        list[str],
        Option("--language", "-l", help="Update `.po` files for these language codes, or `all`."),
    ] = ["all"],  # noqa: B006
    only_translated: Annotated[
        bool,
        Option(
            "--only-translated",
            "-t",
            help="Only keep translated terms in the updated `.po` files, removing all untranslated terms.",
        ),
    ] = False,
    exclude: Annotated[
        list[str],
        Option(
            "--exclude",
            "-x",
            help="Exclude these modules from being updated, or `default` for localizations, themes, hardware and tests.",
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
    selection = select_modules(
        modules,
        exclude=exclude,
        path_filters=path_filters,
        com_path=com_path,
        ent_path=ent_path,
        extra_addons_paths=extra_addons_paths,
    )
    if not selection.module_to_path:
        print_error("The provided modules are not available! Nothing to update ...\n")
        raise Exit(1)

    print(f"Modules to update translation files for: [b]{'[/b], [b]'.join(sorted(selection.module_to_path))}[/b]\n")
    print_header(":speech_balloon: Update Translation Files")

    def languages_of(module_path: Path) -> list[str]:
        existing = sorted(po.stem for po in (module_path / "i18n").glob("*.po") if po.is_file())
        return existing if "all" in languages else [lang for lang in languages if lang in existing]

    processed, failures = process_modules(
        selection.module_to_path,
        languages_of=languages_of,
        action=partial(_update_po_for_lang, only_translated=only_translated),
        description="Updating",
    )

    if not processed:
        print_warning("There were no translation files to update.\n")
        return
    if not failures:
        print_success("All translation files were updated correctly!\n")
        return
    retry_cmd = get_retry_command(
        modules=sorted(failures),
        languages=sorted({lang for langs in failures.values() for lang in langs}),
        only_translated=only_translated,
        exclude=normalize_list_option(exclude),
        path_filters=path_filters,
        com_path=com_path,
        ent_path=ent_path,
        extra_addons_paths=extra_addons_paths,
    )
    failed_str = format_failures(failures)
    print_error(
        f"Some translation files weren't updated:\n\n{failed_str}\n\n"
        "You can run the command again for them using:\n"
        f"[b]{escape(shlex.join(retry_cmd))}[/b]\n",
    )
    raise Exit(1)


def get_retry_command(
    *,
    modules: list[str],
    languages: list[str],
    only_translated: bool,
    exclude: list[str],
    path_filters: list[Path],
    com_path: Path,
    ent_path: Path,
    extra_addons_paths: list[Path],
) -> list[str]:
    """Get the command to update the failed modules and languages again, with every option that was given."""
    cmd = ["otk", "po", "update", *modules]
    cmd += [arg for lang in languages for arg in ("-l", lang)]
    if only_translated:
        cmd.append("-t")
    cmd += [arg for e in exclude for arg in ("-x", e)]
    cmd += [arg for p in path_filters for arg in ("-f", str(p))]
    if com_path != Path("odoo"):
        cmd += ["-c", str(com_path)]
    if ent_path != Path("enterprise"):
        cmd += ["-e", str(ent_path)]
    cmd += [arg for p in extra_addons_paths for arg in ("-a", str(p))]
    return cmd


def is_translated(entry: POEntry) -> bool:
    """Return whether an entry counts as translated for `msgattrib --translated`, so both ways give the same result.

    Fuzzy entries count as translated, and plural entries as soon as their first form is.
    """
    if entry.msgid_plural:
        return bool(entry.msgstr_plural.get(0))
    return bool(entry.msgstr)


def _update_po_for_lang(
    lang: str, pot_path: Path, module_path: Path, *, only_translated: bool,
) -> tuple[bool, RenderableType]:
    """Update a .po file for the given language and .pot file.

    :param lang: The language code to update the .po file for.
    :param pot_path: The .pot file to get the terms from.
    :param module_path: The path to the module.
    :param only_translated: Whether to only keep translated terms in the updated `.po` files, removing all untranslated
        terms.
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
                *(["--translated"] if only_translated else []),
                f"--output-file={po_path}",
                str(po_path),
            ]
            subprocess.run(cmd, capture_output=True, check=True)
        except subprocess.CalledProcessError as e:
            return False, get_error_log_panel(e.stderr.decode().strip(), f"Updating {po_path.name} failed!")
        else:
            return True, format_po_written(po_path)
    else:
        # Fallback to using `polib` if `msgmerge` is not available.
        try:
            po = pofile(po_path)
            pot = pofile(pot_path)
            # Merge the .po file with the .pot file to update all terms.
            po.merge(pot)
            # Remove entries that are obsolete.
            po[:] = [entry for entry in po if not entry.obsolete]
            if only_translated:
                po[:] = [entry for entry in po if is_translated(entry)]
            po.save()
        except (OSError, ValueError) as e:
            return False, get_error_log_panel(str(e), f"Updating {po_path.name} failed!")
        else:
            return True, format_po_written(po_path)
