from collections.abc import Callable
from enum import Enum
from pathlib import Path

from polib import POFile, pofile
from rich.console import RenderableType
from rich.tree import Tree

from odoo_toolkit.common import Status, TransientProgress, get_error_log_panel


class Lang(str, Enum):
    """Languages available in Odoo."""

    ALL     = "all"
    AM_ET   = "am"          # Amharic (Ethiopia)
    AR_001  = "ar"          # Arabic
    AR_SY   = "ar_SY"       # Arabic (Syria)
    AZ_AZ   = "az"          # Azerbaijani (Azerbaijan)
    BE_BY   = "be"          # Belarusian (Belarus)
    BG_BG   = "bg"          # Bulgarian (Bulgaria)
    BN_IN   = "bn"          # Bengali (India)
    BS_BA   = "bs"          # Bosnian (Bosnia)
    CA_ES   = "ca"          # Catalan (Spain)
    CS_CZ   = "cs"          # Czech (Czechia)
    DA_DK   = "da"          # Danish (Denmark)
    DE_DE   = "de"          # German (Germany)
    DE_CH   = "de_CH"       # German (Switzerland)
    EL_GR   = "el"          # Greek (Greece)
    EN_AU   = "en_AU"       # English (Australia)
    EN_CA   = "en_CA"       # English (Canada)
    EN_GB   = "en_GB"       # English (United Kingdom)
    EN_IN   = "en_IN"       # English (India)
    EN_NZ   = "en_NZ"       # English (New Zealand)
    ES_ES   = "es"          # Spanish (Spain)
    ES_419  = "es_419"      # Spanish (Latin America)
    ES_AR   = "es_AR"       # Spanish (Argentina)
    ES_BO   = "es_BO"       # Spanish (Bolivia)
    ES_CL   = "es_CL"       # Spanish (Chile)
    ES_CO   = "es_CO"       # Spanish (Colombia)
    ES_CR   = "es_CR"       # Spanish (Costa Rica)
    ES_DO   = "es_DO"       # Spanish (Dominican Republic)
    ES_EC   = "es_EC"       # Spanish (Ecuador)
    ES_GT   = "es_GT"       # Spanish (Guatemala)
    ES_MX   = "es_MX"       # Spanish (Mexico)
    ES_PA   = "es_PA"       # Spanish (Panama)
    ES_PE   = "es_PE"       # Spanish (Peru)
    ES_PY   = "es_PY"       # Spanish (Paraguay)
    ES_UY   = "es_UY"       # Spanish (Uruguay)
    ES_VE   = "es_VE"       # Spanish (Venezuela)
    ET_EE   = "et"          # Estonian (Estonia)
    EU_ES   = "eu"          # Basque (Spain)
    FA_IR   = "fa"          # Persian (Iran)
    FI_FI   = "fi"          # Finnish (Finland)
    FR_FR   = "fr"          # French (France)
    FR_BE   = "fr_BE"       # French (Belgium)
    FR_CA   = "fr_CA"       # French (Canada)
    FR_CH   = "fr_CH"       # French (Switzerland)
    GL_ES   = "gl"          # Galician (Spain)
    GU_IN   = "gu"          # Gujarati (India)
    HE_IL   = "he"          # Hebrew (Israel)
    HI_IN   = "hi"          # Hindi (India)
    HR_HR   = "hr"          # Croatian (Croatia)
    HU_HU   = "hu"          # Hungarian (Hungary)
    ID_ID   = "id"          # Indonesian (Indonesia)
    IT_IT   = "it"          # Italian (Italy)
    JA_JP   = "ja"          # Japanese (Japan)
    KA_GE   = "ka"          # Georgian (Georgia)
    KAB_DZ  = "kab"         # Kabyle (Algeria)
    KM_KH   = "km"          # Khmer (Cambodia)
    KO_KR   = "ko"          # Korean (South Korea)
    KO_KP   = "ko_KP"       # Korean (North Korea)
    LB_LU   = "lb"          # Luxembourgish (Luxembourg)
    LO_LA   = "lo"          # Lao (Laos)
    LT_LT   = "lt"          # Lithuanian (Lithuania)
    LV_LV   = "lv"          # Latvian (Latvia)
    MK_MK   = "mk"          # Macedonian (Macedonia)
    ML_IN   = "ml"          # Malayalam (India)
    MN_MN   = "mn"          # Mongolian (Mongolia)
    MS_MY   = "ms"          # Malay (Malaysia)
    MY_MM   = "my"          # Burmese (Myanmar)
    NB_NO   = "nb"          # Norwegian Bokmål (Norway)
    NL_NL   = "nl"          # Dutch (Netherlands)
    NL_BE   = "nl_BE"       # Dutch (Belgium)
    PL_PL   = "pl"          # Polish (Poland)
    PT_PT   = "pt"          # Portuguese (Portugal)
    PT_AO   = "pt_AO"       # Portuguese (Angola)
    PT_BR   = "pt_BR"       # Portuguese (Brazil)
    RO_RO   = "ro"          # Romanian (Romania)
    RU_RU   = "ru"          # Russian (Russia)
    SK_SK   = "sk"          # Slovak (Slovakia)
    SL_SI   = "sl"          # Slovenian (Slovenia)
    SQ_AL   = "sq"          # Albanian (Albania)
    SR_RS   = "sr"          # Serbian (Cyrillic script, Serbia)
    SR_LATN = "sr@latin"    # Serbian (Latin script, Serbia)
    SV_SE   = "sv"          # Swedish (Sweden)
    SW      = "sw"          # Swahili
    TE_IN   = "te"          # Telugu (India)
    TH_TH   = "th"          # Thai (Thailand)
    TL_PH   = "tl"          # Tagalog (Philippines)
    TR_TR   = "tr"          # Turkish (Türkiye)
    UK_UA   = "uk"          # Ukrainian (Ukraine)
    VI_VN   = "vi"          # Vietnamese (Vietnam)
    ZH_CN   = "zh_CN"       # Chinese (Simplified Han script, China)
    ZH_HK   = "zh_HK"       # Chinese (Traditional Han script, Hong Kong)
    ZH_TW   = "zh_TW"       # Chinese (Traditional Han script, Taiwan)


