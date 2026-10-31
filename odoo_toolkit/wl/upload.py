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

from .authors import AuthorOption, EmailOption, resolve_author
from .common import (
    MethodOption,
    OverwriteOption,
    UploadConflicts,
    UploadFuzzy,
    UploadMethod,
    WeblateApi,
    WeblateApiError,
    format_upload_counts,
    get_weblate_api,
    get_weblate_project_component_slugs,
    print_failures,
    run_concurrently,
    upload_translations,
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
    author: AuthorOption = None,
    email: EmailOption = None,
    method: MethodOption = UploadMethod.TRANSLATE,
    conflicts: OverwriteOption = UploadConflicts.IGNORE,
    fuzzy: Annotated[
        UploadFuzzy,
        Option(
            "--fuzzy",
            "-f",
            help='Ignore fuzzy translations (`ignore`), mark them as "Needs editing" (`process`), or add them as '
            "regular translations (`approve`).",
        ),
    ] = UploadFuzzy.PROCESS,
) -> None:
    """Upload specific PO files to Weblate.

    This command can be useful to bulk upload specific PO files to Weblate. The files need to be in the current working
    directory, named `<project>-<component>-<language>.po`, like the ones `otk wl download` saves.
    """
    print_command_title(":outbox_tray: Odoo Weblate PO Upload")

    weblate_api = get_weblate_api()

    # Weblate reads these from the form, next to the file.
    upload_data: dict[str, str] = {
        "method": method.value,
        "conflicts": conflicts.value,
        "fuzzy": "" if fuzzy == UploadFuzzy.IGNORE else fuzzy.value,
    }
    upload_data |= resolve_author(author, email)

    files = _select_files(
        weblate_api,
        project,
        [get_cldr_lang(lang) for lang in normalize_list_option(languages)],
        normalize_list_option(components),
    )

    failures: dict[str, str] = {}
    contents: dict[tuple[str, str], bytes] = {}
    for key, path in files.items():
        try:
            contents[key] = path.read_bytes()
        except OSError as e:
            failures[f"{key[0]}/{key[1]}"] = f"could not be read: {e.strerror}"

    with TransientProgress() as progress:
        task = progress.add_task("Uploading PO files", total=len(contents))
        results = run_concurrently(
            sorted(contents),
            lambda key: upload_translations(weblate_api, project, key[0], key[1], contents[key], upload_data),
            progress=progress,
            task_id=task,
        )

    counts = {"accepted": 0, "skipped": 0, "not_found": 0}
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


def _select_files(
    api: WeblateApi, project: str, language_codes: list[str], patterns: list[str],
) -> dict[tuple[str, str], Path]:
    """Select the PO files to upload, by component and language.

    :param api: The Weblate API to use.
    :param project: The project slug.
    :param language_codes: The Weblate language codes to upload.
    :param patterns: The glob patterns of the components to upload, or none for all of them.
    :raises Exit: If there are no files, or the components of the project can't be fetched.
    :return: The files to upload.
    """
    files = _find_files(project, language_codes)
    if patterns:
        matching = set(filter_by_globs([component for component, _ in files], patterns))
        files = {key: path for key, path in files.items() if key[0] in matching}
    if not files:
        print_error(f"No files named [b]{project}-<component>-<language>.po[/b] to upload in [u]{Path.cwd()}[/u].")
        raise Exit(1)

    # The name only tells where the project ends if the component exists: `odoo-18-l10n-…` is also `odoo-18-…`.
    try:
        slugs = get_weblate_project_component_slugs(api, project)
    except WeblateApiError as e:
        print_error(f"Fetching the components of [b]{project}[/b] failed: {e.summary}", str(e))
        raise Exit(1) from e
    if unknown := sorted(path.name for (component, _), path in files.items() if component not in slugs):
        print_warning(f"Skipped files of components that aren't in [b]{project}[/b]: {', '.join(unknown)}")
    files = {key: path for key, path in files.items() if key[0] in slugs}
    if not files:
        print_error(f"No files to upload to [b]{project}[/b] have a component in the project.")
        raise Exit(1)
    return files


def _find_files(project: str, language_codes: list[str]) -> dict[tuple[str, str], Path]:
    """Find the PO files to upload in the current directory, by component and language."""
    files: dict[tuple[str, str], Path] = {}
    for language in language_codes:
        suffix = f"-{language}.po"
        for path in Path.cwd().glob(f"{project}-*{suffix}"):
            if component := path.name.removeprefix(f"{project}-").removesuffix(suffix):
                files[component, language] = path
    return files
