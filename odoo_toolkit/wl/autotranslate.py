from enum import Enum
from typing import Annotated

from typer import Argument, Exit, Option, Typer, confirm

from odoo_toolkit.common import (
    EMPTY_LIST,
    Status,
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
    WEBLATE_AUTOTRANSLATE_ENDPOINT,
    WEBLATE_PROJECT_COMPONENTS_ENDPOINT,
    WeblateApi,
    WeblateApiError,
    WeblateComponentData,
)


class TranslationMode(str, Enum):
    """Translation modes available for the autotranslate endpoint."""

    SUGGEST = "suggest"
    TRANSLATE = "translate"
    FUZZY = "fuzzy"
    APPROVED = "approved"


class TranslationEngines(str, Enum):
    """Translation engines available for the autotranslate endpoint."""

    ALIBABA = "alibaba"
    APERTIUM_APY = "apertium-apy"
    AWS = "aws"
    AZURE_OPENAI = "azure-openai"
    BAIDU = "baidu"
    CYRTRANSLIT = "cyrtranslit"
    DEEPL = "deepl"
    GLOSBE = "glosbe"
    GOOGLE_TRANSLATE = "google-translate"
    GOOGLE_TRANSLATE_API_V3 = "google-translate-api-v3"
    LIBRETRANSLATE = "libretranslate"
    MICROSOFT_TRANSLATOR = "microsoft-translator"
    MODERNMT = "modernmt"
    MYMEMORY = "mymemory"
    NETEASE_SIGHT = "netease-sight"
    OPENAI = "openai"
    SAP_TRANSLATION_HUB = "sap-translation-hub"
    SYSTRAN = "systran"
    TMSERVER = "tmserver"
    WEBLATE = "weblate"
    WEBLATE_TRANSLATION_MEMORY = "weblate-translation-memory"
    YANDEX = "yandex"
    YANDEX_V2 = "yandex-v2"
    YOUDAO_ZHIYUN = "youdao-zhiyun"


DEFAULT_TRANSLATION_ENGINES = [TranslationEngines.WEBLATE]

app = Typer()


@app.command()
def autotranslate(
    project: Annotated[str, Argument(help="The Weblate project to autotranslate.")],
    languages: Annotated[list[str], Option("--language", "-l", help="The languages to autotranslate.")],
    components: Annotated[
        list[str],
        Option(
            "--component",
            "-c",
            help="The Weblate components to autotranslate. You can use glob patterns. Translates all components if "
            "none are specified.",
        ),
    ] = EMPTY_LIST,
    query: Annotated[
        str | None,
        Option(
            "--query",
            "-q",
            help="Specify which strings need to be translated by using a Weblate search string. Translates all strings "
            "if not specified.",
        ),
    ] = None,
    translation_mode: Annotated[
        TranslationMode,
        Option(
            "--mode",
            "-m",
            help="Specify the translation mode to use. Either add translations as suggestions (`suggest`), as "
            'translations (`translate`), as "Needing edit" (`fuzzy`), or as approved translations (`approved`).',
        ),
    ] = TranslationMode.SUGGEST,
    translation_engines: Annotated[
        list[TranslationEngines],
        Option(
            "--engine",
            "-e",
            help="Specify which translation engines to use. They need to be activated in your Weblate instance. You "
            "can provide multiple engines.",
        ),
    ] = DEFAULT_TRANSLATION_ENGINES,
    threshold: Annotated[
        int,
        Option(
            "--threshold",
            "-t",
            help="Specify the minimum match percentage threshold for using translation memory engines.",
        ),
    ] = 100,
) -> None:
    """Autotranslate translations for components in a Weblate project.

    This command allows you to autotranslate existing components in a Weblate project using various translation engines.
    You need to specify for which language(s) you want the translations autotranslated.

    You can provide specific components or none at all. In that case, all components will be autotranslated.
    You can also specify which type of strings you want to have translated in the project and how the translations
    should be added.
    Finally, you can choose which translation engines to use for the autotranslation.
    """
    print_command_title(":robot: Odoo Weblate Autotranslate")

    if not query:
        confirm(
            "No query parameter was specified. This will autotranslate [b]all[/b] strings, translated or not.\n"
            "Are you sure you want to continue?",
            abort=True,
        )

    # Support comma-separated values as well.
    languages = sorted(normalize_list_option(languages))
    components = normalize_list_option(components)

    try:
        weblate_api = WeblateApi()
    except NameError as e:
        print_error(str(e))
        raise Exit from e

    success_count, partial_count, failure_count = _process_components(
        weblate_api, project, components, languages, query, translation_mode, translation_engines, threshold,
    )

    total_processed = success_count + partial_count + failure_count

    if total_processed == 0:
        print_warning("No components were found or selected to autotranslate.")
        return

    if partial_count == 0 and failure_count == 0:
        print_success(f"Successfully autotranslated all {success_count} component(s)!")
    else:
        summary = [
            "Operation finished with errors:",
            f"  - [b]{success_count}[/b] component(s) [b]succeeded[/b] completely.",
            f"  - [b]{partial_count}[/b] component(s) succeeded for [b]some languages[/b] but failed for others.",
            f"  - [b]{failure_count}[/b] component(s) [b]failed[/b] completely.",
        ]
        print_error("\n".join(summary))


