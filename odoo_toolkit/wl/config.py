import json
import re
from collections import defaultdict
from fnmatch import fnmatch
from pathlib import Path
from typing import Annotated, Literal

from typer import Argument, Exit, Option, Typer

from odoo_toolkit.common import (
    EMPTY_LIST,
    TransientProgress,
    get_valid_modules_to_path_mapping,
    is_l10n_module,
    normalize_list_option,
    print,
    print_command_title,
    print_error,
    print_header,
    print_success,
)
from odoo_toolkit.mv.config import get_default_repo_path

from .common import WeblateComponentData

app = Typer()

WeblateConfigType = dict[str, dict[str, list[WeblateComponentData]]]

# Maps fnmatch glob patterns (matched against module names) to a fixed list of language codes.
# Patterns are evaluated in order; the first match wins.
# Modules not listed here fall back to scanning available .po files.
L10N_LANGUAGE_MAP: dict[str, list[str] | None] = {
    "*l10n_account_withholding_tax*": ["es_419", "id", "th"],
    "*l10n_ae*": ["ar"],
    "*l10n_ar*": ["es_419"],
    "*l10n_at*": ["de"],
    "*l10n_bd*": ["bn"],
    "*l10n_be*": ["de", "fr", "nl"],
    "*l10n_bf*": ["fr"],
    "*l10n_bg*": ["bg"],
    "*l10n_bh*": ["ar"],
    "*l10n_bj*": ["fr"],
    "*l10n_bo*": ["es_419"],
    "*l10n_br*": ["pt_BR"],
    "*l10n_ca*": ["fr"],
    "*l10n_cd*": ["fr"],
    "*l10n_cf*": ["fr"],
    "*l10n_cg*": ["fr"],
    "*l10n_ch*": ["de", "fr", "it"],
    "*l10n_ci*": ["fr"],
    "*l10n_cl*": ["es_419"],
    "*l10n_cm*": ["fr"],
    "*l10n_cn_city*": None,
    "*l10n_cn*": ["zh_CN"],
    "*l10n_co*": ["es_419"],
    "*l10n_cr*": ["es_419"],
    "*l10n_cy*": ["el", "tr"],
    "*l10n_cz*": ["cs"],
    "*l10n_de*": ["de"],
    "*l10n_din5008*": ["de", "fr", "it"],
    "*l10n_dk*": ["da"],
    "*l10n_do*": ["es_419"],
    "*l10n_dz*": ["ar", "fr"],
    "*l10n_ec*": ["es_419"],
    "*l10n_ee*": ["et"],
    "*l10n_eg*": ["ar"],
    "*l10n_employment_hero*": ["ms"],
    "*l10n_es*": ["es"],
    "*l10n_et*": ["am"],
    "*l10n_eu*": ["bg", "cs", "da", "de", "el", "es", "et", "fi", "fr", "hr", "hu", "it", "lt", "lv", "nl", "pl", "pt", "ro", "sk", "sl", "sv"],
    "*l10n_fi*": ["fi"],
    "*l10n_fr*": ["fr"],
    "*l10n_ga*": ["fr"],
    "*l10n_gcc*": ["ar"],
    "*l10n_ge*": ["ka"],
    "*l10n_gf*": ["fr"],
    "*l10n_gn*": ["fr"],
    "*l10n_gp*": ["fr"],
    "*l10n_gq*": ["es", "fr", "pt"],
    "*l10n_gr*": ["el"],
    "*l10n_gt*": ["es_419"],
    "*l10n_gw*": ["pt"],
    "*l10n_hk*": ["zh_TW"],
    "*l10n_hn*": ["es_419"],
    "*l10n_hr*": ["hr"],
    "*l10n_hu*": ["hu"],
    "*l10n_id*": ["id"],
    "*l10n_il*": ["he"],
    "*l10n_in*": ["hi"],
    "*l10n_iq*": ["ar"],
    "*l10n_it*": ["it"],
    "*l10n_jo*": ["ar"],
    "*l10n_jp*": ["ja"],
    "*l10n_ke*": ["sw"],
    "*l10n_kh*": ["km"],
    "*l10n_km*": ["ar", "fr"],
    "*l10n_kr*": ["ko"],
    "*l10n_kw*": ["ar"],
    "*l10n_kz*": ["ru"],
    "*l10n_latam*": ["es_419", "pt_BR"],
    "*l10n_lb*": ["ar", "fr"],
    "*l10n_lk*": ["si", "ta"],
    "*l10n_lt*": ["lt"],
    "*l10n_lu*": ["de", "fr", "lb"],
    "*l10n_lv*": ["lv"],
    "*l10n_ma*": ["ar", "fr"],
    "*l10n_mc*": ["fr"],
    "*l10n_mk*": ["mk"],
    "*l10n_ml*": ["fr"],
    "*l10n_mm*": ["my"],
    "*l10n_mn*": ["mn"],
    "*l10n_mq*": ["fr"],
    "*l10n_mr*": ["fr"],
    "*l10n_mu*": ["fr"],
    "*l10n_mx*": ["es_419"],
    "*l10n_my*": ["ms"],
    "*l10n_mz*": ["pt"],
    "*l10n_ne*": ["fr"],
    "*l10n_nl*": ["nl"],
    "*l10n_no*": ["nb"],
    "*l10n_om*": ["ar"],
    "*l10n_pa*": ["es_419"],
    "*l10n_pe*": ["es_419"],
    "*l10n_ph*": ["tl"],
    "*l10n_pl*": ["pl"],
    "*l10n_pt*": ["pt"],
    "*l10n_qa*": ["ar"],
    "*l10n_re*": ["fr"],
    "*l10n_ro*": ["ro"],
    "*l10n_rs*": ["sr@latin"],
    "*l10n_rw*": ["fr", "sw"],
    "*l10n_sa*": ["ar"],
    "*l10n_se*": ["sv"],
    "*l10n_si*": ["sl"],
    "*l10n_sk*": ["sk"],
    "*l10n_sn*": ["fr"],
    "*l10n_syscohada*": ["fr"],
    "*l10n_td*": ["ar", "fr"],
    "*l10n_tg*": ["fr"],
    "*l10n_th*": ["th"],
    "*l10n_tn*": ["ar", "fr"],
    "*l10n_tr*": ["tr"],
    "*l10n_tw*": ["zh_TW"],
    "*l10n_tz*": ["sw"],
    "*l10n_ua*": ["uk"],
    "*l10n_ug*": ["sw"],
    "*l10n_us*": ["es_419"],
    "*l10n_uy*": ["es_419"],
    "*l10n_uz*": ["ru", "uz"],
    "*l10n_ve*": ["es_419"],
    "*l10n_vn*": ["vi"],
    "*l10n_yt*": ["fr"],
}



