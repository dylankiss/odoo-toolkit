import itertools
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
def upload(
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

    with TransientProgress() as progress:
        progress_task = progress.add_task(
            "Uploading PO files...",
            total=len(components) * len(languages_set),
        )
        upload_table = Table(box=None, pad_edge=False, show_header=False)

        params = {
            "method": method.value,
            "conflicts": conflicts.value,
            "fuzzy": "" if fuzzy.value == UploadFuzzy.IGNORE else fuzzy.value,
        }
        if author:
            params["author"] = author
        if email:
            params["email"] = email

        for component, language in itertools.product(sorted(components), sorted(languages_set)):
            language_code = get_cldr_lang(language)
            language_name = get_language_name(language_code)
            file_path = Path(f"{project}-{component}-{language_code}.po")

            if not file_path.is_file():
                missing_po_files.add(file_path)
                progress.advance(progress_task)
                continue

            po_bytes = None
            if fuzzy == UploadFuzzy.APPROVE:
                # Pre-process the PO file to remove fuzzy flags (because the API won't approve fuzzy translations).
                try:
                    po = pofile(file_path)
                    for entry in po.fuzzy_entries():
                        entry.flags.remove("fuzzy")
                    po_bytes = str(po).encode("utf-8")
                except (OSError, ValueError) as e:
                    failed_uploads += 1
                    upload_table.add_row(
                        f"{project}/{component} ({language_name})",
                        get_error_log_panel(str(e), "Reading PO file failed!"),
                    )
                    progress.advance(progress_task)
                    continue

            progress.update(progress_task, description=f"Uploading [b]{language_code}.po[/b] for {project}/{component}...")
            try:
                response = weblate_api.post(
                    WeblateTranslationsUploadResponse,
                    WEBLATE_TRANSLATIONS_FILE_ENDPOINT.format(
                        project=project,
                        component=component,
                        language=language_code,
                    ),
                    params=params,
                    files={
                        "file": (f"{project}-{component}-{language_code}.po", po_bytes)
                            if po_bytes is not None
                            else file_path.open("rb"),
                    },
                )
            except (WeblateApiError, OSError) as e:
                failed_uploads += 1
                upload_table.add_row(
                    f"{project}/{component} ({language_name})",
                    get_error_log_panel(str(e), "Uploading failed!"),
                )
                progress.advance(progress_task)
                continue
            accepted_count += response["accepted"]
            skipped_count += response["skipped"]
            not_found_count += response["not_found"]

            upload_table.add_row(
                f"{project}/{component} ({language_name})",
                f"Accepted: [b]{response['accepted']}[/b], Skipped: [b]{response['skipped']}[/b], Not Found: [b]{response['not_found']}[/b] :white_check_mark:",
            )
            progress.advance(progress_task)

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