def _get_project_components(api: WeblateApi, project: str) -> set[str]:
    """Fetch and return a set of component slugs for a given project."""
    try:
        component_generator = api.get_generator(
            WeblateComponentData, WEBLATE_PROJECT_COMPONENTS_ENDPOINT.format(project=project),
        )
        return {c.get("slug", "") for c in component_generator}
    except WeblateApiError as e:
        print_error(f"Weblate API Error: Failed to fetch components for project '{project}'.", str(e))
        raise Exit from e


def _autotranslate_languages(
    api: WeblateApi,
    project: str,
    component: str,
    languages: list[str],
    query: str | None,
    translation_mode: TranslationMode,
    translation_engines: list[TranslationEngines],
    threshold: int,
) -> Status:
    """Autotranslate a specific component across multiple languages."""
    if not languages:
        return Status.SUCCESS

    success_count = 0
    failure_count = 0
    json: dict[str, str|int|list[str]] = {
        "mode": translation_mode.value,
        "auto_source": "mt",
        "engines": [engine.value for engine in translation_engines],
        "threshold": threshold,
    }
    if query:
        json["q"] = query
    for language in languages:
        try:
            api.post(
                str,
                WEBLATE_AUTOTRANSLATE_ENDPOINT.format(
                    project=project, component=component, language=get_cldr_lang(language),
                ),
                json=json,
            )
            success_count += 1
        except WeblateApiError as e:  # noqa: PERF203
            failure_count += 1
            print_error(f"An API call failed. Autotranslate for '{component}' and '{language}' failed.", str(e))

    if failure_count == 0:
        return Status.SUCCESS
    if success_count > 0:
        return Status.PARTIAL
    return Status.FAILURE


def _process_components(
    api: WeblateApi,
    project: str,
    components: list[str],
    languages: list[str],
    query: str | None,
    translation_mode: TranslationMode,
    translation_engines: list[TranslationEngines],
    threshold: int,
) -> tuple[int, int, int]:
    """Iterate through components, filter them, and call the autotranslate function.

    Returns a tuple of (success_count, partial_count, failure_count).
    """
    counts = {Status.SUCCESS: 0, Status.PARTIAL: 0, Status.FAILURE: 0}
    project_components = sorted(filter_by_globs(_get_project_components(api, project), components))

    with TransientProgress() as progress:
        progress_task = progress.add_task(
            f"Autotranslating [b]{project}[/b]", total=len(project_components),
        )
        for component in sorted(project_components):
            progress.advance(progress_task)

            if components and component not in components:
                continue

            progress.update(
                progress_task,
                description=f"Autotranslating [b]{component}[/b] in [b]{project}[/b]",
            )

            status = _autotranslate_languages(
                api, project, component, languages, query, translation_mode, translation_engines, threshold,
            )
            counts[status] += 1

    return counts[Status.SUCCESS], counts[Status.PARTIAL], counts[Status.FAILURE]
