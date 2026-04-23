import re
from collections import defaultdict
from fnmatch import fnmatch
from pathlib import Path
from typing import Annotated

from typer import Argument, Option, Typer

from odoo_toolkit.common import (
    EMPTY_LIST,
    TransientProgress,
    get_valid_modules_to_path_mapping,
    normalize_list_option,
    print,
    print_command_title,
    print_error,
    print_header,
    print_success,
)

from .common import WeblateConfig, WeblateConfigError

app = Typer()

_DEFAULT_ODOO_CONFIG_EXCLUDES = ["*l10n_*", "hw_*", "*test*", "api_doc", "pos_blackbox_be", "iot_*"]
_DEFAULT_ODOO_L10N_EXCLUDES = [
    "*test*",
    "l10n_account_customer_statements",
    "l10n_account_withholding_tax_pos",
    "*l10n_anz*",
    "*l10n_au*",
    "l10n_cn_city",
    "*l10n_gcc*",
    "*l10n_ie*",
    "*l10n_lk*",
    "*l10n_mt*",
    "*l10n_ng*",
    "*l10n_nz*",
    "*l10n_pk*",
    "*l10n_sg*",
    "*l10n_uk*",
    "*l10n_za*",
    "*l10n_zm*",
]

_DEFAULT_ODOO_PROFILE_CONFIGS = [
    ("odoo-{version}", ["all"], _DEFAULT_ODOO_CONFIG_EXCLUDES),
    ("odoo-{version}-l10n", ["*l10n_*"], _DEFAULT_ODOO_L10N_EXCLUDES),
]


def _get_config_languages(module_path: Path, module_name: str, languages: list[str], lang_filter: bool) -> list[str] | None:
    """Resolve the language list for a module.

    Returning None means the module should be skipped.
    """
    if not lang_filter:
        return languages

    i18n_folder = module_path / "i18n"
    module_languages = [po.stem for po in i18n_folder.glob("*.po")] if i18n_folder.is_dir() else EMPTY_LIST
    if not module_languages and (i18n_folder / f"{module_name}.pot").is_file():
        return None
    return module_languages


def _save_config(weblate_config: WeblateConfig, updated: int, skipped: int) -> None:
    """Persist a Weblate config and report the outcome."""
    try:
        weblate_config.save()
        print(f"Modules added or updated: [b]{updated}[/b]")
        print(f"Modules skipped or removed: [b]{skipped}[/b]")
        print_success("Config file successfully updated.\n")
    except WeblateConfigError as e:
        print_error("Config file update failed.\n", str(e))


def _update_addons_config(
    weblate_config_path: Path,
    local_modules: list[str],
    module_to_path: dict[str, Path],
    project: str,
    languages: list[str],
    lang_filter: bool,
    reset: bool,
) -> None:
    """Update one .weblate.json file for the given modules."""
    print_header(f"Updating [u]{weblate_config_path}[/u]")

    weblate_config = WeblateConfig(weblate_config_path)
    if reset:
        weblate_config.clear(project)

    updated, skipped = 0, 0
    for module_name in TransientProgress().track(local_modules, description="Updating modules ..."):
        module_languages = _get_config_languages(module_to_path[module_name], module_name, languages, lang_filter)
        if module_languages is None:
            skipped += 1
            continue
        if weblate_config.update_module(module_to_path[module_name], project, normalize_list_option(module_languages)):
            updated += 1
        else:
            skipped += 1

    _save_config(weblate_config, updated, skipped)


def _configure_odoo_profile(
    project: str,
    modules: list[str],
    exclude: list[str],
    com_path: Path,
    ent_path: Path,
    extra_addons_paths: list[Path],
) -> None:
    """Apply one predefined Odoo Weblate config profile."""
    print_header(f"Configuring [u]{project}[/u]")
    _update_config(
        modules=modules,
        project=project,
        exclude=exclude,
        path_filters=EMPTY_LIST,
        languages=EMPTY_LIST,
        reset=True,
        com_path=com_path,
        ent_path=ent_path,
        extra_addons_paths=extra_addons_paths,
    )


