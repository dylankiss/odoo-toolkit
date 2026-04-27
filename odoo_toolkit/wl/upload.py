import itertools
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Annotated

from polib import pofile
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
    UploadConflicts,
    UploadFuzzy,
    UploadMethod,
    WeblateApi,
    WeblateApiError,
    WeblateTranslationsUploadResponse,
    get_weblate_project_component_slugs,
)

app = Typer()


@app.command()
def upload(  # noqa: C901, PLR0912, PLR0915
    project: Annotated[str, Option("--project", "-p", help="The Weblate project to upload translations to.")],
    languages: Annotated[
        list[str],
        Option(
            "--language",
            "-l",
            help="The languages to upload translations for. At least one language must be specified.",
        ),
    ],
    components: Annotated[
        list[str],
        Option(
            "--component",
            "-c",
            help="The Weblate components to upload translations to. You can use glob patterns. Uploads to all components if none are specified.",
        ),
    ] = EMPTY_LIST,
    author: Annotated[
        str | None,
        Option(
            "--author",
            "-a",
            help="The author name to use for the uploaded translations. If not set, the API key user will be used.",
        ),
    ] = None,
    email: Annotated[
        str | None,
        Option(
            "--email",
            "-e",
            help="The author email to use for the uploaded translations. If not set, the API key user will be used.",
        ),
    ] = None,
    method: Annotated[
        UploadMethod,
        Option(
            "--method",
            "-m",
            help="Specify what the upload should do. Either upload the translations as reviewed strings (`approve`), "
            "non-reviewed strings (`translate`), or suggestions (`suggest`).",
        ),
    ] = UploadMethod.TRANSLATE,
    conflicts: Annotated[
        UploadConflicts,
        Option(
            "--overwrite",
            "-o",
            help="Specify what the upload should do. Either don't overwrite existing translations (`ignore`), "
            "overwrite only non-reviewed translations (`replace-translated`), or overwrite even approved translations (`replace-approved`).",
        ),
    ] = UploadConflicts.IGNORE,
    fuzzy: Annotated[
        UploadFuzzy,
        Option(
            "--fuzzy",
            "-f",
            help='Specify how fuzzy translations should be handled. Either ignore them (`ignore`), mark them as'
            '"Needs editing" (`process`), or add them as a regular translation (`approve`).',
        ),
    ] = UploadFuzzy.PROCESS,
) -> None:
    """Upload specific PO files to Weblate.

    This command can be useful to bulk upload specific PO files to Weblate. The files will be need to be saved in the
    current working directory with the name pattern `<project>-<component>-<language>.po`.
    """
    print_command_title(":outbox_tray: Odoo Weblate PO Upload")

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

    accepted_count = 0
    skipped_count = 0
    not_found_count = 0
    failed_uploads = 0
    missing_po_files: set[Path] = set()

    params: dict[str, str] = {
        "method": method.value,
        "conflicts": conflicts.value,
        "fuzzy": "" if fuzzy.value == UploadFuzzy.IGNORE else fuzzy.value,
    }
    if author:
        params["author"] = author
    if email:
        params["email"] = email

    with TransientProgress() as progress:
        progress_task = progress.add_task(
            "Uploading PO files...",
            total=len(components) * len(languages_set),
        )
        upload_table = Table(box=None, pad_edge=False, show_header=False)

        results: list[tuple[str, str, str, str]] = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(
                    _upload_translation, weblate_api, project, component, language, params, fuzzy,
                ): (component, language)
                for component, language in itertools.product(sorted(components), sorted(languages_set))
            }
            for future in as_completed(futures):
                component, _ = futures[future]
                results.append((component, *future.result()))
                progress.advance(progress_task)

        for component, language_code, status, detail in sorted(results, key=lambda r: (r[0], r[1])):
            language_name = get_language_name(language_code)
            if status == "missing":
                missing_po_files.add(Path(detail))
            elif status == "read_failed":
                failed_uploads += 1
                upload_table.add_row(
                    f"{project}/{component} ({language_name})",
                    get_error_log_panel(detail, "Reading PO file failed!"),
                )
            elif status == "upload_failed":
                failed_uploads += 1
                upload_table.add_row(
                    f"{project}/{component} ({language_name})",
                    get_error_log_panel(detail, "Uploading failed!"),
                )
            elif status == "success":
                accepted, skipped, not_found = (int(x) for x in detail.split(":"))
                accepted_count += accepted
                skipped_count += skipped
                not_found_count += not_found
                upload_table.add_row(
                    f"{project}/{component} ({language_name})",
                    f"Accepted: [b]{accepted}[/b], Skipped: [b]{skipped}[/b], Not Found: [b]{not_found}[/b] :white_check_mark:",
                )

    print(upload_table, "")

    if missing_po_files:
        print_warning(
            f"Missing {len(missing_po_files)} PO files that were not found in the current directory:",
        )
        for missing_file in sorted(missing_po_files):
            print_warning(f" - {missing_file}")
        print()
    if failed_uploads:
        print_error(f"Failed to upload {failed_uploads} translations due to errors.")
    if accepted_count:
        print_success(
            f"Updated {accepted_count} translations, skipped {skipped_count} translations, "
            f"and didn't find {not_found_count} source strings.",
        )
    else:
        print_warning(
            f"No translations updated. Skipped {skipped_count} translations, "
            f"and didn't find {not_found_count} source strings.",
        )


def _upload_translation(
    weblate_api: WeblateApi,
    project: str,
    component: str,
    language: str,
    params: dict[str, str],
    fuzzy: UploadFuzzy,
) -> tuple[str, str, str]:
    """Upload a single translation file. Returns (language_code, status, detail).

    status/detail:
    - "missing": file path as string (file not found on disk)
    - "read_failed": error message (failed to read/process PO file)
    - "upload_failed": error message (API call failed)
    - "success": "accepted:skipped:not_found" integer counts
    """
    language_code = get_cldr_lang(language)
    file_path = Path(f"{project}-{component}-{language_code}.po")

    if not file_path.is_file():
        return language_code, "missing", str(file_path)

    try:
        if fuzzy == UploadFuzzy.APPROVE:
            po = pofile(file_path)
            for entry in po.fuzzy_entries():
                entry.flags.remove("fuzzy")
            content = str(po).encode("utf-8")
        else:
            content = file_path.read_bytes()
    except (OSError, ValueError) as e:
        return language_code, "read_failed", str(e)

    try:
        response = weblate_api.post(
            WeblateTranslationsUploadResponse,
            WEBLATE_TRANSLATIONS_FILE_ENDPOINT.format(
                project=project, component=component, language=language_code,
            ),
            params=params,
            files={"file": (f"{project}-{component}-{language_code}.po", content)},
        )
    except (WeblateApiError, OSError) as e:
        return language_code, "upload_failed", str(e)

    return language_code, "success", f"{response['accepted']}:{response['skipped']}:{response['not_found']}"
