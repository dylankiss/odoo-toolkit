from fnmatch import fnmatch
from typing import Annotated

from typer import Exit, Option, Typer

from odoo_toolkit.common import (
    EMPTY_LIST,
    TransientProgress,
    normalize_list_option,
    print_command_title,
    print_error,
    print_header,
    print_success,
    print_warning,
)
from odoo_toolkit.po.common import get_cldr_lang

from .authors import AuthorOption, EmailOption, resolve_author
from .common import (
    NOT_FOUND,
    MethodOption,
    OverwriteOption,
    UploadConflicts,
    UploadFuzzy,
    UploadMethod,
    WeblateApi,
    WeblateApiError,
    clean_po_header,
    fetch_language_files,
    format_upload_counts,
    get_weblate_api,
    get_weblate_project_component_slugs,
    po_has_translations,
    print_failures,
    run_concurrently,
    upload_translations,
)

app = Typer()

GLOB_CHARACTERS = ("*", "?", "[")


@app.command()
def copy(  # noqa: C901, PLR0912, PLR0915
    src_project: Annotated[str, Option("--src-project", "-p", help="The Weblate project to copy translations from.")],
    src_languages: Annotated[
        list[str], Option("--src-language", "-l", help="The language codes to copy translations from."),
    ],
    dest_projects: Annotated[
        list[str],
        Option("--dest-project", "-P", help="The Weblate projects to copy translations to."),
    ] = EMPTY_LIST,
    dest_language: Annotated[
        str | None,
        Option("--dest-language", "-L", help="The language code to copy translations to."),
    ] = None,
    src_components: Annotated[
        list[str],
        Option("--src-component", "-c", help="The Weblate components to copy translations from."),
    ] = EMPTY_LIST,
    dest_component: Annotated[
        str | None,
        Option("--dest-component", "-C", help="The Weblate component to copy translations to."),
    ] = None,
    query: Annotated[
        str | None,
        Option(
            "--query",
            "-q",
            help="Specify which strings need to be copied over by using a Weblate search string. Copies all translated "
            "strings if not specified.",
        ),
    ] = None,
    author: AuthorOption = None,
    email: EmailOption = None,
    method: MethodOption = UploadMethod.TRANSLATE,
    conflicts: OverwriteOption = UploadConflicts.IGNORE,
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
    dest_projects = sorted(set(normalize_list_option(dest_projects)))

    # Validate the argument combinations.

    if len(src_languages_set) != 1 and dest_language:
        print_error("You need to specify exactly one source language when specifying a destination language.")
        raise Exit(1)

    if dest_component:
        if any(any(char in c for char in GLOB_CHARACTERS) for c in src_components_set):
            print_error("You cannot use wildcards in the source components when specifying a destination component.")
            raise Exit(1)
        if len(src_components_set) != 1:
            print_error("You need to specify exactly one source component when specifying a destination component.")
            raise Exit(1)

    if (
        (not dest_projects or src_project in dest_projects)
        and (not dest_language or dest_language in src_languages_set)
        and (not dest_component or dest_component in src_components_set)
    ):
        print_error("You cannot copy translations to the same language and component in the same project.")
        raise Exit(1)

    upload_data = {
        "conflicts": conflicts.value,
        "method": method.value,
        "fuzzy": UploadFuzzy.PROCESS.value,
    }
    upload_data |= resolve_author(author, email)

    weblate_api = get_weblate_api()

    if not dest_projects:
        dest_projects = [src_project]

    # Map the components to process, per destination project.

    try:
        components_per_dest = _map_components(
            weblate_api,
            src_project,
            dest_projects,
            src_components_set=src_components_set,
            dest_component=dest_component,
        )
    except WeblateApiError as e:
        print_error(f"Fetching the components failed: {e.summary}", str(e))
        raise Exit(1) from e

    if not any(components_per_dest.values()):
        print_warning("No components to copy. None of the source components exist in the destination project(s).")
        raise Exit(1)

    # Only the source components that at least one destination project can receive are worth downloading.
    needed_src_components = {
        comp_src for components in components_per_dest.values() for comp_src in components
    }

    # Map the languages to process.

    languages: dict[str, str] = {}
    if dest_language:
        languages[src_languages_set.pop()] = dest_language
    else:
        languages.update({lang: lang for lang in src_languages_set})

    # Copy the translations, one source language at a time. Fetching per language keeps only that language's files in
    # memory while still downloading every source file exactly once for all destination projects.

    totals: dict[str, dict[str, int]] = {
        dest_project: {"accepted": 0, "skipped": 0, "not_found": 0} for dest_project in dest_projects
    }
    not_found_pairs: dict[str, list[tuple[str, str]]] = {dest_project: [] for dest_project in dest_projects}
    # The failures per (component, language), with their reason.
    fetch_failures: dict[tuple[str, str], str] = {}
    upload_failures: dict[tuple[str, str, str], str] = {}
    untranslated_skipped = 0
    missing_skipped = 0
    copied_anything = False

    for lang_src, lang_dest in sorted(languages.items()):
        lang_code = get_cldr_lang(lang_src)
        print_header(f"Copy [b]{lang_src}[/b] from project [b]{src_project}[/b]")

        with TransientProgress() as progress:
            fetch_task = progress.add_task(
                f"Fetching {lang_src} source files...", total=len(needed_src_components),
            )
            fetched = fetch_language_files(
                weblate_api,
                src_project,
                lang_code,
                needed_src_components,
                query=query,
                progress=progress,
                task_id=fetch_task,
            )
        fetch_failures |= {(component, lang_code): reason for component, reason in fetched.failures.items()}
        # Like localization modules, which only have the languages of their country.
        missing_skipped += len(fetched.missing)
        # The language-specific header lines must not be carried over to another language.
        source_files = {component: clean_po_header(po_file) for component, po_file in fetched.files.items()}

        # Uploading a file without a single translation only burns a request.
        untranslated = {component for component, po_file in source_files.items() if not po_has_translations(po_file)}
        untranslated_skipped += len(untranslated)
        source_files = {c: po for c, po in source_files.items() if c not in untranslated}

        if not source_files:
            continue
        copied_anything = True

        for dest_project in dest_projects:
            uploads: list[tuple[bytes, str]] = [
                (source_files[comp_src], comp_dest)
                for comp_src, comp_dest in sorted(components_per_dest[dest_project].items())
                if comp_src in source_files
            ]
            if not uploads:
                continue

            with TransientProgress() as progress:
                progress_task = progress.add_task(
                    f"Copying {lang_src} to {dest_project}", total=len(uploads),
                )
                results = run_concurrently(
                    uploads,
                    lambda upload, dest_project=dest_project, lang_dest=lang_dest: upload_translations(
                        weblate_api, dest_project, upload[1], get_cldr_lang(lang_dest), upload[0], upload_data,
                    ),
                    progress=progress,
                    task_id=progress_task,
                )
            for (_, comp_dest), result in results.items():
                if isinstance(result, WeblateApiError):
                    if result.status_code == NOT_FOUND:
                        not_found_pairs[dest_project].append((comp_dest, get_cldr_lang(lang_dest)))
                    else:
                        upload_failures[dest_project, comp_dest, get_cldr_lang(lang_dest)] = result.summary
                    continue
                for key in ("accepted", "skipped", "not_found"):
                    totals[dest_project][key] += result[key]

    # Report the results.

    if fetch_failures:
        print_error(f"Failed to fetch {len(fetch_failures)} source file(s) from [b]{src_project}[/b]:")
        print_failures({f"{component}/{lang}": reason for (component, lang), reason in fetch_failures.items()})
    if upload_failures:
        print_error(f"Failed to upload {len(upload_failures)} file(s):")
        print_failures({
            f"{project}: {component}/{lang}": reason for (project, component, lang), reason in upload_failures.items()
        })

    skipped = [
        f"{missing_skipped} without the language in [b]{src_project}[/b]" if missing_skipped else "",
        f"{untranslated_skipped} without any translation" if untranslated_skipped else "",
    ]
    if skipped := [reason for reason in skipped if reason]:
        print_warning(f"Skipped the source files of {' and '.join(skipped)}.")

    if not copied_anything:
        if fetch_failures:
            print_error("No source files could be fetched.")
            raise Exit(1)
        print_warning("Nothing to copy.")
        return

    for dest_project in dest_projects:
        if pairs := not_found_pairs[dest_project]:
            pairs_str = ", ".join(f"{c}/{lang}" for c, lang in sorted(pairs))
            print_warning(f"{len(pairs)} combination(s) not found in '{dest_project}' (skipped): {pairs_str}")

        counts = totals[dest_project]
        result = format_upload_counts(counts["accepted"], counts["skipped"], counts["not_found"])
        (print_success if counts["accepted"] else print_warning)(f"[b]{dest_project}[/b]: {result}.")

    if fetch_failures or upload_failures:
        raise Exit(1)


def _map_components(
    api: WeblateApi,
    src_project: str,
    dest_projects: list[str],
    *,
    src_components_set: set[str],
    dest_component: str | None,
) -> dict[str, dict[str, str]]:
    """Map every destination project to the source/destination component pairs it should receive.

    Components that don't exist in a destination project are left out, so they cost neither a download nor an upload
    that could only fail.

    :param api: The Weblate API to use.
    :param src_project: The project to copy translations from.
    :param dest_projects: The projects to copy translations to.
    :param src_components_set: The source component names or glob patterns, or an empty set for all of them.
    :param dest_component: The single destination component to copy to, if any.
    :raises WeblateApiError: If the components of a project could not be fetched.
    :return: A mapping of destination project to a mapping of source component to destination component.
    """
    if dest_component:
        # An explicit destination was named, so there is nothing to look up or filter.
        return {dest_project: {next(iter(src_components_set)): dest_component} for dest_project in dest_projects}

    slugs: dict[str, set[str]] = {}

    def get_slugs(project: str) -> set[str]:
        # The source project can be a destination too.
        if project not in slugs:
            slugs[project] = get_weblate_project_component_slugs(api, project)
        return slugs[project]

    has_globs = any(any(char in c for char in GLOB_CHARACTERS) for c in src_components_set)
    if src_components_set and not has_globs:
        # The source components are all literal, so the source project's component list adds nothing.
        src_component_slugs = set(src_components_set)
    else:
        src_component_slugs = {
            c
            for c in get_slugs(src_project)
            if not src_components_set or any(fnmatch(c, pattern) for pattern in src_components_set)
        }

    return {dest_project: {c: c for c in src_component_slugs & get_slugs(dest_project)} for dest_project in dest_projects}