class WeblateConfigError(Exception):
    """Custom exception for WeblateConfig errors."""

    def __init__(self, file_path: Path, error_type: Literal["load", "save"]) -> None:
        """Initialize a WeblateConfigError for loading `file_path`."""
        match error_type:
            case "load":
                super().__init__(f"The configuration file '{file_path}' could not be loaded.")
            case "save":
                super().__init__(f"The configuration file '{file_path}' could not be saved.")


class WeblateConfig:
    """A Weblate config file."""

    def __init__(self, file_path: Path) -> None:
        """Initialize a WeblateConfig object.

        :param file_path: The file to load into the object or to save the new object to.
        :raises WeblateConfigError: If the given file could not be loaded or parsed.
        """
        self.file_path = file_path
        self.config: dict[str, dict[str, list[WeblateComponentData]]] = {
            "projects": defaultdict[str, list[WeblateComponentData]](list),
        }

        if self.file_path.is_file():
            try:
                projects = json.loads(self.file_path.read_text()).get("projects")
                if projects:
                    self.config["projects"].update(projects)
            except (OSError, json.JSONDecodeError) as e:
                raise WeblateConfigError(self.file_path, "load") from e

    def update_module(self, module_path: Path, project: str, languages: list[str]) -> bool:
        """Update a module configuration in the Weblate config file.

        If the `.pot` file exists, the module configuration will be added or updated.
        If the `.pot` file doesn't exist, the module configuration will be removed.

        If no languages are provided and the module is a l10n module, only the languages available in that module will
        be added.

        :param module_path: The path to the module to update.
        :param project: The Weblate project slug.
        :param languages: The specific language codes to translate this module into.
        :return: True if the module was added or updated, False if it couldn't be added or updated, or was removed.
        """
        module_name = module_path.name
        existing_module_config = next(
            (c for c in self.config["projects"][project] if c.get("name") == module_name), None,
        )
        if not (module_path / "i18n" / f"{module_name}.pot").is_file():
            if existing_module_config:
                self.config["projects"][project].remove(existing_module_config)
            return False

        relative_module_path = module_path.relative_to(self.file_path.parent)
        module_config: WeblateComponentData = {
            "name": module_name,
            "filemask": f"{relative_module_path}/i18n/*.po",
            "new_base": f"{relative_module_path}/i18n/{module_name}.pot",
        }
        if not languages and is_l10n_module(module_name):
            # If we are adding a l10n module, check the fixed language map first.
            fixed_language_match = next(
                ((pattern, langs) for pattern, langs in L10N_LANGUAGE_MAP.items() if fnmatch(module_name, pattern)),
                None,
            )
            if fixed_language_match is None:
                languages = sorted([lang.stem for lang in (module_path / "i18n").glob("*.po") if lang.is_file()])
            else:
                _, fixed_languages = fixed_language_match
                languages = fixed_languages or []
        if languages:
            module_config["language_regex"] = f"^({'|'.join(sorted(languages))})$"
        if existing_module_config:
            # Without languages anymore, the module follows the project's languages again.
            existing_module_config.pop("language_regex", None)
            existing_module_config.update(module_config)
        else:
            self.config["projects"][project].append(module_config)
        return True

    def save(self) -> None:
        """Save the Weblate config to a file.

        :raises WeblateConfigError: If the given file could not be saved.
        """

        def sort_config(config: WeblateConfigType) -> WeblateConfigType:
            return {
                "projects": {
                    project: sorted(components, key=lambda c: c.get("name", ""))
                    for project, components in sorted(config["projects"].items())
                },
            }

        try:
            self.file_path.write_text(f"{json.dumps(sort_config(self.config), indent=4)}\n")
        except (OSError, json.JSONDecodeError) as e:
            raise WeblateConfigError(self.file_path, "save") from e

    def clear(self, project: str) -> None:
        """Remove a project's components from the config."""
        if project in self.config["projects"]:
            self.config["projects"][project].clear()



