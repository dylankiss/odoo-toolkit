import csv
import re
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from rich.console import RenderableType
from rich.tree import Tree

from odoo_toolkit.common import TransientProgress, get_valid_modules_to_path_mapping, normalize_list_option, print
from odoo_toolkit.mv.config import get_default_repo_path

# The modules `-x default` excludes: localizations, themes, hardware and tests.
DEFAULT_EXCLUDE = ["*l10n_*", "*theme_*", "*hw_*", "*test*", "pos_blackbox_be"]


@dataclass
class ModuleSelection:
    """The modules a command works on, and how they were selected."""

    # The selected modules, mapped to their directories.
    module_to_path: dict[str, Path]
    com_path: Path
    ent_path: Path
    # Whether a module, or a module directory, may be used, according to `--exclude` and `--path-filter`.
    include_module: Callable[[str], bool]
    include_path: Callable[[Path], bool]


def select_modules(
    modules: list[str],
    *,
    exclude: list[str],
    path_filters: list[Path],
    com_path: Path,
    ent_path: Path,
    extra_addons_paths: list[Path],
) -> ModuleSelection:
    """Select the modules a command works on, from its arguments and options.

    :param modules: The modules (or glob patterns), or `all`, `community`, `enterprise`, `community-l10n`, or
        `enterprise-l10n`.
    :param exclude: The modules (or glob patterns) to leave out, where `default` stands for `DEFAULT_EXCLUDE`.
    :param path_filters: Only use modules within these directories, if any.
    :param com_path: The Odoo Community repository. Its default, `odoo`, means the one of the multiverse version
        directory you're in, if any.
    :param ent_path: The Odoo Enterprise repository, like `com_path`.
    :param extra_addons_paths: Extra directories containing modules.
    :return: The selected modules.
    """
    exclude = normalize_list_option(exclude)
    if "default" in exclude:
        exclude = [e for e in exclude if e != "default"] + DEFAULT_EXCLUDE
    filters = [path.expanduser().resolve() for path in path_filters]

    def include_module(module: str) -> bool:
        return not any(fnmatch(module, pattern) for pattern in exclude)

    def include_path(path: Path) -> bool:
        return include_module(path.name) and (not filters or any(path.is_relative_to(f) for f in filters))

    com_path = get_default_repo_path(com_path, "odoo")
    ent_path = get_default_repo_path(ent_path, "enterprise")
    module_to_path = get_valid_modules_to_path_mapping(
        modules=normalize_list_option(modules),
        com_path=com_path,
        ent_path=ent_path,
        extra_addons_paths=extra_addons_paths,
        include_path=include_path,
    )
    return ModuleSelection(module_to_path, com_path, ent_path, include_module, include_path)

