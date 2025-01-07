from collections.abc import Callable
from enum import Enum
from pathlib import Path

from polib import POFile, pofile
from rich.console import RenderableType
from rich.tree import Tree

from odoo_toolkit.common import Status, TransientProgress, get_error_log_panel


class Lang(str, Enum):
    """Languages available in Odoo."""

    ALL = "all"
    AM_ET = "am"
    AR_001 = "ar"
    AR_SY = "ar_SY"
    AZ_AZ = "az"
    BE_BY = "be"
    BG_BG = "bg"
    BN_IN = "bn"
    BS_BA = "bs"
    CA_ES = "ca"
    CS_CZ = "cs"
    DA_DK = "da"
    DE_DE = "de"
    DE_CH = "de_CH"
    EL_GR = "el"
    EN_AU = "en_AU"
    EN_CA = "en_CA"
    EN_GB = "en_GB"
    EN_IN = "en_IN"
    EN_NZ = "en_NZ"
    ES_ES = "es"
    ES_419 = "es_419"
    ES_AR = "es_AR"
    ES_BO = "es_BO"
    ES_CL = "es_CL"
    ES_CO = "es_CO"
    ES_CR = "es_CR"
    ES_DO = "es_DO"
    ES_EC = "es_EC"
    ES_GT = "es_GT"
    ES_MX = "es_MX"
    ES_PA = "es_PA"
    ES_PE = "es_PE"
    ES_PY = "es_PY"
    ES_UY = "es_UY"
    ES_VE = "es_VE"
    ET_EE = "et"
    EU_ES = "eu"
    FA_IR = "fa"
    FI_FI = "fi"
    FR_FR = "fr"
    FR_BE = "fr_BE"
    FR_CA = "fr_CA"
    FR_CH = "fr_CH"
    GL_ES = "gl"
    GU_IN = "gu"
    HE_IL = "he"
    HI_IN = "hi"
    HR_HR = "hr"
    HU_HU = "hu"
    ID_ID = "id"
    IT_IT = "it"
    JA_JP = "ja"
    KA_GE = "ka"
    KAB_DZ = "kab"
    KM_KH = "km"
    KO_KR = "ko"
    KO_KP = "ko_KP"
    LB_LU = "lb"
    LO_LA = "lo"
    LT_LT = "lt"
    LV_LV = "lv"
    MK_MK = "mk"
    ML_IN = "ml"
    MN_MN = "mn"
    MS_MY = "ms"
    MY_MM = "my"
    NB_NO = "nb"
    NL_NL = "nl"
    NL_BE = "nl_BE"
    PL_PL = "pl"
    PT_PT = "pt"
    PT_AO = "pt_AO"
    PT_BR = "pt_BR"
    RO_RO = "ro"
    RU_RU = "ru"
    SK_SK = "sk"
    SL_SI = "sl"
    SQ_AL = "sq"
    SR_RS = "sr"
    SR_LATIN = "sr@latin"
    SV_SE = "sv"
    SW = "sw"
    TE_IN = "te"
    TH_TH = "th"
    TL_PH = "tl"
    TR_TR = "tr"
    UK_UA = "uk"
    VI_VN = "vi"
    ZH_CN = "zh_CN"
    ZH_HK = "zh_HK"
    ZH_TW = "zh_TW"


