import itertools
import os
from pathlib import Path
from typing import Annotated

from rich.table import Table
from typer import Exit, Option, Typer

from odoo_toolkit.common import (
    EMPTY_LIST,
    TransientProgress,
    filter_by_globs,
    get_error_log_panel,
    normalize_list_option,
    print,
    print_command_title,
    print_error,
    print_success,
    print_warning,
)
from odoo_toolkit.po.common import get_cldr_lang, get_language_name

from .common import (
    WEBLATE_TRANSLATIONS_FILE_ENDPOINT,
    WeblateApi,
    WeblateApiError,
    get_weblate_project_components,
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
            help="The Weblate components to download translations from. You can use glob patterns. Downloads all components if none are specified.",
        ),
    ] = EMPTY_LIST,
    query: Annotated[
        str | None,
        Option("--query", "-q", help="A Weblate query to filter strings."),
    ] = None,
    filter_empty: Annotated[
        bool,
        Option(
            "--filter-empty",
            help="If set, only download PO files that are not empty after applying the query filter.",
        ),
    ] = False,
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
        raise Exit from e

    languages_set = set(normalize_list_option(languages))
    components = normalize_list_option(components)
    try:
        with TransientProgress() as progress:
            progress_task = progress.add_task(
                "Fetching components...", total=None,
            )
            components = filter_by_globs(get_weblate_project_components(weblate_api, project), components)
            progress.update(progress_task, completed=1, total=1)
    except WeblateApiError as e:
        print_error("Weblate API Error: Failed to fetch components for project.", str(e))
        raise Exit from e

    successful_downloads = 0
    failed_downloads = 0
    empty_files_skipped = 0

    with TransientProgress() as progress:
        progress_task = progress.add_task(
            "Downloading PO files...",
            total=len(components) * len(languages_set),
        )
        download_table = Table(box=None, pad_edge=False, show_header=False)

        params = {"q": query, "format": "po"} if query else None
        for component, language in itertools.product(sorted(components), sorted(languages_set)):
            language_code = get_cldr_lang(language)
            language_name = get_language_name(language_code)
            progress.update(progress_task, description=f"Downloading [b]{language_code}.po[/b] for {project}/{component}...")
            try:
                po_file: bytes = weblate_api.get_bytes(
                    WEBLATE_TRANSLATIONS_FILE_ENDPOINT.format(
                        project=project,
                        component=component,
                        language=language_code,
                    ),
                    params=params,
                )
            except WeblateApiError as e:
                failed_downloads += 1
                download_table.add_row(
                    f"{project}/{component} ({language_name})",
                    get_error_log_panel(str(e), "Downloading failed!"),
                )
                progress.advance(progress_task)
                continue
            if filter_empty and b'msgid ""\nmsgstr ""' in po_file and po_file.count(b'msgid "') <= 1:
                # Filter out empty PO files (only header present).
                empty_files_skipped += 1
                download_table.add_row(
                    f"[d]{project}/{component} ({language_name})[/d]",
                    "[d]Skipped empty PO file[/d] :wastebasket:",
                )
                progress.advance(progress_task)
                continue
            file_path = Path(f"{project}-{component}-{language_code}.po")
            try:
                with file_path.open("wb") as f:
                    f.write(po_file)
            except OSError as e:
                download_table.add_row(
                    f"{project}/{component} ({language_name})",
                    get_error_log_panel(str(e), f"Writing PO file to {file_path} failed!"),
                )
                progress.advance(progress_task)
                break
            else:
                successful_downloads += 1
                absolute_file_path = file_path.resolve()
                download_table.add_row(
                    f"{project}/{component} ({language_name})",
                    f"[d]{absolute_file_path.parent}{os.sep}[/d][b]{absolute_file_path.name}[/b] :white_check_mark:",
                )
                progress.advance(progress_task)

    print(download_table, "")

    empty_files_message = (f" ({empty_files_skipped} empty files skipped)" if empty_files_skipped else "")
    if successful_downloads and not failed_downloads:
        print_success(f"Successfully downloaded {successful_downloads} translations{empty_files_message}.")
    elif not successful_downloads and failed_downloads:
        print_error(f"Failed to download {failed_downloads} translations{empty_files_message}.")
    elif not successful_downloads and not failed_downloads and empty_files_skipped:
        print_warning("No translations downloaded. All files were empty and skipped.")
    else:
        print_warning(
            f"Successfully downloaded {successful_downloads} translations, "
            f"but failed to download {failed_downloads} translations{empty_files_message}.",
        )
