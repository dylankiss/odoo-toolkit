import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Annotated

from typer import Exit, Option, Typer

from odoo_toolkit.common import (
    EMPTY_LIST,
    Status,
    TransientProgress,
    filter_by_globs,
    normalize_list_option,
    print_command_title,
    print_error,
    print_header,
    print_success,
    print_warning,
)
from odoo_toolkit.po.common import get_cldr_lang

from .autotranslate import TranslationMode
from .common import (
    WEBLATE_AUTOTRANSLATE_ENDPOINT,
    WEBLATE_MAX_WORKERS,
    WEBLATE_PROJECT_ENDPOINT,
    WEBLATE_REQUESTS_PER_SECOND,
    WeblateApi,
    WeblateApiError,
    WeblateAutotranslateResponse,
    WeblateComponentData,
    WeblateProjectDetailData,
    get_weblate_components,
    get_weblate_project_languages,
)

app = Typer()

HTTP_NOT_FOUND = 404
HTTP_FORBIDDEN = 403

DEFAULT_QUERY = "state:<translated"

# Weblate's autotranslate form requires `threshold` on every request, even though it only applies to machine
# translation engines and is therefore ignored when translating from another component.
UNUSED_MACHINERY_THRESHOLD = 100

# Weblate reports a failed autotranslation in the body of an HTTP 200 response instead of a failing status code.
AUTOTRANSLATE_FAILURE_PREFIX = "Automatic translation failed"

SHARED_TM_ERROR = (
    "Project [b]{src_project}[/b] does not contribute to the shared translation memory, so Weblate refuses to use it "
    "as a translation source for [b]{project}[/b].\n"
    "Enable [b]Contribute to shared translation memory[/b] in that project's settings, or pick a source project that "
    "already does."
)


