import re
from enum import Enum
from typing import Annotated, Any

from typer import Exit, Option, Typer

from odoo_toolkit.common import (
    TransientProgress,
    print_command_title,
    print_error,
    print_success,
    print_warning,
)

from .common import (
    WEBLATE_PROJECT_COMPONENTS_ENDPOINT,
    WEBLATE_TRANSLATIONS_FILE_ENDPOINT,
    WeblateApi,
    WeblateApiError,
    WeblateComponentResponse,
    WeblatePagedResponse,
    WeblateTranslationsUploadResponse,
    get_weblate_lang,
)


class UploadMethod(str, Enum):
    """Upload methods available to the translation upload endpoint."""

    TRANSLATE = "translate"
    APPROVE = "approve"
    SUGGEST = "suggest"


class UploadConflicts(str, Enum):
    """Conflict handling available to the translation upload endpoint."""

    IGNORE = "ignore"
    REPLACE_TRANSLATED = "replace-translated"
    REPLACE_REVIEWED = "replace-reviewed"


app = Typer()
po_clean_header_pattern = re.compile(b'^"(?:Language|Plural-Forms):.*\n', flags=re.MULTILINE)


@app.command()
def transfer(
    src_project: Annotated[str, Option("--src-project", "-p", help="The Weblate project to copy translations from.")],
    src_language: Annotated[str, Option("--src-language", "-l", help="The language code to copy translations from.")],
    dest_project: Annotated[
        str | None, Option("--dest-project", "-P", help="The Weblate project to copy translations to."),
    ] = None,
    dest_language: Annotated[
        str | None, Option("--dest-language", "-L", help="The language code to copy translations to."),
    ] = None,
    src_component: Annotated[
        str | None, Option("--src-component", "-c", help="The Weblate component to copy translations from."),
    ] = None,
    dest_component: Annotated[
        str | None, Option("--dest-component", "-C", help="The Weblate component to copy translations to."),
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
            "overwrite only non-reviewed translations (`replace-translated`), or overwrite even reviewed translations (`replace-reviewed`).",
        ),
    ] = UploadConflicts.REPLACE_TRANSLATED,
) -> None:
    """Transfer translations from one language, component, or project to another.

    This command allows you to copy existing translations of components in Weblate to either another language, another
    component, and/or another project.

    If you don't define a destination project, it will copy inside the same project.
    If you don't define a destination language, it will copy to the same language.
    If you don't define a source component, it will copy all components within the source project.
    If you don't define a target component, it will copy to the same components in the target project.
    """
    print_command_title(":memo: Odoo Weblate Transfer Translations")

    dest_project, dest_language, dest_component = _normalize_transfer_args(
        src_project, src_language, dest_project, dest_language, src_component, dest_component,
    )

    upload_data = {
        "conflicts": conflicts.value,
        "method": method.value,
        "fuzzy": "process",
    }

    try:
        weblate_api = WeblateApi()
    except NameError as e:
        print_error(str(e))
        raise Exit from e

    if src_component and src_language:
        try:
            response = _upload_translations(
                weblate_api, src_project, src_component, src_language,
                dest_project, dest_component, dest_language, upload_data,
            )
            _print_transfer_result(response)
        except WeblateApiError as e:
            print_error("Weblate API Error", str(e))
            raise Exit from e
        return

    try:
        total_dest_components = weblate_api.get(
            WeblatePagedResponse, WEBLATE_PROJECT_COMPONENTS_ENDPOINT.format(project=dest_project),
        ).get("count", 0)
    except WeblateApiError as e:
        print_error("Weblate API Error", str(e))
        raise Exit from e

    src_components = _get_project_components(weblate_api, src_project)
    dest_components = _get_project_components(weblate_api, dest_project) if dest_project != src_project else src_components

    accepted_count = 0
    skipped_count = 0
    not_found_count = 0

    with TransientProgress() as progress:
        progress_task = progress.add_task(
            f"Copying translations from {src_project} to {dest_project}", total=total_dest_components,
        )
        try:
            for component in dest_components:
                progress.advance(progress_task)
                if component not in src_components:
                    print_warning(f"Component '{component}' not found in source project '{src_project}'. Skipping.")
                    continue
                response = _upload_translations(
                    weblate_api, src_project, component, src_language,
                    dest_project, component, dest_language, upload_data,
                )
                accepted_count += response["accepted"]
                skipped_count += response["skipped"]
                not_found_count += response["not_found"]
        except WeblateApiError as e:
            print_error("Fetching components failed.", str(e))

    _print_transfer_result({"accepted": accepted_count, "skipped": skipped_count, "not_found": not_found_count})

def _get_project_components(api: WeblateApi, project: str) -> set[str]:
    """Fetch and return a set of component slugs for a given project."""
    try:
        return {
            c.get("slug")
            for c in api.get_generator(
                WeblateComponentResponse, WEBLATE_PROJECT_COMPONENTS_ENDPOINT.format(project=project),
            )
        }
    except WeblateApiError as e:
        print_error(f"Weblate API Error: Failed to fetch components for project '{project}'.", str(e))
        raise Exit from e

def _normalize_transfer_args(
    src_project: str,
    src_language: str,
    dest_project: str | None,
    dest_language: str | None,
    src_component: str | None,
    dest_component: str | None,
) -> tuple[str, str, str | None]:
    if not dest_project:
        dest_project = src_project
    if not dest_language:
        dest_language = src_language
    if src_component and not dest_component:
        dest_component = src_component
    return dest_project, dest_language, dest_component

def _upload_translations(
    api: WeblateApi,
    src_project: str,
    src_component: str,
    src_language: str,
    dest_project: str,
    dest_component: str | None,
    dest_language: str,
    upload_data: dict[str, Any],
) -> WeblateTranslationsUploadResponse:
    po_file: bytes = api.get_bytes(
        WEBLATE_TRANSLATIONS_FILE_ENDPOINT.format(
            project=src_project, component=src_component, language=get_weblate_lang(src_language),
        ),
    )
    return api.post(
        WeblateTranslationsUploadResponse,
        WEBLATE_TRANSLATIONS_FILE_ENDPOINT.format(
            project=dest_project, component=dest_component, language=get_weblate_lang(dest_language),
        ),
        data=upload_data,
        files={"file": ("upload.po", re.sub(po_clean_header_pattern, b"", po_file))},
    )

def _print_transfer_result(response: WeblateTranslationsUploadResponse) -> None:
    if response["accepted"]:
        print_success(
            f"Updated {response['accepted']} translations, skipped {response['skipped']} translations, "
            f"and didn't find {response['not_found']} source strings.",
        )
    else:
        print_warning(
            f"No translations updated. Skipped {response['skipped']} translations, "
            f"and didn't find {response['not_found']} source strings.",
        )
