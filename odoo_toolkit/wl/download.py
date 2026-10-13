from pathlib import Path
from typing import Annotated

from typer import Exit, Option, Typer

from odoo_toolkit.common import (
    EMPTY_LIST,
    TransientProgress,
    filter_by_globs,
    normalize_list_option,
    print_command_title,
    print_error,
    print_success,
    print_warning,
)
from odoo_toolkit.po.common import get_cldr_lang

from .common import (
    WeblateApi,
    WeblateApiError,
    fetch_language_files,
    get_weblate_project_component_slugs,
    po_has_entries,
    print_failures,
)

app = Typer()


@app.command()
def download(
    project: Annotated[str, Option("--project", "-p", help="The Weblate project to download translations from.")],
    languages: Annotated[
        list[str],
        Option(
            "--language",
            "-l",
            help="The languages to download translations for. At least one language must be specified.",
        ),
    ],
    components: Annotated[
        list[str],
        Option(
            "--component",
            "-c",
            help="The Weblate components to download translations from. You can use glob patterns. Downloads all "
            "components if none are specified.",
        ),
    ] = EMPTY_LIST,
    ignore_components: Annotated[
        list[str],
        Option(
            "--ignore-component",
            "-i",
            help="The Weblate components to ignore when downloading translations. You can use glob patterns.",
        ),
    ] = EMPTY_LIST,
    query: Annotated[
        str | None,
        Option(
            "--query",
            "-q",
            help="A Weblate query to filter strings. Files without any matching string aren't saved.",
        ),
    ] = None,
) -> None:
    """Download specific PO files from Weblate.

    This command can be useful to bulk download specific PO files from Weblate, and filtering the strings using a
    Weblate query. The files will be saved in the current working directory with the name pattern
    `<project>-<component>-<language>.po`.
    """
    print_command_title(":inbox_tray: Odoo Weblate PO Download")

    try:
        weblate_api = WeblateApi()
    except NameError as e:
        print_error(str(e))
        raise Exit(1) from e

    language_codes = sorted({get_cldr_lang(lang) for lang in normalize_list_option(languages)})
    try:
        with TransientProgress() as progress:
            progress.add_task("Fetching components", total=None)
            selected = set(
                filter_by_globs(
                    get_weblate_project_component_slugs(weblate_api, project),
                    normalize_list_option(components),
                    normalize_list_option(ignore_components),
                ),
            )
    except WeblateApiError as e:
        print_error(f"Fetching the components failed: {e.summary}", str(e))
        raise Exit(1) from e
    if not selected:
        print_warning(f"No components of [b]{project}[/b] match.")
        raise Exit(1)

    saved: list[Path] = []
    failures: dict[str, str] = {}
    missing = 0
    without_matches = 0
    for language_code in language_codes:
        with TransientProgress() as progress:
            task = progress.add_task(f"Downloading {language_code}", total=len(selected))
            fetched = fetch_language_files(
                weblate_api, project, language_code, selected, query=query, progress=progress, task_id=task,
            )
        failures |= {f"{component}/{language_code}": reason for component, reason in fetched.failures.items()}
        missing += len(fetched.missing)
        files = fetched.files
        if query:
            files = {component: po_file for component, po_file in files.items() if po_has_entries(po_file)}
            without_matches += len(fetched.files) - len(files)
        language_saved, language_failures = _save(project, language_code, files)
        saved += language_saved
        failures |= language_failures

    skipped = [
        f"{missing} without the language" if missing else "",
        f"{without_matches} without strings matching the query" if without_matches else "",
    ]
    if skipped := [reason for reason in skipped if reason]:
        print_warning(f"Skipped the files of {' and '.join(skipped)}.")
    if failures:
        print_error(f"Failed to download {len(failures)} file(s):")
        print_failures(failures)
    if saved:
        print_success(f"Downloaded {len(saved)} file(s) to [u]{Path.cwd()}[/u].")
    if failures:
        raise Exit(1)


def _save(project: str, language_code: str, files: dict[str, bytes]) -> tuple[list[Path], dict[str, str]]:
    """Save the PO files of one language in the current directory.

    :return: The saved files, and why the others couldn't be saved.
    """
    saved: list[Path] = []
    failures: dict[str, str] = {}
    for component, po_file in sorted(files.items()):
        file_path = Path(f"{project}-{component}-{language_code}.po")
        try:
            file_path.write_bytes(po_file)
        except OSError as e:
            failures[f"{component}/{language_code}"] = f"writing {file_path} failed ({e})"
        else:
            saved.append(file_path)
    return saved, failures