ODOO_LANGUAGES = {
    "am",        # Amharic
    "ar",        # Arabic
    "ar_SY",     # Arabic (Syria)
    "az",        # Azerbaijani
    "be",        # Belarusian
    "bg",        # Bulgarian
    "bn",        # Bengali
    "bs",        # Bosnian
    "ca",        # Catalan
    "cs",        # Czech
    "da",        # Danish
    "de",        # German
    "de_CH",     # German (Switzerland)
    "el",        # Greek
    "en_AU",     # English (Australia)
    "en_CA",     # English (Canada)
    "en_GB",     # English (United Kingdom)
    "en_IN",     # English (India)
    "en_NZ",     # English (New Zealand)
    "es",        # Spanish
    "es_419",    # Spanish (Latin America)
    "es_AR",     # Spanish (Argentina)
    "es_BO",     # Spanish (Bolivia)
    "es_CL",     # Spanish (Chile)
    "es_CO",     # Spanish (Colombia)
    "es_CR",     # Spanish (Costa Rica)
    "es_DO",     # Spanish (Dominican Republic)
    "es_EC",     # Spanish (Ecuador)
    "es_GT",     # Spanish (Guatemala)
    "es_MX",     # Spanish (Mexico)
    "es_PA",     # Spanish (Panama)
    "es_PE",     # Spanish (Peru)
    "es_PY",     # Spanish (Paraguay)
    "es_UY",     # Spanish (Uruguay)
    "es_VE",     # Spanish (Venezuela)
    "et",        # Estonian
    "eu",        # Basque
    "fa",        # Persian
    "fi",        # Finnish
    "fr",        # French
    "fr_BE",     # French (Belgium)
    "fr_CA",     # French (Canada)
    "fr_CH",     # French (Switzerland)
    "gl",        # Galician
    "gu",        # Gujarati
    "he",        # Hebrew
    "hi",        # Hindi
    "hr",        # Croatian
    "hu",        # Hungarian
    "id",        # Indonesian
    "it",        # Italian
    "ja",        # Japanese
    "ka",        # Georgian
    "kab",       # Kabyle
    "km",        # Khmer
    "ko",        # Korean (South Korea)
    "ko_KP",     # Korean (North Korea)
    "lb",        # Luxembourgish
    "lo",        # Lao
    "lt",        # Lithuanian
    "lv",        # Latvian
    "mk",        # Macedonian
    "ml",        # Malayalam
    "mn",        # Mongolian
    "ms",        # Malay
    "my",        # Burmese
    "nb",        # Norwegian Bokmål
    "nl",        # Dutch
    "nl_BE",     # Dutch (Belgium)
    "pl",        # Polish
    "pt",        # Portuguese
    "pt_AO",     # Portuguese (Angola)
    "pt_BR",     # Portuguese (Brazil)
    "ro",        # Romanian
    "ru",        # Russian
    "sk",        # Slovak
    "sl",        # Slovenian
    "sq",        # Albanian
    "sr",        # Serbian
    "sr@latin",  # Serbian (Latin script)
    "sv",        # Swedish
    "sw",        # Swahili
    "te",        # Telugu
    "th",        # Thai
    "tl",        # Tagalog
    "tr",        # Turkish
    "uk",        # Ukrainian
    "vi",        # Vietnamese
    "zh_CN",     # Chinese (China)
    "zh_HK",     # Chinese (Hong Kong)
    "zh_TW",     # Chinese (Taiwan)
}


def _create_cldr_dicts() -> tuple[dict[str, str], dict[str, str]]:
    """Create two dictionaries: one mapping language codes to their names, the other to their plural forms rules."""
    cldr_path = Path(__file__).parent / "cldr.csv"
    names: dict[str, str] = {}
    plural_rules: dict[str, str] = {}
    if cldr_path.is_file():
        with cldr_path.open("r", encoding="utf-8") as cldr_file:
            reader = csv.reader(cldr_file)
            # Skip the header row.
            next(reader)

            for code, name, nplurals, plural_rule in reader:
                names[code] = name
                plural_rules[code] = f"nplurals={nplurals}; plural={plural_rule};"
    return names, plural_rules


CLDR_LANGUAGE_NAMES, CLDR_PLURAL_RULES = _create_cldr_dicts()


# The Odoo language codes (used in the `.po` file names) that differ from their CLDR codes, which Weblate uses.
ODOO_TO_CLDR_LANG = {
    "ku": "ckb",
    "nb": "nb_NO",
    "sr@latin": "sr_Latn",
    "zh_CN": "zh_Hans",
    "zh_TW": "zh_Hant",
}
CLDR_TO_ODOO_LANG = {cldr: odoo for odoo, cldr in ODOO_TO_CLDR_LANG.items()}


def get_cldr_lang(lang_code: str) -> str:
    """Convert an Odoo language code to the CLDR one, which Weblate uses, like `zh_Hans` for `zh_CN`."""
    return ODOO_TO_CLDR_LANG.get(lang_code, lang_code)


def get_odoo_lang(lang_code: str) -> str:
    """Convert a CLDR language code to the Odoo one, used in the `.po` file names, like `zh_CN` for `zh_Hans`."""
    return CLDR_TO_ODOO_LANG.get(lang_code, lang_code)


