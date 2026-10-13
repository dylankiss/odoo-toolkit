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
    WEBLATE_TRANSLATIONS_FILE_ENDPOINT,
    UploadConflicts,
    UploadFuzzy,
    UploadMethod,
    WeblateApi,
    WeblateApiError,
    WeblateTranslationsUploadResponse,
    format_upload_counts,
    print_failures,
    run_concurrently,
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
            help="The Weblate components to upload translations to. You can use glob patterns. Uploads all matching "
            "files if none are specified.",
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
            help='Specify how fuzzy translations should be handled. Either ignore them (`ignore`), mark them as '
            '"Needs editing" (`process`), or add them as a regular translation (`approve`).',
        ),
    ] = UploadFuzzy.PROCESS,
) -> None:
    """Upload specific PO files to Weblate.

    This command can be useful to bulk upload specific PO files to Weblate. The files need to be in the current working
    directory, named `<project>-<component>-<language>.po`, like the ones `otk wl download` saves.
    """
    print_command_title(":outbox_tray: Odoo Weblate PO Upload")

    try:
        weblate_api = WeblateApi()
    except NameError as e:
        print_error(str(e))
        raise Exit(1) from e

    # Weblate reads these from the form, next to the file.
    upload_data: dict[str, str] = {
        "method": method.value,
        "conflicts": conflicts.value,
        "fuzzy": "" if fuzzy == UploadFuzzy.IGNORE else fuzzy.value,
    }
    if author:
        upload_data["author_name"] = author
    if email:
        upload_data["author_email"] = email

    files = _find_files(project, [get_cldr_lang(lang) for lang in normalize_list_option(languages)])
    patterns = normalize_list_option(components)
    files = {key: path for key, path in files.items() if not patterns or filter_by_globs([key[0]], patterns)}
    if not files:
        print_error(f"No files named [b]{project}-<component>-<language>.po[/b] to upload in [u]{Path.cwd()}[/u].")
        raise Exit(1)

    with TransientProgress() as progress:
        task = progress.add_task("Uploading PO files", total=len(files))
        results = run_concurrently(
            sorted(files),
            lambda key: weblate_api.request(
                WeblateTranslationsUploadResponse,
                "POST",
                WEBLATE_TRANSLATIONS_FILE_ENDPOINT.format(project=project, component=key[0], language=key[1]),
                data=upload_data,
                files={"file": (files[key].name, files[key].read_bytes())},
            ),
            progress=progress,
            task_id=task,
        )

    counts = {"accepted": 0, "skipped": 0, "not_found": 0}
    failures: dict[str, str] = {}
    for (component, language), result in results.items():
        if isinstance(result, WeblateApiError):
            failures[f"{component}/{language}"] = result.summary
            continue
        for key in counts:
            counts[key] += result[key]

    result_text = format_upload_counts(counts["accepted"], counts["skipped"], counts["not_found"])
    (print_success if counts["accepted"] else print_warning)(f"{result_text[0].upper()}{result_text[1:]}.")
    if failures:
        print_error(f"Failed to upload {len(failures)} file(s):")
        print_failures(failures)
        raise Exit(1)


def _find_files(project: str, language_codes: list[str]) -> dict[tuple[str, str], Path]:
    """Find the PO files to upload in the current directory, by component and language."""
    files: dict[tuple[str, str], Path] = {}
    for language in language_codes:
        suffix = f"-{language}.po"
        for path in Path.cwd().glob(f"{project}-*{suffix}"):
            if component := path.name.removeprefix(f"{project}-").removesuffix(suffix):
                files[component, language] = path
    return files
