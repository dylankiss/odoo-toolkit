import io
import itertools
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    WEBLATE_PROJECT_LANGUAGE_FILE_ENDPOINT,
    WEBLATE_TRANSLATIONS_FILE_ENDPOINT,
    WeblateApi,
    WeblateApiError,
    get_weblate_project_component_slugs,
)

app = Typer()


@app.command()
def download(  # noqa: C901, PLR0912, PLR0915
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
            components = filter_by_globs(get_weblate_project_component_slugs(weblate_api, project), components)
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

        results: list[tuple[str, str, str, str]] = []
        if query:
            # Per-file downloads: needed to pass the query filter to each translation endpoint.
            params: dict[str, str] = {"q": query, "format": "po"}
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {
                    executor.submit(
                        _download_translation, weblate_api, project, component, language, params, filter_empty,
                    ): (component, language)
                    for component, language in itertools.product(sorted(components), sorted(languages_set))
                }
                for future in as_completed(futures):
                    component, _ = futures[future]
                    results.append((component, *future.result()))
                    progress.advance(progress_task)
        else:
            # Bulk per-language ZIP downloads: one request per language instead of one per
            # component x language pair. Each ZIP contains PO files for all components.
            with ThreadPoolExecutor(max_workers=10) as executor:
                lang_futures = {
                    executor.submit(
                        _download_language_zip, weblate_api, project, language, set(components), filter_empty,
                    ): language
                    for language in sorted(languages_set)
                }
                for future in as_completed(lang_futures):
                    language_results = future.result()
                    results.extend(language_results)
                    progress.advance(progress_task, len(language_results))

        for component, language_code, status, detail in sorted(results, key=lambda r: (r[0], r[1])):
            language_name = get_language_name(language_code)
            if status == "success":
                successful_downloads += 1
                absolute_file_path = Path(detail)
                download_table.add_row(
                    f"{project}/{component} ({language_name})",
                    f"[d]{absolute_file_path.parent}{os.sep}[/d][b]{absolute_file_path.name}[/b] :white_check_mark:",
                )
            elif status == "failed":
                failed_downloads += 1
                download_table.add_row(
                    f"{project}/{component} ({language_name})",
                    get_error_log_panel(detail, "Downloading failed!"),
                )
            elif status == "empty":
                empty_files_skipped += 1
                download_table.add_row(
                    f"[d]{project}/{component} ({language_name})[/d]",
                    "[d]Skipped empty PO file[/d] :wastebasket:",
                )
            elif status == "write_error":
                failed_downloads += 1
                file_path = Path(f"{project}-{component}-{language_code}.po")
                download_table.add_row(
                    f"{project}/{component} ({language_name})",
                    get_error_log_panel(detail, f"Writing PO file to {file_path} failed!"),
                )

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


def _download_language_zip(
    weblate_api: WeblateApi,
    project: str,
    language: str,
    components: set[str],
    filter_empty: bool,
) -> list[tuple[str, str, str, str]]:
    """Download all translations for one language as a ZIP. Returns (component, language_code, status, detail) per component.

    Weblate stores all components that share a Git repo under a single VCS directory.
    Inside the ZIP, every Odoo PO file sits at ``…/{module}/i18n/{language}.po``, so the
    component slug (= module name) is always at ``parts[-3]`` of the ZIP entry path.
    """
    language_code = get_cldr_lang(language)
    try:
        zip_bytes = weblate_api.get_bytes(
            WEBLATE_PROJECT_LANGUAGE_FILE_ENDPOINT.format(project=project, language=language_code),
        )
    except WeblateApiError as e:
        return [(component, language_code, "failed", str(e)) for component in sorted(components)]

    results: list[tuple[str, str, str, str]] = []
    found: set[str] = set()
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                parts = name.split("/")
                # All Odoo PO files live at …/{module}/i18n/{language}.po
                if len(parts) < 3 or parts[-2] != "i18n" or parts[-1] != f"{language_code}.po":  # noqa: PLR2004
                    continue
                component_slug = parts[-3]
                if component_slug not in components:
                    continue
                found.add(component_slug)
                content = zf.read(name)
                if filter_empty and b'msgid ""\nmsgstr ""' in content and content.count(b'msgid "') <= 1:
                    results.append((component_slug, language_code, "empty", ""))
                    continue
                file_path = Path(f"{project}-{component_slug}-{language_code}.po")
                try:
                    file_path.write_bytes(content)
                except OSError as e:
                    results.append((component_slug, language_code, "write_error", str(e)))
                else:
                    results.append((component_slug, language_code, "success", str(file_path.resolve())))
    except zipfile.BadZipFile as e:
        return [(component, language_code, "failed", str(e)) for component in sorted(components)]

    results.extend(
        (component, language_code, "failed", f"No PO file found in bulk ZIP for '{component}'.")
        for component in sorted(components - found)
    )
    return results


def _download_translation(
    weblate_api: WeblateApi,
    project: str,
    component: str,
    language: str,
    params: dict[str, str] | None,
    filter_empty: bool,
) -> tuple[str, str, str]:
    """Download a single translation file. Returns (language_code, status, detail).

    status/detail:
    - "success": absolute file path as string
    - "failed": error message
    - "empty": empty string (file skipped)
    - "write_error": error message
    """
    language_code = get_cldr_lang(language)
    try:
        po_file: bytes = weblate_api.get_bytes(
            WEBLATE_TRANSLATIONS_FILE_ENDPOINT.format(
                project=project, component=component, language=language_code,
            ),
            params=params,
        )
    except WeblateApiError as e:
        return language_code, "failed", str(e)
    if filter_empty and b'msgid ""\nmsgstr ""' in po_file and po_file.count(b'msgid "') <= 1:
        return language_code, "empty", ""
    file_path = Path(f"{project}-{component}-{language_code}.po")
    try:
        file_path.write_bytes(po_file)
    except OSError as e:
        return language_code, "write_error", str(e)
    return language_code, "success", str(file_path.resolve())