def _normalize_odoo_version(value: str) -> str:
    """Normalize an Odoo version/branch name to the Weblate project suffix."""
    normalized = value.strip()
    if normalized == "master":
        return normalized

    normalized = re.sub(r"^saas-", "s", normalized)
    normalized = normalized.replace(".", "-")
    if re.fullmatch(r"\d{1,2}-\d", normalized):
        normalized = f"s{normalized}"
    if re.fullmatch(r"\d{1,2}-0", normalized):
        normalized = normalized[:-2]
    if not re.fullmatch(r"(?:\d{1,2}|s\d{1,2}-\d|master)", normalized):
        msg = (
            "Could not normalize the provided Odoo version. "
            "Use values like `18.0`, `18`, `saas-18.2`, `18.2`, `s18-2`, or `master`."
        )
        raise ValueError(msg)
    return normalized


def _detect_odoo_version_from_path(start_path: Path) -> str:
    """Detect the current Odoo version/branch name from the given path or one of its parents."""
    for candidate in (start_path, *start_path.parents):
        if re.fullmatch(r"(?:master|(?:saas-)?\d{1,2}\.\d)", candidate.name):
            return candidate.name
    msg = (
        "Could not detect an Odoo version from the Community path. "
        "Use a Community path inside a branch folder like `18.0` or `saas-18.2`, or provide `--odoo-version`."
    )
    raise ValueError(msg)


def _update_config(
    modules: list[str],
    project: str,
    exclude: list[str],
    path_filters: list[Path],
    languages: list[str],
    reset: bool,
    com_path: Path,
    ent_path: Path,
    extra_addons_paths: list[Path],
) -> None:
    """Update one Weblate config profile."""
    languages = sorted(normalize_list_option(languages))
    exclude = normalize_list_option(exclude)
    lang_filter = False
    if "filter" in languages:
        languages = EMPTY_LIST
        lang_filter = True

    def include_path(p: Path) -> bool:
        if exclude and any(fnmatch(p.name, e) for e in exclude):
            return False
        if path_filters:
            return any(p.is_relative_to(fp.expanduser().resolve()) for fp in path_filters)
        return True

    module_to_path = get_valid_modules_to_path_mapping(
        modules=normalize_list_option(modules),
        com_path=com_path,
        ent_path=ent_path,
        extra_addons_paths=extra_addons_paths,
        include_path=include_path,
    )

    print(f"Modules to include: [b]{'[/b], [b]'.join(sorted(module_to_path.keys()))}[/b]\n")

    all_addons_paths = [p.expanduser().resolve() for p in [com_path, ent_path, *extra_addons_paths]]

    addons_path_to_modules: dict[Path, list[str]] = defaultdict(list[str])
    for module, module_path in module_to_path.items():
        addons_path = next((ap for ap in all_addons_paths if module_path.is_relative_to(ap)), None)
        if addons_path:
            addons_path_to_modules[addons_path].append(module)

    for addons_path, local_modules in addons_path_to_modules.items():
        _update_addons_config(
            weblate_config_path=addons_path / ".weblate.json",
            local_modules=local_modules,
            module_to_path=module_to_path,
            project=project,
            languages=languages,
            lang_filter=lang_filter,
            reset=reset,
        )