_DEFAULT_ODOO_CONFIG_EXCLUDES = [
    "hw_*",
    "*_test",
    "test_*",
    "api_doc",
    "pos_blackbox_be",
    "iot_*",
    "l10n_account_customer_statements",
    "l10n_account_withholding_tax_pos",
    "*l10n_anz*",
    "*l10n_au*",
    "l10n_cn_city",
    "*l10n_gf*",
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
    """Persist a Weblate config and report the outcome.

    :raises Exit: If the file couldn't be saved.
    """
    try:
        weblate_config.save()
    except WeblateConfigError as e:
        print_error("Config file update failed.\n", str(e))
        raise Exit(1) from e
    print(f"Modules added or updated: [b]{updated}[/b]")
    print(f"Modules skipped or removed: [b]{skipped}[/b]")
    print_success("Config file successfully updated.\n")


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
        if weblate_config.update_module(module_to_path[module_name], project, module_languages):
            updated += 1
        else:
            skipped += 1

    _save_config(weblate_config, updated, skipped)


def _normalize_odoo_version(value: str) -> str:
    """Normalize an Odoo version/branch name to the Weblate project suffix."""
    normalized = value.strip()
    if normalized == "master":
        return normalized

    normalized = re.sub(r"^saas-", "s", normalized)
    normalized = normalized.replace(".", "-")
    if re.fullmatch(r"\d{1,2}-0", normalized):
        normalized = normalized[:-2]
    elif re.fullmatch(r"\d{1,2}-\d", normalized):
        normalized = f"s{normalized}"
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
    """Update the `.weblate.json` files of the given modules."""
    com_path = get_default_repo_path(com_path, "odoo")
    ent_path = get_default_repo_path(ent_path, "enterprise")
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
    """Generate the standard Odoo Weblate config profile."""
    print_command_title(":memo: Odoo Weblate Config Profiles")

    com_path = get_default_repo_path(com_path, "odoo")
    try:
        raw_odoo_version = odoo_version or _detect_odoo_version_from_path(com_path.expanduser().resolve())
        normalized_odoo_version = _normalize_odoo_version(raw_odoo_version)
    except ValueError as e:
        print_error(str(e))
        raise Exit(1) from e

    print(f"Detected Odoo version: [b]{raw_odoo_version}[/b]")
    print(f"Using Weblate project suffix: [b]{normalized_odoo_version}[/b]\n")

    project = f"odoo-{normalized_odoo_version}"
    print_header(f"Configuring [u]{project}[/u]")
    _update_config(
        modules=["all"],
        project=project,
        exclude=_DEFAULT_ODOO_CONFIG_EXCLUDES,
        path_filters=EMPTY_LIST,
        languages=EMPTY_LIST,
        reset=True,
        com_path=com_path,
        ent_path=ent_path,
        extra_addons_paths=extra_addons_paths,
    )