# Plural rules gathered from https://github.com/WeblateOrg/language-data/blob/main/languages.csv
LANG_TO_PLURAL_RULES = {
    Lang.AM_ET  : "nplurals=2; plural=n > 1;",
    Lang.AR_001 : "nplurals=6; plural=n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : n%100>=3 && n%100<=10 ? 3 : n%100>=11 ? 4 : 5;",
    Lang.AR_SY  : "nplurals=6; plural=n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : n%100>=3 && n%100<=10 ? 3 : n%100>=11 ? 4 : 5;",
    Lang.AZ_AZ  : "nplurals=2; plural=n != 1;",
    Lang.BE_BY  : "nplurals=3; plural=n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2;",
    Lang.BG_BG  : "nplurals=2; plural=n != 1;",
    Lang.BN_IN  : "nplurals=2; plural=n > 1;",
    Lang.BS_BA  : "nplurals=3; plural=n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2;",
    Lang.CA_ES  : "nplurals=2; plural=n != 1;",
    Lang.CS_CZ  : "nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;",
    Lang.DA_DK  : "nplurals=2; plural=n != 1;",
    Lang.DE_DE  : "nplurals=2; plural=n != 1;",
    Lang.DE_CH  : "nplurals=2; plural=n != 1;",
    Lang.EL_GR  : "nplurals=2; plural=n != 1;",
    Lang.EN_AU  : "nplurals=2; plural=n != 1;",
    Lang.EN_CA  : "nplurals=2; plural=n != 1;",
    Lang.EN_GB  : "nplurals=2; plural=n != 1;",
    Lang.EN_IN  : "nplurals=2; plural=n != 1;",
    Lang.EN_NZ  : "nplurals=2; plural=n != 1;",
    Lang.ES_ES  : "nplurals=2; plural=n != 1;",
    Lang.ES_419 : "nplurals=2; plural=n != 1;",
    Lang.ES_AR  : "nplurals=2; plural=n != 1;",
    Lang.ES_BO  : "nplurals=2; plural=n != 1;",
    Lang.ES_CL  : "nplurals=2; plural=n != 1;",
    Lang.ES_CO  : "nplurals=2; plural=n != 1;",
    Lang.ES_CR  : "nplurals=2; plural=n != 1;",
    Lang.ES_DO  : "nplurals=2; plural=n != 1;",
    Lang.ES_EC  : "nplurals=2; plural=n != 1;",
    Lang.ES_GT  : "nplurals=2; plural=n != 1;",
    Lang.ES_MX  : "nplurals=2; plural=n != 1;",
    Lang.ES_PA  : "nplurals=2; plural=n != 1;",
    Lang.ES_PE  : "nplurals=2; plural=n != 1;",
    Lang.ES_PY  : "nplurals=2; plural=n != 1;",
    Lang.ES_UY  : "nplurals=2; plural=n != 1;",
    Lang.ES_VE  : "nplurals=2; plural=n != 1;",
    Lang.ET_EE  : "nplurals=2; plural=n != 1;",
    Lang.EU_ES  : "nplurals=2; plural=n != 1;",
    Lang.FA_IR  : "nplurals=2; plural=n > 1;",
    Lang.FI_FI  : "nplurals=2; plural=n != 1;",
    Lang.FR_FR  : "nplurals=2; plural=n > 1;",
    Lang.FR_BE  : "nplurals=2; plural=n > 1;",
    Lang.FR_CA  : "nplurals=2; plural=n > 1;",
    Lang.FR_CH  : "nplurals=2; plural=n > 1;",
    Lang.GL_ES  : "nplurals=2; plural=n != 1;",
    Lang.GU_IN  : "nplurals=2; plural=n > 1;",
    Lang.HE_IL  : "nplurals=4; plural=(n == 1) ? 0 : ((n == 2) ? 1 : ((n > 10 && n % 10 == 0) ? 2 : 3));",
    Lang.HI_IN  : "nplurals=2; plural=n > 1;",
    Lang.HR_HR  : "nplurals=3; plural=n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2;",
    Lang.HU_HU  : "nplurals=2; plural=n != 1;",
    Lang.ID_ID  : "nplurals=1; plural=0;",
    Lang.IT_IT  : "nplurals=2; plural=n != 1;",
    Lang.JA_JP  : "nplurals=1; plural=0;",
    Lang.KA_GE  : "nplurals=2; plural=n != 1;",
    Lang.KAB_DZ : "nplurals=2; plural=n > 1;",
    Lang.KM_KH  : "nplurals=1; plural=0;",
    Lang.KO_KR  : "nplurals=1; plural=0;",
    Lang.KO_KP  : "nplurals=1; plural=0;",
    Lang.LB_LU  : "nplurals=2; plural=n != 1;",
    Lang.LO_LA  : "nplurals=1; plural=0;",
    Lang.LT_LT  : "nplurals=3; plural=(n % 10 == 1 && (n % 100 < 11 || n % 100 > 19)) ? 0 : ((n % 10 >= 2 && n % 10 <= 9 && (n % 100 < 11 || n % 100 > 19)) ? 1 : 2);",
    Lang.LV_LV  : "nplurals=3; plural=(n % 10 == 0 || n % 100 >= 11 && n % 100 <= 19) ? 0 : ((n % 10 == 1 && n % 100 != 11) ? 1 : 2);",
    Lang.MK_MK  : "nplurals=2; plural=n==1 || n%10==1 ? 0 : 1;",
    Lang.ML_IN  : "nplurals=2; plural=n != 1;",
    Lang.MN_MN  : "nplurals=2; plural=n != 1;",
    Lang.MS_MY  : "nplurals=1; plural=0;",
    Lang.MY_MM  : "nplurals=1; plural=0;",
    Lang.NB_NO  : "nplurals=2; plural=n != 1;",
    Lang.NL_NL  : "nplurals=2; plural=n != 1;",
    Lang.NL_BE  : "nplurals=2; plural=n != 1;",
    Lang.PL_PL  : "nplurals=3; plural=n==1 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2;",
    Lang.PT_PT  : "nplurals=2; plural=n > 1;",
    Lang.PT_AO  : "nplurals=2; plural=n > 1;",
    Lang.PT_BR  : "nplurals=2; plural=n > 1;",
    Lang.RO_RO  : "nplurals=3; plural=n==1 ? 0 : (n==0 || (n%100 > 0 && n%100 < 20)) ? 1 : 2;",
    Lang.RU_RU  : "nplurals=3; plural=n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2;",
    Lang.SK_SK  : "nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;",
    Lang.SL_SI  : "nplurals=4; plural=n%100==1 ? 0 : n%100==2 ? 1 : n%100==3 || n%100==4 ? 2 : 3;",
    Lang.SQ_AL  : "nplurals=2; plural=n != 1;",
    Lang.SR_RS  : "nplurals=3; plural=n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2;",
    Lang.SR_LATN: "nplurals=3; plural=n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2;",
    Lang.SV_SE  : "nplurals=2; plural=n != 1;",
    Lang.SW     : "nplurals=2; plural=n != 1;",
    Lang.TE_IN  : "nplurals=2; plural=n != 1;",
    Lang.TH_TH  : "nplurals=1; plural=0;",
    Lang.TL_PH  : "nplurals=2; plural=n != 1 && n != 2 && n != 3 && (n % 10 == 4 || n % 10 == 6 || n % 10 == 9);",
    Lang.TR_TR  : "nplurals=2; plural=n != 1;",
    Lang.UK_UA  : "nplurals=3; plural=n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2;",
    Lang.VI_VN  : "nplurals=1; plural=0;",
    Lang.ZH_CN  : "nplurals=1; plural=0;",
    Lang.ZH_HK  : "nplurals=1; plural=0;",
    Lang.ZH_TW  : "nplurals=1; plural=0;",
}


