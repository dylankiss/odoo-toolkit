import os
import re
from enum import Enum
from pathlib import Path
from typing import Annotated

import polib
from rich.panel import Panel
from rich.progress import Progress
from rich.tree import Tree
from typer import Argument, Option

from .common import PROGRESS_COLUMNS, app, log, logger


class Lang(str, Enum):
    ALL = "all"
    AM_ET = "am_ET"
    AR_001 = "ar"
    AR_SY = "ar_SY"
    AZ_AZ = "az"
    BE_BY = "be"
    BG_BG = "bg"
    BN_IN = "bn_IN"
    BS_BA = "bs"
    CA_ES = "ca_ES"
    CS_CZ = "cs_CZ"
    DA_DK = "da_DK"
    DE_DE = "de"
    DE_CH = "de_CH"
    EL_GR = "el_GR"
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
    EU_ES = "eu_ES"
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
    KO_KP = "ko_KP"
    KO_KR = "ko_KR"
    LB_LU = "lb"
    LO_LA = "lo"
    LT_LT = "lt"
    LV_LV = "lv"
    MK_MK = "mk"
    ML_IN = "ml"
    MN_MN = "mn_MN"
    MS_MY = "ms"
    MY_MM = "my"
    NB_NO = "nb_NO"
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
    SR_CYRL = "sr@Cyrl"
    SR_LATIN = "sr@latin"
    SV_SE = "sv"
    SW = "sw"
    TE_IN = "te"
    TH_TH = "th"
    TL_PH = "tl"
    TR_TR = "tr"
    UK_UA = "uk"
    VI_VN = "vi"
    ZH_CH = "zh_CH"
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
        Lang.ZH_CH,
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
    "nplurals=3; plural=(n == 1 || (n % 10 == 1 && n % 100 != 11)) ? 0 : ((n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20)) ? 1 : 2);": {
        Lang.SR_CYRL,
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


@app.command()
def create_po(
    modules: Annotated[
        list[str],
        Argument(help='Create .po files for these Odoo modules, or either "all", "community", or "enterprise".'),
    ],
    languages: Annotated[
        list[Lang],
        Option("--languages", "-l", help='Create .po files for these languages, or "all".', case_sensitive=False),
    ] = [Lang.ALL],
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
):
    """
    Create Odoo translation files (.po) according to their .pot files.
    """
    log(
        Panel.fit(
            ":new: Odoo PO Create",
            style="bold magenta",
            border_style="bold magenta",
        ),
        "",
    )

    base_module_path = com_path.expanduser().resolve() / "odoo" / "addons"
    com_modules_path = com_path.expanduser().resolve() / "addons"
    ent_modules_path = ent_path.expanduser().resolve()

    com_modules = {f.parent.name for f in com_modules_path.glob("*/__manifest__.py")}
    ent_modules = {f.parent.name for f in ent_modules_path.glob("*/__manifest__.py")}
    all_modules = {"base"} | com_modules | ent_modules

    # Determine all modules to update.
    if len(modules) == 1 and modules[0] == "all":
        modules_to_update = all_modules
    elif len(modules) == 1 and modules[0] == "community":
        modules_to_update = {"base"} | com_modules
    elif len(modules) == 1 and modules[0] == "enterprise":
        modules_to_update = ent_modules
    elif len(modules) == 1:
        modules_to_update = set(modules[0].split(",")) & all_modules
    else:
        modules_to_update = {re.sub(r",", "", m) for m in modules if m in all_modules}

    if not modules_to_update:
        log(":exclamation_mark: [red]The provided modules are not available! Nothing to update ...\n")
        return

    log(f"Modules to update: [bold]{'[/bold], [bold]'.join(sorted(modules_to_update))}[/bold]\n")

    # Map each module to its directory.
    modules_to_path_mapping = {
        module: path
        for modules, path in [
            ({"base"} & modules_to_update, base_module_path),
            (com_modules & modules_to_update, com_modules_path),
            (ent_modules & modules_to_update, ent_modules_path),
        ]
        for module in modules
    }

    log(Panel.fit(":speech_balloon: [bold]Create Translations"), "")
    modules = sorted(modules_to_update)
    success = failure = False

    # Determine all PO file languages to create.
    if Lang.ALL in languages:
        languages = Lang
    languages = sorted(languages)

    for module in modules:
        create_tree = Tree(f"[bold]{module}")
        i18n_path = modules_to_path_mapping[module] / module / "i18n"
        pot_file = i18n_path / f"{module}.pot"
        if not pot_file.exists():
            failure = True
            create_tree.add("No .pot file found!")
            log(create_tree, "")
            continue
        try:
            pot = polib.pofile(pot_file)
        except OSError as error:
            failure = True
            create_tree.add(
                Panel(
                    str(error),
                    title=f"Reading {pot_file.name} failed!",
                    title_align="left",
                    style="red",
                    border_style="bold red",
                )
            )
            continue

        with Progress(*PROGRESS_COLUMNS, console=logger, transient=True) as progress:
            task = progress.add_task(f"Updating [bold]{module}", total=len(languages))
            for lang in languages:
                try:
                    po_file = i18n_path / f"{lang.value}.po"
                    po = polib.POFile()
                    po.header = pot.header
                    po.metadata = pot.metadata.copy()
                    po.metadata.update({"Language": lang.value, "Plural-Forms": LANG_TO_PLURAL_RULES.get(lang, "")})
                    for entry in pot:
                        po.append(entry)
                    po.save(po_file)
                    success = True
                    create_tree.add(
                        f"[dim]{po_file.parent}{os.sep}[/dim][bold]{po_file.name}[/bold] :white_check_mark:"
                    )
                except OSError as error:
                    failure = True
                    create_tree.add(
                        Panel(
                            str(error),
                            title=f"Creating {po_file.name} failed!",
                            title_align="left",
                            style="red",
                            border_style="bold red",
                        )
                    )
                    continue
                progress.update(task, advance=1)

        log(create_tree, "")

    if not success and failure:
        log(":exclamation_mark: [red]No translation files were created!\n")
    elif success and failure:
        log(":warning: [yellow]Some translation files were created correctly, while others weren't!\n")
    else:
        log(":white_check_mark: [green]All translation files were created correctly!\n")


@app.command()
def update_po(
    modules: Annotated[
        list[str],
        Argument(help='Update .po files for these Odoo modules, or either "all", "community", or "enterprise".'),
    ],
    languages: Annotated[
        list[Lang],
        Option("--languages", "-l", help='Update .po files for these languages, or "all".', case_sensitive=False),
    ] = [Lang.ALL],
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
):
    """
    Update Odoo translation files (.po) according to a new version of their .pot files.
    """
    log(
        Panel.fit(
            ":arrows_counterclockwise: Odoo PO Update",
            style="bold magenta",
            border_style="bold magenta",
        ),
        "",
    )

    base_module_path = com_path.expanduser().resolve() / "odoo" / "addons"
    com_modules_path = com_path.expanduser().resolve() / "addons"
    ent_modules_path = ent_path.expanduser().resolve()

    com_modules = {f.parent.name for f in com_modules_path.glob("*/__manifest__.py")}
    ent_modules = {f.parent.name for f in ent_modules_path.glob("*/__manifest__.py")}
    all_modules = {"base"} | com_modules | ent_modules

    # Determine all modules to update.
    if len(modules) == 1 and modules[0] == "all":
        modules_to_update = all_modules
    elif len(modules) == 1 and modules[0] == "community":
        modules_to_update = {"base"} | com_modules
    elif len(modules) == 1 and modules[0] == "enterprise":
        modules_to_update = ent_modules
    elif len(modules) == 1:
        modules_to_update = set(modules[0].split(",")) & all_modules
    else:
        modules_to_update = {re.sub(r",", "", m) for m in modules if m in all_modules}

    if not modules_to_update:
        log(":exclamation_mark: [red]The provided modules are not available! Nothing to update ...\n")
        return

    log(f"Modules to update: [bold]{'[/bold], [bold]'.join(sorted(modules_to_update))}[/bold]\n")

    # Map each module to its directory.
    modules_to_path_mapping = {
        module: path
        for modules, path in [
            ({"base"} & modules_to_update, base_module_path),
            (com_modules & modules_to_update, com_modules_path),
            (ent_modules & modules_to_update, ent_modules_path),
        ]
        for module in modules
    }

    log(Panel.fit(":speech_balloon: [bold]Update Translations"), "")
    modules = sorted(modules_to_update)
    success = failure = False

    # Determine all PO files to update.
    if Lang.ALL in languages:
        languages = Lang
    languages = sorted(languages)

    for module in modules:
        update_tree = Tree(f"[bold]{module}")
        i18n_path = modules_to_path_mapping[module] / module / "i18n"
        pot_file = i18n_path / f"{module}.pot"
        if not pot_file.exists():
            failure = True
            update_tree.add("No .pot file found!")
            log(update_tree, "")
            continue
        try:
            pot = polib.pofile(pot_file)
        except OSError as error:
            failure = True
            update_tree.add(
                Panel(
                    str(error),
                    title=f"Reading {pot_file.name} failed!",
                    title_align="left",
                    style="red",
                    border_style="bold red",
                )
            )
            continue
        langs_to_update = [lang for lang in languages if (i18n_path / f"{lang.value}.po").exists()]
        if not langs_to_update:
            update_tree.add("No .po files found!")
            log(update_tree, "")
            continue
        with Progress(*PROGRESS_COLUMNS, console=logger, transient=True) as progress:
            task = progress.add_task(f"Updating [bold]{module}", total=len(langs_to_update))
            for lang in langs_to_update:
                try:
                    po_file = i18n_path / f"{lang.value}.po"
                    po = polib.pofile(po_file)
                    po.header = pot.header
                    po.metadata.update({"Language": lang.value, "Plural-Forms": LANG_TO_PLURAL_RULES.get(lang, "")})
                    # Remove entries that are obsolete, fuzzy, or not in the POT file.
                    po[:] = [entry for entry in po if entry in pot and not entry.obsolete and not entry.fuzzy]
                    # Add entries in the POT file that are not in the PO file yet.
                    for entry in pot:
                        if entry not in po:
                            po.append(entry)
                    # Sort the entries before saving.
                    po.sort(key=lambda entry: (entry.msgid, entry.msgctxt or ""))
                    po.save()
                    success = True
                    update_tree.add(
                        f"[dim]{po_file.parent}{os.sep}[/dim][bold]{po_file.name}[/bold] :white_check_mark:"
                    )
                except OSError as error:
                    failure = True
                    update_tree.add(
                        Panel(
                            str(error),
                            title=f"Updating {po_file.name} failed!",
                            title_align="left",
                            style="red",
                            border_style="bold red",
                        )
                    )
                    continue
                progress.update(task, advance=1)

        log(update_tree, "")

    if not success and failure:
        log(":exclamation_mark: [red]No translation files were updated!\n")
    elif success and failure:
        log(":warning: [yellow]Some translation files were updated correctly, while others weren't!\n")
    else:
        log(":white_check_mark: [green]All translation files were updated correctly!\n")