def get_language_name(lang: str) -> str:
    """Get the language name for a given language code.

    :param lang: The language code to get the name for.
    :return: The language name for the given language code, or the code itself if not found.
    """
    lang = get_cldr_lang(lang)
    return CLDR_LANGUAGE_NAMES.get(lang, lang)


def get_plural_forms(lang: str) -> str:
    """Get the plural forms rule for a given language code.

    :param lang: The language code to get the plural forms rule for.
    :return: The plural forms rule for the given language code, falling back to its base language, and to the rule
        of English (`nplurals=2; plural=(n != 1);`) if neither is known.
    """
    lang = get_cldr_lang(lang)
    if lang in CLDR_PLURAL_RULES:
        return CLDR_PLURAL_RULES[lang]

    # Fallback to the base language if the specific locale is not found.
    base_lang = lang.split("_", 1)[0]
    return CLDR_PLURAL_RULES.get(base_lang, "nplurals=2; plural=(n != 1);")


def get_nplurals(lang: str) -> int:
    """Get the number of plural forms of a language, from its plural forms rule."""
    match = re.search(r"nplurals\s*=\s*(\d+)", get_plural_forms(lang))
    return int(match.group(1)) if match else 2


def update_module_po(
    *,
    action: Callable[[str, Path, Path], tuple[bool, RenderableType]],
    module: str,
    languages: list[str],
    module_path: Path,
    module_tree: Tree,
    description: str,
) -> list[str] | None:
    """Perform an action on a module's .po files for the given languages, using the .pot file.

    :param action: The action to perform on a .po file. A function that takes the language, the .pot file path and the
        module's path, and returns whether it succeeded and a message to render in the `module_tree`.
    :param module: The module whose .po files we're working with.
    :param languages: The language codes of the .po files we're working with.
    :param module_path: The path to the module's directory.
    :param module_tree: The visual tree to render the action's messages, or error messages in.
    :param description: What the action does, like `Updating`, for the progress bar.
    :return: The languages the action failed for, or None if there was nothing to do, like for a module without a .pot
        file or without the .po files.
    """
    pot_path = module_path / "i18n" / f"{module}.pot"
    if not pot_path.is_file():
        module_tree.add("[d]No .pot file[/d]")
        return None
    if not languages:
        module_tree.add("[d]No .po files[/d]")
        return None

    failures: list[str] = []
    for lang in TransientProgress().track(languages, description=f"{description} [b]{module}[/b]"):
        succeeded, renderable = action(lang, pot_path, module_path)
        module_tree.add(renderable)
        if not succeeded:
            failures.append(lang)
    return failures


def process_modules(
    module_to_path: dict[str, Path],
    *,
    languages_of: Callable[[Path], list[str]],
    action: Callable[[str, Path, Path], tuple[bool, RenderableType]],
    description: str,
) -> tuple[int, dict[str, list[str]]]:
    """Perform an action on the .po files of every module, showing the results per module.

    :param module_to_path: The modules, mapped to their directories.
    :param languages_of: Get the languages to work on, given a module's directory.
    :param action: The action to perform on a .po file, see `update_module_po`.
    :param description: What the action does, like `Updating`, for the progress bar.
    :return: The number of modules there was something to do for, and the failed languages per module.
    """
    processed = 0
    failures: dict[str, list[str]] = {}
    with TransientProgress() as progress:
        task = progress.add_task(f"{description} .po files", total=len(module_to_path))
        for module, module_path in sorted(module_to_path.items()):
            progress.update(task, description=f"{description} .po files for [b]{module}[/b]")
            module_tree = Tree(f"[b]{module}[/b]")
            failed = update_module_po(
                action=action,
                module=module,
                languages=languages_of(module_path),
                module_path=module_path,
                module_tree=module_tree,
                description=description,
            )
            print(module_tree, "")
            if failed is not None:
                processed += 1
            if failed:
                failures[module] = failed
            progress.advance(task)
    return processed, failures
