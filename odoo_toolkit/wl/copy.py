import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from fnmatch import fnmatch
from typing import Annotated, Any

from rich.progress import TaskID
from typer import Exit, Option, Typer

from odoo_toolkit.common import (
    EMPTY_LIST,
    TransientProgress,
    normalize_list_option,
    print,
    print_command_title,
    print_error,
    print_header,
    print_success,
    print_warning,
)
from odoo_toolkit.po.common import get_cldr_lang

from .common import (
    NOT_FOUND,
    WEBLATE_MAX_WORKERS,
    WEBLATE_PROJECT_LANGUAGE_FILE_ENDPOINT,
    WEBLATE_TRANSLATIONS_FILE_ENDPOINT,
    UploadConflicts,
    UploadMethod,
    WeblateApi,
    WeblateApiError,
    WeblateTranslationsUploadResponse,
    clean_po_header,
    extract_po_files_from_zip,
    get_weblate_project_component_slugs,
    po_has_translations,
)

app = Typer()

GLOB_CHARACTERS = ("*", "?", "[")
# Below this many components, fetching each file individually costs few enough requests that it beats downloading the
# whole project archive, whose size is driven by the project rather than by the selection.
BULK_ZIP_MIN_COMPONENTS = 10


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
    author_name: Annotated[
        str | None,
        Option(
            "--author-name",
            "-n",
            help="The author name to use for the uploaded translations. If not set, the API key user will be used.",
        ),
    ] = None,
    author_email: Annotated[
        str | None,
        Option(
            "--author-email",
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
        "fuzzy": "process",
    }
    if author_name:
        upload_data["author_name"] = author_name
    if author_email:
        upload_data["author_email"] = author_email

    try:
        weblate_api = WeblateApi()
    except NameError as e:
        print_error(str(e))
        raise Exit(1) from e

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
        return

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
    copied_anything = False

    for lang_src, lang_dest in sorted(languages.items()):
        lang_code = get_cldr_lang(lang_src)
        print_header(f"Copy [b]{lang_src}[/b] from project [b]{src_project}[/b]")

        with TransientProgress() as progress:
            fetch_task = progress.add_task(
                f"Fetching {lang_src} source files...", total=len(needed_src_components),
            )
            source_files, failed = fetch_language_sources(
                weblate_api,
                src_project,
                lang_code,
                needed_src_components,
                query=query,
                progress=progress,
                task_id=fetch_task,
            )
        fetch_failures |= {(component, lang_code): reason for component, reason in failed.items()}

        # Uploading a file without a single translation only burns a request.
        untranslated = {component for component, po_file in source_files.items() if not po_has_translations(po_file)}
        untranslated_skipped += len(untranslated)
        source_files = {c: po for c, po in source_files.items() if c not in untranslated}

        if not source_files:
            continue
        copied_anything = True

        for dest_project in dest_projects:
            uploads = [
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
                with ThreadPoolExecutor(max_workers=WEBLATE_MAX_WORKERS) as executor:
                    futures = {
                        executor.submit(
                            _upload_translations,
                            weblate_api,
                            po_file,
                            dest_project,
                            comp_dest,
                            lang_dest,
                            upload_data=upload_data,
                        ): comp_dest
                        for po_file, comp_dest in uploads
                    }
                    for future in as_completed(futures):
                        progress.advance(progress_task)
                        comp_dest = futures[future]
                        try:
                            response = future.result()
                        except WeblateApiError as e:
                            if e.status_code == NOT_FOUND:
                                not_found_pairs[dest_project].append((comp_dest, get_cldr_lang(lang_dest)))
                            else:
                                upload_failures[dest_project, comp_dest, get_cldr_lang(lang_dest)] = e.summary
                            continue
                        totals[dest_project]["accepted"] += response["accepted"]
                        totals[dest_project]["skipped"] += response["skipped"]
                        totals[dest_project]["not_found"] += response["not_found"]

    # Report the results.

    if fetch_failures:
        print_error(f"Failed to fetch {len(fetch_failures)} source file(s) from [b]{src_project}[/b]:")
        _print_failures({f"{component}/{lang}": reason for (component, lang), reason in fetch_failures.items()})
    if upload_failures:
        print_error(f"Failed to upload {len(upload_failures)} file(s):")
        _print_failures({
            f"{project}: {component}/{lang}": reason for (project, component, lang), reason in upload_failures.items()
        })

    if untranslated_skipped:
        print_warning(f"Skipped {untranslated_skipped} source file(s) without any translation.")

    if not copied_anything:
        if untranslated_skipped:
            print_warning("Nothing to copy. Every source file that was fetched is untranslated.")
            return
        print_error("No source files could be fetched.")
        raise Exit(1)

    for dest_project in dest_projects:
        if pairs := not_found_pairs[dest_project]:
            pairs_str = ", ".join(f"{c}/{lang}" for c, lang in sorted(pairs))
            print_warning(f"{len(pairs)} combination(s) not found in '{dest_project}' (skipped): {pairs_str}")

        counts = totals[dest_project]
        if counts["accepted"]:
            print_success(
                f"[b]{dest_project}[/b]: updated {counts['accepted']} translations, "
                f"skipped {counts['skipped']} translations, "
                f"and didn't find {counts['not_found']} source strings.",
            )
        else:
            print_warning(
                f"[b]{dest_project}[/b]: no translations updated. Skipped {counts['skipped']} translations, "
                f"and didn't find {counts['not_found']} source strings.",
            )

    if fetch_failures or upload_failures:
        raise Exit(1)


def _print_failures(failures: dict[str, str]) -> None:
    """Print failures, grouped by reason, since many usually fail for the same reason."""
    by_reason: dict[str, list[str]] = {}
    for item, reason in sorted(failures.items()):
        by_reason.setdefault(reason, []).append(item)
    for reason, items in by_reason.items():
        print(f"  {reason}: {', '.join(items)}")


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

    has_globs = any(any(char in c for char in GLOB_CHARACTERS) for c in src_components_set)
    if src_components_set and not has_globs:
        # The source components are all literal, so the source project's component list adds nothing.
        src_component_slugs = set(src_components_set)
    else:
        src_component_slugs = {
            c
            for c in get_weblate_project_component_slugs(api, src_project)
            if not src_components_set or any(fnmatch(c, pattern) for pattern in src_components_set)
        }

    components_per_dest: dict[str, dict[str, str]] = {}
    dest_slugs_cache: dict[str, set[str]] = {}
    for dest_project in dest_projects:
        if dest_project not in dest_slugs_cache:
            dest_slugs_cache[dest_project] = get_weblate_project_component_slugs(api, dest_project)
        components_per_dest[dest_project] = {
            c: c for c in src_component_slugs & dest_slugs_cache[dest_project]
        }
    return components_per_dest


def fetch_language_sources(
    api: WeblateApi,
    project: str,
    language_code: str,
    components: set[str],
    *,
    query: str | None,
    progress: TransientProgress,
    task_id: TaskID,
) -> tuple[dict[str, bytes], dict[str, str]]:
    """Fetch the source PO files of one language, keyed by component slug.

    Without a query, all components are pulled in a single project archive request instead of one request per
    component. The project archive endpoint cannot filter strings, so a query forces per-component downloads.

    :param api: The Weblate API to use.
    :param project: The project to fetch the translations from.
    :param language_code: The CLDR language code to fetch the translations for.
    :param components: The component slugs to fetch.
    :param query: The Weblate search string to filter the strings by, if any.
    :param progress: The progress bar to advance.
    :param task_id: The progress bar task to advance.
    :return: A mapping of component slug to its cleaned PO file, and the reason per component that couldn't be fetched.
    """
    if not query and len(components) >= BULK_ZIP_MIN_COMPONENTS:
        try:
            zip_bytes = api.get_bytes(
                WEBLATE_PROJECT_LANGUAGE_FILE_ENDPOINT.format(project=project, language=language_code),
                params={"format": "zip"},
            )
            po_files = extract_po_files_from_zip(zip_bytes, language_code, components)
        except (WeblateApiError, zipfile.BadZipFile) as e:
            reason = e.summary if isinstance(e, WeblateApiError) else f"invalid ZIP file ({e})"
            print_warning(
                f"Downloading all {language_code} files at once failed ({reason}), so downloading them one by one.",
            )
        else:
            progress.advance(task_id, len(components))
            return (
                {component: clean_po_header(po_file) for component, po_file in po_files.items()},
                dict.fromkeys(sorted(components - set(po_files)), "not in the project's files"),
            )

    source_files: dict[str, bytes] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=WEBLATE_MAX_WORKERS) as executor:
        futures = {
            executor.submit(_fetch_source, api, project, component, language_code, query): component
            for component in sorted(components)
        }
        for future in as_completed(futures):
            component = futures[future]
            progress.advance(task_id)
            try:
                source_files[component] = clean_po_header(future.result())
            except WeblateApiError as e:
                failures[component] = e.summary
    return source_files, failures


def _fetch_source(api: WeblateApi, project: str, component: str, language_code: str, query: str | None) -> bytes:
    """Fetch a source PO file as bytes."""
    params = {"format": "po"}
    if query:
        params["q"] = query
    return api.get_bytes(
        WEBLATE_TRANSLATIONS_FILE_ENDPOINT.format(project=project, component=component, language=language_code),
        params=params,
    )


def _upload_translations(
    api: WeblateApi,
    src_bytes: bytes,
    dest_project: str,
    dest_component: str,
    dest_language: str,
    *,
    upload_data: dict[str, Any],
) -> WeblateTranslationsUploadResponse:
    lang_code = get_cldr_lang(dest_language)
    return api.post(
        WeblateTranslationsUploadResponse,
        WEBLATE_TRANSLATIONS_FILE_ENDPOINT.format(
            project=dest_project,
            component=dest_component,
            language=lang_code,
        ),
        data=upload_data,
        files={"file": (f"{dest_project}-{dest_component}-{lang_code}.po", src_bytes)},
    )
