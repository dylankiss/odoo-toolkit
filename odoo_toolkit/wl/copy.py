import itertools
import re
from enum import Enum
from fnmatch import fnmatch
from typing import Annotated, Any

from typer import Exit, Option, Typer

from odoo_toolkit.common import (
    EMPTY_LIST,
    TransientProgress,
    normalize_list_option,
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
    REPLACE_APPROVED = "replace-approved"


app = Typer()
po_clean_header_pattern = re.compile(b'^"(?:Language|Plural-Forms):.*\n', flags=re.MULTILINE)


@app.command()
def copy(
    src_project: Annotated[str, Option("--src-project", "-p", help="The Weblate project to copy translations from.")],
    src_languages: Annotated[list[str], Option("--src-language", "-l", help="The language codes to copy translations from.")],
    dest_project: Annotated[
        str | None, Option("--dest-project", "-P", help="The Weblate project to copy translations to."),
    ] = None,
    dest_language: Annotated[
        str | None, Option("--dest-language", "-L", help="The language code to copy translations to."),
    ] = None,
    src_components: Annotated[
        list[str], Option("--src-component", "-c", help="The Weblate components to copy translations from."),
    ] = EMPTY_LIST,
    dest_component: Annotated[
        str | None, Option("--dest-component", "-C", help="The Weblate component to copy translations to."),
    ] = None,
    author: Annotated[
        str | None,
        Option(
            "--author", "-a",
            help="The author name to use for the uploaded translations. If not set, the API key user will be used.",
        ),
    ] = None,
    email: Annotated[
        str | None,
        Option(
            "--email", "-e",
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
) -> None:
    """Copy translations between languages, components, and/or projects.

    This command allows you to copy existing translations of components in Weblate to either another language, another
    component, and/or another project. Technically it downloads the PO files from the source and uploads them to the
    destination.\n
    \n
    If you don't define a destination project, it will copy inside the same project.\n
    If you don't define a destination language, it will copy to the same language(s).\n
    If you don't define any source components, it will copy all components that are both in the source and destination
    projects.\n
    If you don't define a target component, it will copy to the same component(s) in the target project.
    """
    print_command_title(":memo: Odoo Weblate Copy Translations")

    src_languages_set = set(normalize_list_option(src_languages))
    src_components_set = set(normalize_list_option(src_components))

    # Validate the argument combinations.

    if len(src_languages_set) != 1 and dest_language:
        print_error("You need to specify exactly one source language when specifying a destination language.")
        raise Exit

    if dest_component:
        if any("*" in c or "?" in c or "[" in c for c in src_components_set):
            print_error("You cannot use wildcards in the source components when specifying a destination component.")
            raise Exit
        if len(src_components_set) != 1:
            print_error("You need to specify exactly one source component when specifying a destination component.")
            raise Exit

    if (
        (not dest_project or dest_project == src_project)
        and (not dest_language or dest_language in src_languages_set)
        and (not dest_component or dest_component in src_components_set)
    ):
        print_error("You cannot copy translations to the same language and component in the same project.")
        raise Exit

    upload_data = {
        "conflicts": conflicts.value,
        "method": method.value,
        "fuzzy": "process",
    }
    if author:
        upload_data["author"] = author
    if email:
        upload_data["email"] = email

    try:
        weblate_api = WeblateApi()
    except NameError as e:
        print_error(str(e))
        raise Exit from e

    # Map the components to process.

    components: dict[str, str] = {}
    if dest_component:
        components[src_components_set.pop()] = dest_component
    else:
        dest_components = _get_project_components(weblate_api, dest_project or src_project)
        if dest_project != src_project:
            remote_src_components = _get_project_components(weblate_api, src_project)
        else:
            remote_src_components = dest_components

        if not src_components_set:
            components.update({c: c for c in remote_src_components if c in dest_components})
        else:
            components.update({c: c for c in remote_src_components if any(fnmatch(c, pattern) for pattern in src_components_set) and c in dest_components})

    # Map the languages to process.

    languages: dict[str, str] = {}
    if dest_language:
        languages[src_languages_set.pop()] = dest_language
    else:
        languages.update({lang: lang for lang in src_languages_set})

    # Copy the translations.

    accepted_count = 0
    skipped_count = 0
    not_found_count = 0

    with TransientProgress() as progress:
        progress_task = progress.add_task(
            f"Copy translations from {src_project} to {dest_project or src_project}",
            total=len(components) * len(languages),
        )


        for (src_component, dest_component), (src_language, dest_language) in itertools.product(
            sorted(components.items(), key=lambda c: c[0]), sorted(languages.items(), key=lambda lang: lang[0]),
        ):
            progress.update(
                progress_task,
                description=f"Copying [b]{src_component}[/b] ([b]{src_language}[/b], [b]{src_project}[/b]) "
                    f"to [b]{dest_component}[/b] ([b]{dest_language}[/b], [b]{dest_project or src_project}[/b])",
            )
            progress.advance(progress_task)

            try:
                response = _upload_translations(
                    weblate_api, src_project, src_component, src_language,
                    dest_project or src_project, dest_component, dest_language, upload_data,
                )
            except WeblateApiError as e:
                print_error("Copying translations failed.", str(e))
                continue
            accepted_count += response["accepted"]
            skipped_count += response["skipped"]
            not_found_count += response["not_found"]

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