PLURAL_RULES_TO_LANGS = {
    "nplurals=1; plural=0;": {
        Lang.ID_ID,
        Lang.JA_JP,
        Lang.KA_GE,
        Lang.KM_KH,
        Lang.KO_KP,
        Lang.KO_KR,
        Lang.LO_LA,
        Lang.MS_MY,
        Lang.MY_MM,
        Lang.TH_TH,
        Lang.VI_VN,
        Lang.ZH_CN,
        Lang.ZH_HK,
        Lang.ZH_TW,
    },
    "nplurals=2; plural=(n != 1);": {
        Lang.AZ_AZ,
        Lang.BG_BG,
        Lang.BN_IN,
        Lang.CA_ES,
        Lang.DA_DK,
        Lang.DE_DE,
        Lang.DE_CH,
        Lang.EL_GR,
        Lang.EN_AU,
        Lang.EN_CA,
        Lang.EN_GB,
        Lang.EN_IN,
        Lang.EN_NZ,
        Lang.ES_ES,
        Lang.ES_419,
        Lang.ES_AR,
        Lang.ES_BO,
        Lang.ES_CL,
        Lang.ES_CO,
        Lang.ES_CR,
        Lang.ES_DO,
        Lang.ES_EC,
        Lang.ES_GT,
        Lang.ES_MX,
        Lang.ES_PA,
        Lang.ES_PE,
        Lang.ES_PY,
        Lang.ES_UY,
        Lang.ES_VE,
        Lang.EU_ES,
        Lang.FI_FI,
        Lang.GL_ES,
        Lang.GU_IN,
        Lang.HE_IL,
        Lang.HI_IN,
        Lang.HU_HU,
        Lang.IT_IT,
        Lang.KAB_DZ,
        Lang.LB_LU,
        Lang.ML_IN,
        Lang.MN_MN,
        Lang.NB_NO,
        Lang.NL_NL,
        Lang.NL_BE,
        Lang.PT_PT,
        Lang.PT_AO,
        Lang.PT_BR,
        Lang.SQ_AL,
        Lang.SV_SE,
        Lang.SW,
        Lang.TE_IN,
    },
    "nplurals=2; plural=(n > 1);": {
        Lang.AM_ET,
        Lang.FA_IR,
        Lang.FR_FR,
        Lang.FR_BE,
        Lang.FR_CA,
        Lang.FR_CH,
        Lang.TL_PH,
        Lang.TR_TR,
    },
    "nplurals=2; plural= n==1 || n%10==1 ? 0 : 1;": {
        Lang.MK_MK,
    },
    "nplurals=3; plural=(n==1) ? 0 : (n>=2 && n<=4) ? 1 : 2;": {
        Lang.CS_CZ,
        Lang.SK_SK,
    },
    "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n != 0 ? 1 : 2);": {
        Lang.LV_LV,
    },
    "nplurals=3; plural=(n==1 ? 0 : (n==0 || (n%100 > 0 && n%100 < 20)) ? 1 : 2);": {
        Lang.RO_RO,
    },
    "nplurals=3; plural=(n==1 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);": {
        Lang.PL_PL,
    },
    "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && (n%100<10 || n%100>=20) ? 1 : 2);": {
        Lang.LT_LT,
    },
    "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);": {
        Lang.BE_BY,
        Lang.BS_BA,
        Lang.HR_HR,
        Lang.RU_RU,
        Lang.UK_UA,
    },
    "nplurals=3; plural=(n == 1 || (n % 10 == 1 && n % 100 != 11)) ? 0 : ((n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20)) ? 1 : 2);": {  # noqa: E501
        Lang.SR_RS,
        Lang.SR_LATIN,
    },
    "nplurals=4; plural=(n%100==1 ? 0 : n%100==2 ? 1 : n%100==3 || n%100==4 ? 2 : 3);": {
        Lang.SL_SI,
    },
    "nplurals=6; plural=(n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : n%100>=3 && n%100<=10 ? 3 : n%100>=11 ? 4 : 5);": {
        Lang.AR_001,
        Lang.AR_SY,
    },
}
LANG_TO_PLURAL_RULES = {lang: plural_rules for plural_rules, langs in PLURAL_RULES_TO_LANGS.items() for lang in langs}


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
    :type action: Callable[
        [:class:`Lang`, :class:`polib.POFile`, :class:`pathlib.Path`],
        tuple[bool, :class:`rich.console.RenderableType`],
    ]
    :param module: The module whose .po files we're working with.
    :type module: str
    :param languages: The languages of the .po files we're working with.
    :type languages: list[:class:`Lang`]
    :param module_path: The path to the module's directory.
    :type module_path: :class:`pathlib.Path`
    :param module_tree: The visual tree to render the action's messages, or error messages in.
    :type module_tree: :class:`rich.tree.Tree`
    :return: `Status.SUCCESS` if the `action` succeeded for all .po files, `Status.FAILURE` if the `action` failed for
        every .po file, and `Status.PARTIAL` if the `action` succeeded for some .po files.
    :rtype: :class:`odoo_toolkit.common.Status`
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