def update_module_po(
    action: Callable[[Lang, POFile, Path], tuple[bool, RenderableType]],
    module: str,
    languages: list[Lang],
    module_path: Path,
    module_tree: Tree,
) -> Status:
    """Perform an action on a module's .po files for the given languages, using the .pot file.

    :param action: The action to perform on the .po files. A function that takes the language, the .pot file and the
        module's path as parameters, and that returns the success status and a message to render in the `module_tree`.
    :param module: The module whose .po files we're working with.
    :param languages: The languages of the .po files we're working with.
    :param module_path: The path to the module's directory.
    :param module_tree: The visual tree to render the action's messages, or error messages in.
    :return: `Status.SUCCESS` if the `action` succeeded for all .po files, `Status.FAILURE` if the `action` failed for
        every .po file, and `Status.PARTIAL` if the `action` succeeded for some .po files.
    """
    success = failure = False
    pot_file = module_path / "i18n" / f"{module}.pot"
    if not pot_file.is_file():
        module_tree.add("No .pot file found!")
        return Status.FAILURE
    try:
        pot = pofile(pot_file)
    except (OSError, ValueError) as e:
        module_tree.add(get_error_log_panel(str(e), f"Reading {pot_file.name} failed!"))
        return Status.FAILURE

    for lang in TransientProgress().track(languages, description=f"Updating [b]{module}[/b]"):
        result, renderable = action(lang, pot, module_path)
        module_tree.add(renderable)
        success = success or result
        failure = failure or not result

    return Status.PARTIAL if success and failure else Status.SUCCESS if success else Status.FAILURE
