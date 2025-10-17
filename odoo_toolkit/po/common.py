import csv
from collections.abc import Callable
from pathlib import Path

from rich.console import RenderableType
from rich.tree import Tree

from odoo_toolkit.common import Status, TransientProgress
from odoo_toolkit.wl.common import get_weblate_lang

ODOO_LANGUAGES = {
    "am",           # Amharic
    "ar",           # Arabic
    "ar_SY",        # Arabic (Syria)
    "az",           # Azerbaijani
    "be",           # Belarusian
    "bg",           # Bulgarian
    "bn",           # Bengali
    "bs",           # Bosnian
    "ca",           # Catalan
    "cs",           # Czech
    "da",           # Danish
    "de",           # German
    "de_CH",        # German (Switzerland)
    "el",           # Greek
    "en_AU",        # English (Australia)
    "en_CA",        # English (Canada)
    "en_GB",        # English (United Kingdom)
    "en_IN",        # English (India)
    "en_NZ",        # English (New Zealand)
    "es",           # Spanish
    "es_419",       # Spanish (Latin America)
    "es_AR",        # Spanish (Argentina)
    "es_BO",        # Spanish (Bolivia)
    "es_CL",        # Spanish (Chile)
    "es_CO",        # Spanish (Colombia)
    "es_CR",        # Spanish (Costa Rica)
    "es_DO",        # Spanish (Dominican Republic)
    "es_EC",        # Spanish (Ecuador)
    "es_GT",        # Spanish (Guatemala)
    "es_MX",        # Spanish (Mexico)
    "es_PA",        # Spanish (Panama)
    "es_PE",        # Spanish (Peru)
    "es_PY",        # Spanish (Paraguay)
    "es_UY",        # Spanish (Uruguay)
    "es_VE",        # Spanish (Venezuela)
    "et",           # Estonian
    "eu",           # Basque
    "fa",           # Persian
    "fi",           # Finnish
    "fr",           # French
    "fr_BE",        # French (Belgium)
    "fr_CA",        # French (Canada)
    "fr_CH",        # French (Switzerland)
    "gl",           # Galician
    "gu",           # Gujarati
    "he",           # Hebrew
    "hi",           # Hindi
    "hr",           # Croatian
    "hu",           # Hungarian
    "id",           # Indonesian
    "it",           # Italian
    "ja",           # Japanese
    "ka",           # Georgian
    "kab",          # Kabyle
    "km",           # Khmer
    "ko",           # Korean (South Korea)
    "ko_KP",        # Korean (North Korea)
    "lb",           # Luxembourgish
    "lo",           # Lao
    "lt",           # Lithuanian
    "lv",           # Latvian
    "mk",           # Macedonian
    "ml",           # Malayalam
    "mn",           # Mongolian
    "ms",           # Malay
    "my",           # Burmese
    "nb",           # Norwegian Bokmål
    "nl",           # Dutch
    "nl_BE",        # Dutch (Belgium)
    "pl",           # Polish
    "pt",           # Portuguese
    "pt_AO",        # Portuguese (Angola)
    "pt_BR",        # Portuguese (Brazil)
    "ro",           # Romanian
    "ru",           # Russian
    "sk",           # Slovak
    "sl",           # Slovenian
    "sq",           # Albanian
    "sr",           # Serbian
    "sr@latin",     # Serbian (Latin script)
    "sv",           # Swedish
    "sw",           # Swahili
    "te",           # Telugu
    "th",           # Thai
    "tl",           # Tagalog
    "tr",           # Turkish
    "uk",           # Ukrainian
    "vi",           # Vietnamese
    "zh_CN",        # Chinese (China)
    "zh_HK",        # Chinese (Hong Kong)
    "zh_TW",        # Chinese (Taiwan)
}

def _create_plural_rules_dict() -> dict[str, str]:
    """Create a dictionary mapping language codes to their plural forms rules."""
    cldr_path = Path(__file__).parent / "cldr.csv"
    plural_rules: dict[str, str] = {}
    if cldr_path.is_file():
        with cldr_path.open("r", encoding="utf-8") as cldr_file:
            reader = csv.reader(cldr_file)
            # Skip the header row.
            next(reader)

            for code, _name, nplurals, plural_rule in reader:
                plural_rules[code] = f"nplurals={nplurals}; plural={plural_rule};"
    return plural_rules

CLDR_PLURAL_RULES = _create_plural_rules_dict()

def get_plural_forms(lang: str) -> str:
    """Get the plural forms rule for a given language code.

    :param lang: The language code to get the plural forms rule for.
    :return: The plural forms rule for the given language code, or an empty string if not found.
    """
    lang = get_weblate_lang(lang)
    if lang in CLDR_PLURAL_RULES:
        return CLDR_PLURAL_RULES[lang]

    # Fallback to the base language if the specific locale is not found.
    base_lang = lang.split("_", 1)[0]
    return CLDR_PLURAL_RULES.get(base_lang, "nplurals=2; plural=(n != 1);")

def update_module_po(
    action: Callable[[str, Path, Path], tuple[bool, RenderableType]],
    module: str,
    languages: list[str],
    module_path: Path,
    module_tree: Tree,
) -> tuple[Status, list[str]]:
    """Perform an action on a module's .po files for the given languages, using the .pot file.

    :param action: The action to perform on the .po files. A function that takes the language, the .pot file path and
        the module's path as parameters, and that returns the success status and a message to render in the
        `module_tree`.
    :param module: The module whose .po files we're working with.
    :param languages: The language codes of the .po files we're working with.
    :param module_path: The path to the module's directory.
    :param module_tree: The visual tree to render the action's messages, or error messages in.
    :return: A tuple with the first item being `Status.SUCCESS` if the `action` succeeded for all .po files,
        `Status.FAILURE` if the `action` failed for every .po file, and `Status.PARTIAL` if the `action` succeeded for
        some .po files. The second item is a list of language codes for which the `action` failed.
    """
    success = failure = False
    pot_path = module_path / "i18n" / f"{module}.pot"
    if not pot_path.is_file():
        module_tree.add("No .pot file found!")
        return Status.FAILURE, []

    failures: list[str] = []
    for lang in TransientProgress().track(languages, description=f"Updating [b]{module}[/b]"):
        result, renderable = action(lang, pot_path, module_path)
        module_tree.add(renderable)
        success = success or result
        failure = failure or not result
        if result is False:
            failures.append(lang)

    return (Status.PARTIAL, failures) if success and failure else (Status.SUCCESS, failures) if success else (Status.FAILURE, failures)