@app.command()
def autotranslate_from(
    project: Annotated[
        str, Option("--project", "-p", help="The Weblate project whose components should be autotranslated."),
    ],
    src_project: Annotated[
        str,
        Option(
            "--src-project",
            "-s",
            help="The Weblate project to take the translations from. Its components are matched by slug against the "
            "components of the target project.",
        ),
    ],
    languages: Annotated[
        list[str],
        Option(
            "--language",
            "-l",
            help="The languages to autotranslate. Autotranslates every language present in the target project if "
            "none are specified.",
        ),
    ] = EMPTY_LIST,
    components: Annotated[
        list[str],
        Option(
            "--component",
            "-c",
            help="The Weblate components to autotranslate. You can use glob patterns. Considers all matching "
            "components if none are specified.",
        ),
    ] = EMPTY_LIST,
    excluded_components: Annotated[
        list[str],
        Option(
            "--exclude",
            "-x",
            help="The Weblate components to skip. You can use glob patterns.",
        ),
    ] = EMPTY_LIST,
    query: Annotated[
        str,
        Option(
            "--query",
            "-q",
            help="Specify which strings need to be translated by using a Weblate search string.",
        ),
    ] = DEFAULT_QUERY,
    translation_mode: Annotated[
        TranslationMode,
        Option(
            "--mode",
            "-m",
            help="Specify the translation mode to use. Either add translations as suggestions (`suggest`), as "
            'translations (`translate`), as "Needing edit" (`fuzzy`), or as approved translations (`approved`).',
        ),
    ] = TranslationMode.SUGGEST,
) -> None:
    """Autotranslate components in a Weblate project from equally named components in another project.

    This command looks up every component of the target project that also exists, under the same slug, in the source
    project, and autotranslates it using only the translations of that matching source component. No machine
    translation engines are involved.\n
    \n
    If you don't specify any languages, all languages present in the target project are used, restricted per component
    by that component's language regex. If you don't specify any components, all components that exist in both projects
    are used.\n
    \n
    Note that the source project needs to have "Contribute to shared translation memory" enabled, unless it is the
    same project as the target one.
    """
    print_command_title(":robot: Odoo Weblate Autotranslate From Project")

    # Support comma-separated values as well.
    languages = normalize_list_option(languages)
    components = normalize_list_option(components)
    excluded_components = normalize_list_option(excluded_components)

    try:
        weblate_api = WeblateApi(requests_per_second=WEBLATE_REQUESTS_PER_SECOND)
    except NameError as e:
        print_error(str(e))
        raise Exit from e

    _check_source_project_usable(weblate_api, project, src_project)

    # Fetch both component lists once. Everything else is derived from them without extra requests.
    try:
        dest_components = get_weblate_components(weblate_api, project)
        src_component_ids = {
            slug: component_id
            for c in get_weblate_components(weblate_api, src_project)
            if (slug := c.get("slug")) and (component_id := c.get("id"))
        }
    except WeblateApiError as e:
        print_error("Weblate API Error: Failed to fetch the components of the projects.", str(e))
        raise Exit from e

    matching_components = {
        slug: component
        for component in dest_components
        if (slug := component.get("slug", "")) in src_component_ids
    }
    selected_slugs = sorted(filter_by_globs(matching_components, components, excluded_components))

    if not selected_slugs:
        print_warning(f"No components of [b]{project}[/b] have a matching component in [b]{src_project}[/b].")
        return

    # Only fetch the project languages when the user didn't provide any.
    if languages:
        candidate_languages = sorted({get_cldr_lang(lang) for lang in languages})
    else:
        try:
            candidate_languages = get_weblate_project_languages(weblate_api, project)
        except WeblateApiError as e:
            print_error(f"Weblate API Error: Failed to fetch the languages of project '{project}'.", str(e))
            raise Exit from e

    # Restrict the languages per component using its language regex, to avoid requests that can only fail.
    translations = [
        (slug, language)
        for slug in selected_slugs
        for language in _component_languages(matching_components[slug], candidate_languages)
    ]

    if not translations:
        print_warning("No component and language combinations were found to autotranslate.")
        return

    print_header(f"Autotranslate [b]{project}[/b] from [b]{src_project}[/b]")

    json_payload: dict[str, str | int] = {
        "mode": translation_mode.value,
        "auto_source": "others",
        "q": query,
        "threshold": UNUSED_MACHINERY_THRESHOLD,
    }

    success_count, partial_count, failure_count = _process_translations(
        weblate_api, project, src_component_ids, translations, json_payload,
    )

    if partial_count == 0 and failure_count == 0:
        print_success(
            f"Successfully autotranslated all {success_count} component(s) from [b]{src_project}[/b]!",
        )
    else:
        summary = [
            "Operation finished with errors:",
            f"  - [b]{success_count}[/b] component(s) [b]succeeded[/b] completely.",
            f"  - [b]{partial_count}[/b] component(s) succeeded for [b]some languages[/b] but failed for others.",
            f"  - [b]{failure_count}[/b] component(s) [b]failed[/b] completely.",
        ]
        print_error("\n".join(summary))


def _check_source_project_usable(api: WeblateApi, project: str, src_project: str) -> None:
    """Abort unless the source project may be used as a translation source for the target project.

    Weblate only accepts a source component from another project when that project contributes to the shared
    translation memory. Checking it once up front beats discovering it through a failed request per component and
    language.

    :param api: The Weblate API to use.
    :param project: The project whose components will be autotranslated.
    :param src_project: The project the translations will be taken from.
    :raises Exit: If the project could not be fetched, or does not contribute to the shared translation memory.
    """
    if src_project == project:
        return
    try:
        src_project_data = api.get(WeblateProjectDetailData, WEBLATE_PROJECT_ENDPOINT.format(project=src_project))
    except WeblateApiError as e:
        print_error(f"Weblate API Error: Failed to fetch project '{src_project}'.", str(e))
        raise Exit from e
    if not src_project_data.get("contribute_shared_tm", True):
        print_error(SHARED_TM_ERROR.format(src_project=src_project, project=project))
        raise Exit