@app.command()
def config(
    modules: Annotated[
        list[str],
        Argument(help="Include these Odoo modules in `.weblate.json`, or either `all`, `community`, or `enterprise`."),
    ],
    project: Annotated[str, Option("--project", "-p", help="Specify the Weblate project slug.")],
    exclude: Annotated[
        list[str], Option("--exclude", "-x", help="Exclude these modules from being added or updated."),
    ] = EMPTY_LIST,
    path_filters: Annotated[
        list[Path],
        Option("--path-filter", "-f", help="Only add or update modules within these paths."),
    ] = EMPTY_LIST,
    languages: Annotated[
        list[str],
        Option(
            "--language",
            "-l",
            help="Define specific language codes for this component. Mostly used for localizations. "
            "If none are given, it follows the default languages on Weblate. "
            "If you want the specific PO file languages added as a filter, use `filter`.",
        ),
    ] = EMPTY_LIST,
    reset: Annotated[
        bool, Option("--reset", "-r", help="Reset the config file for the given project and only add the given modules."),
    ] = False,
    com_path: Annotated[
        Path,
        Option(
            "--com-path",
            "-c",
            help="Specify the path to your Odoo Community repository.",
        ),
    ] = Path("odoo"),
    ent_path: Annotated[
        Path,
        Option(
            "--ent-path",
            "-e",
            help="Specify the path to your Odoo Enterprise repository.",
        ),
    ] = Path("enterprise"),
    extra_addons_paths: Annotated[
        list[Path],
        Option(
            "--addons-path",
            "-a",
            help="Specify extra addons paths if your modules are not in Community or Enterprise.",
        ),
    ] = EMPTY_LIST,
) -> None:
    """Update modules in the Weblate config file.

    This command will add, update, or remove module entries in `.weblate.json` files. The `.weblate.json` files need to
    be located at the provided addons paths' roots. If not, a new file will be created.\n
    \n
    If no `languages` are provided, and a localization module is added, we will automatically limit the languages to the
    ones currently available in that localization module.\n
    \n
    For `odoo` and `enterprise`, the project slug follows the format `odoo-18` for major versions and `odoo-s18-1` for
    SaaS versions. Other repos have their own project slugs. Check the Weblate URLs to find the right project slug.
    """
    print_command_title(":memo: Odoo Weblate Config")

    _update_config(
        modules=modules,
        project=project,
        exclude=exclude,
        path_filters=path_filters,
        languages=languages,
        reset=reset,
        com_path=com_path,
        ent_path=ent_path,
        extra_addons_paths=extra_addons_paths,
    )


@app.command()
def config_odoo(
    odoo_version: Annotated[
        str | None,
        Option(
            "--odoo-version",
            "-v",
            help=(
                "The Odoo branch/version to configure, like `18.0`, `18`, `saas-18.2`, `18.2`, `s18-2`, or `master`. "
                "If omitted, it is detected from the Community path (or one of its parent folders)."
            ),
        ),
    ] = None,
    com_path: Annotated[
        Path,
        Option(
            "--com-path",
            "-c",
            help="Specify the path to your Odoo Community repository.",
        ),
    ] = Path("odoo"),
    ent_path: Annotated[
        Path,
        Option(
            "--ent-path",
            "-e",
            help="Specify the path to your Odoo Enterprise repository.",
        ),
    ] = Path("enterprise"),
    extra_addons_paths: Annotated[
        list[Path],
        Option(
            "--addons-path",
            "-a",
            help="Specify extra addons paths if your modules are not in Community or Enterprise.",
        ),
    ] = EMPTY_LIST,
) -> None:
    """Generate the standard Odoo and Odoo l10n Weblate config profiles."""
    print_command_title(":memo: Odoo Weblate Config Profiles")

    try:
        raw_odoo_version = odoo_version or _detect_odoo_version_from_path(com_path.expanduser().resolve())
        normalized_odoo_version = _normalize_odoo_version(raw_odoo_version)
    except ValueError as e:
        print_error(str(e))
        return

    print(f"Detected Odoo version: [b]{raw_odoo_version}[/b]")
    print(f"Using Weblate project suffix: [b]{normalized_odoo_version}[/b]\n")

    for project_template, modules, exclude in _DEFAULT_ODOO_PROFILE_CONFIGS:
        _configure_odoo_profile(
            project=project_template.format(version=normalized_odoo_version),
            modules=modules,
            exclude=exclude,
            com_path=com_path,
            ent_path=ent_path,
            extra_addons_paths=extra_addons_paths,
        )