def _component_languages(component: WeblateComponentData, languages: list[str]) -> list[str]:
    """Return the languages of `languages` that the component's language regex allows."""
    language_regex = component.get("language_regex")
    if not language_regex:
        return languages
    try:
        pattern = re.compile(language_regex)
    except re.error:
        return languages
    return [language for language in languages if pattern.match(language)]


def _autotranslate_single(
    api: WeblateApi,
    project: str,
    component: str,
    language: str,
    *,
    src_component_id: int,
    json_payload: dict[str, str | int],
) -> tuple[bool, str | None]:
    """Autotranslate one component/language pair.

    :return: A tuple of whether the translation exists, and the error message if the call failed.
    """
    try:
        response = api.post(
            WeblateAutotranslateResponse,
            WEBLATE_AUTOTRANSLATE_ENDPOINT.format(project=project, component=component, language=language),
            json={**json_payload, "component": src_component_id},
        )
    except WeblateApiError as e:
        if e.status_code == HTTP_NOT_FOUND:
            return False, None
        if e.status_code == HTTP_FORBIDDEN:
            return True, f"{e}\n\nThe source component is not allowed as a translation source for this translation."
        return True, str(e)
    # Weblate swallows some failures and reports them in the body of an HTTP 200 response.
    details = response.get("details", "")
    if details.startswith(AUTOTRANSLATE_FAILURE_PREFIX):
        return True, details
    return True, None


def _process_translations(
    api: WeblateApi,
    project: str,
    src_component_ids: dict[str, int],
    translations: list[tuple[str, str]],
    json_payload: dict[str, str | int],
) -> tuple[int, int, int]:
    """Autotranslate all component/language pairs and return (success_count, partial_count, failure_count)."""
    component_results: dict[str, list[bool]] = {component: [] for component, _ in translations}
    failures: list[tuple[str, str, str]] = []
    missing: list[tuple[str, str]] = []

    with TransientProgress() as progress:
        progress_task = progress.add_task(f"Autotranslating [b]{project}[/b]", total=len(translations))
        with ThreadPoolExecutor(max_workers=WEBLATE_MAX_WORKERS) as executor:
            futures = {
                executor.submit(
                    _autotranslate_single,
                    api,
                    project,
                    component,
                    language,
                    src_component_id=src_component_ids[component],
                    json_payload=json_payload,
                ): (component, language)
                for component, language in translations
            }
            for future in as_completed(futures):
                component, language = futures[future]
                progress.advance(progress_task)
                exists, error = future.result()
                if not exists:
                    missing.append((component, language))
                    continue
                if error:
                    failures.append((component, language, error))
                component_results[component].append(error is None)

    if missing:
        pairs_str = ", ".join(f"{c}/{lang}" for c, lang in sorted(missing))
        print_warning(f"{len(missing)} translation(s) don't exist in '{project}' (skipped): {pairs_str}")

    if failures:
        pairs_str = ", ".join(f"{c}/{lang}" for c, lang, _ in sorted(failures))
        # Every failed pair usually shares one root cause, so show each distinct error once instead of repeating it.
        distinct_errors = sorted({error for *_, error in failures})
        print_error(
            f"{len(failures)} autotranslation(s) failed: {pairs_str}",
            "\n\n".join(distinct_errors),
        )

    counts = {Status.SUCCESS: 0, Status.PARTIAL: 0, Status.FAILURE: 0}
    for results in component_results.values():
        if not results:
            # Every language of this component was skipped, so there is nothing to report.
            continue
        if all(results):
            counts[Status.SUCCESS] += 1
        elif any(results):
            counts[Status.PARTIAL] += 1
        else:
            counts[Status.FAILURE] += 1

    return counts[Status.SUCCESS], counts[Status.PARTIAL], counts[Status.FAILURE]
