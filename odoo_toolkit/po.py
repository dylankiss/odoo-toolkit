import os
import re
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal

import polib
from rich.console import RenderableType
from rich.tree import Tree
from typer import Argument, Exit, Option

from .common import (
    TransientProgress,
    app,
    get_error_log_panel,
    print,
    print_command_title,
    print_error,
    print_header,
    print_success,
    print_warning,
)


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


@app.command()
def create_po(
    modules: Annotated[
        list[str],
        Argument(help='Create .po files for these Odoo modules, or either "all", "community", or "enterprise".'),
    ],
    languages: Annotated[
        list[Lang],
        Option("--languages", "-l", help='Create .po files for these languages, or "all".', case_sensitive=False),
    ],
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
) -> None:
    """Create Odoo translation files (.po) according to their .pot files.

    This command will provide you with a clean .po file per language you specified for the given modules. It basically
    copies all entries from the .pot file in the module and completes the metadata with the right language information.
    All generated .po files will be saved in the respective modules' `i18n` directories.
    \n\n
    > Without any options specified, the command is supposed to run from within the parent directory where your `odoo`
    and `enterprise` repositories are checked out with these names.
    """
    print_command_title(":memo: Odoo PO Create")

    modules_to_update, modules_to_path_mapping = _determine_modules_and_path_mapping(
        modules=modules,
        com_path=com_path,
        ent_path=ent_path,
    )
    if not modules_to_update:
        print_error("The provided modules are not available! Nothing to create ...")
        raise Exit
    print(f"Modules to create translation files for: [b]{'[/b], [b]'.join(sorted(modules_to_update))}[/b]\n")

    print_header(":speech_balloon: Create Translation Files")

    modules = sorted(modules_to_update)
    success = failure = False

    # Determine all PO file languages to create.
    if Lang.ALL in languages:
        languages = Lang
    languages = sorted(languages)

    for module in modules:
        result = _create_or_update_po_files_for_module(
            module=module,
            languages=languages,
            modules_to_path_mapping=modules_to_path_mapping,
            mode="create",
        )
        success = success or result in (True, None)
        failure = failure or result in (False, None)

    if not success and failure:
        print_error("No translation files were created!\n")
    elif success and failure:
        print_warning("Some translation files were created correctly, while others weren't!\n")
    else:
        print_success("All translation files were created correctly!\n")


@app.command()
def update_po(
    modules: Annotated[
        list[str],
        Argument(help='Update .po files for these Odoo modules, or either "all", "community", or "enterprise".'),
    ],
    languages: Annotated[
        list[Lang],
        Option("--languages", "-l", help='Update .po files for these languages, or "all".', case_sensitive=False),
    ] = [Lang.ALL],  # noqa: B006
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
) -> None:
    """Update Odoo translation files (.po) according to a new version of their .pot files.

    This command will update the .po files for the provided modules according to a new .pot file you might have exported
    in their `i18n` directory.
    \n\n
    > Without any options specified, the command is supposed to run from within the parent directory where your `odoo`
    and `enterprise` repositories are checked out with these names.
    """
    print_command_title(":arrows_counterclockwise: Odoo PO Update")

    modules_to_update, modules_to_path_mapping = _determine_modules_and_path_mapping(
        modules=modules,
        com_path=com_path,
        ent_path=ent_path,
    )
    if not modules_to_update:
        print_error("The provided modules are not available! Nothing to update ...")
        raise Exit
    print(f"Modules to update translation files for: [b]{'[/b], [b]'.join(sorted(modules_to_update))}[/b]\n")

    print_header(":speech_balloon: Update Translation Files")

    modules = sorted(modules_to_update)
    success = failure = False

    # Determine all PO files to update.
    if Lang.ALL in languages:
        languages = Lang
    languages = sorted(languages)

    for module in modules:
        result = _create_or_update_po_files_for_module(
            module=module,
            languages=languages,
            modules_to_path_mapping=modules_to_path_mapping,
            mode="update",
        )
        success = success or result in (True, None)
        failure = failure or result in (False, None)

    if not success and failure:
        print_error("No translation files were updated!\n")
    elif success and failure:
        print_warning("Some translation files were updated correctly, while others weren't!\n")
    else:
        print_success("All translation files were updated correctly!\n")


@app.command()
def merge_po(
    po_files: Annotated[list[Path], Argument(help="Merge these .po files together.")],
    output_file: Annotated[Path, Option("--output-file", "-o", help="Specify the output .po file.")] = Path(
        "merged.po",
    ),
    overwrite: Annotated[bool, Option("--overwrite", help="Overwrite existing translations.")] = False,
) -> None:
    """Merge multiple translation files (.po) into one.

    The order of the files determines which translation takes priority. Empty translations in earlier files will be
    completed with translations from later files, taking the first one in the order they occur.
    \n\n
    If the option `--overwrite` is active, existing translations in earlier files will always be overwritten by
    translations in later files. In that case the last file takes precedence.
    \n\n
    The .po metadata is taken from the first file by default, or the last if `--overwrite` is active.
    """
    print_command_title(":shuffle_tracks_button: Odoo PO Merge")

    if len(po_files) < 2:  # noqa: PLR2004
        print_error("You need at least two .po files to merge them.")
        raise Exit

    for po_file in po_files:
        if not po_file.is_file():
            print_error(f"The provided file [b]{po_file}[/b] does not exist or is not a file.")
            raise Exit

    print(
        f"Merging files [b]{' ← '.join(str(po_file) for po_file in po_files)}[/b]"
        f"{', overwriting translations.' if overwrite else '.'}\n",
    )

    merged_po = polib.POFile()
    with TransientProgress() as progress:
        progress_task = progress.add_task("Merging files ...", total=len(po_files))
        try:
            for po_file in po_files:
                po = polib.pofile(po_file)
                if po.metadata and (not merged_po.metadata or overwrite):
                    merged_po.metadata = po.metadata
                merged_po = _merge_second_po_into_first(merged_po, po)
                progress.update(progress_task, advance=1)

            merged_po.sort(key=lambda entry: (entry.msgid, entry.msgctxt or ""))
            merged_po.save(output_file)
        except OSError as e:
            print_error("Merging .po files failed.", str(e))
            raise Exit from e

    print_success(
        f"The files were successfully merged into [b]{output_file}[/b] ({merged_po.percent_translated()}% translated)",
    )


def _merge_second_po_into_first(
    first_po: polib.POFile, second_po: polib.POFile, overwrite: bool = False,
) -> polib.POFile:
    """Merge the second .po file into the first, without considering order.

    :param first_po: The first .po file, that will be modified by the second
    :type first_po: polib.POFile
    :param second_po: The second .po file, that will be merged into the first
    :type second_po: polib.POFile
    :param overwrite: Whether to overwrite translations in the first file by ones in the second, defaults to False
    :type overwrite: bool, optional
    :return: The merged .po file
    :rtype: polib.POFile
    """
    for entry in second_po:
        if entry.obsolete or entry.fuzzy:
            continue
        existing_entry = first_po.find(entry.msgid, entry.msgctxt)
        if existing_entry:
            if entry.msgstr and (not existing_entry.msgstr or overwrite):
                existing_entry.msgstr = entry.msgstr
            if entry.msgstr_plural and (not existing_entry.msgstr_plural or overwrite):
                existing_entry.msgstr_plural = entry.msgstr_plural
        else:
            first_po.append(entry)
    return first_po



def _determine_modules_and_path_mapping(
    modules: list[str],
    com_path: Path,
    ent_path: Path,
) -> tuple[list[str], dict[str, Path]]:
    """Determine the modules to consider and their addons directories.

    :param modules: The requested list of modules to update
    :type modules: list[str]
    :param com_path: The Odoo Community repository
    :type com_path: Path
    :param ent_path: The Odoo Enterprise repository
    :type ent_path: Path
    :raises Exit: If there are no modules to update
    :return: A tuple containing the modules to update, and the mapping to their addons directory
    :rtype: tuple[list[str], dict[str, Path]]
    """
    base_module_path = com_path.expanduser().resolve() / "odoo" / "addons"
    com_modules_path = com_path.expanduser().resolve() / "addons"
    ent_modules_path = ent_path.expanduser().resolve()

    com_modules = {f.parent.name for f in com_modules_path.glob("*/__manifest__.py")}
    ent_modules = {f.parent.name for f in ent_modules_path.glob("*/__manifest__.py")}
    all_modules = {"base"} | com_modules | ent_modules

    # Determine all modules to consider.
    if len(modules) == 1:
        match modules[0]:
            case "all":
                modules_to_consider = all_modules
            case "community":
                modules_to_consider = {"base"} | com_modules
            case "enterprise":
                modules_to_consider = ent_modules
            case _:
                modules_to_consider = set(modules[0].split(",")) & all_modules
    else:
        modules_to_consider = {re.sub(r",", "", m) for m in modules if m in all_modules}

    if not modules_to_consider:
        return [], {}

    # Map each module to its addons directory.
    modules_to_path_mapping = {
        module: path
        for modules, path in [
            ({"base"} & modules_to_consider, base_module_path),
            (com_modules & modules_to_consider, com_modules_path),
            (ent_modules & modules_to_consider, ent_modules_path),
        ]
        for module in modules
    }

    return modules_to_consider, modules_to_path_mapping


def _create_or_update_po_files_for_module(
    module: str,
    languages: list[Lang],
    modules_to_path_mapping: dict[str, Path],
    mode: Literal["create", "update"],
) -> bool | None:
    """Create or update .po files for the given module and languages.

    :param module: The module to create or update .po files for
    :type module: str
    :param languages: The languages to create or update .po files for
    :type languages: list[Lang]
    :param modules_to_path_mapping: The mapping from every module to its addons directory
    :type modules_to_path_mapping: dict[str, Path]
    :return: `True` if all .po files were created/updated correctly, `False` if none were, and `None` if some were
    :rtype: bool | None
    """
    success = failure = False
    po_tree = Tree(f"[b]{module}[/b]")
    i18n_path = modules_to_path_mapping[module] / module / "i18n"
    pot_file = i18n_path / f"{module}.pot"
    if not pot_file.exists():
        po_tree.add("No .pot file found!")
        print(po_tree, "")
        return False
    try:
        pot = polib.pofile(pot_file)
    except OSError as e:
        po_tree.add(get_error_log_panel(str(e), f"Reading {pot_file.name} failed!"))
        print(po_tree, "")
        return False

    with TransientProgress() as progress:
        progress_task = progress.add_task(f"Updating [b]{module}[/b]", total=len(languages))
        for lang in languages:
            if mode == "create":
                result, renderable = _create_po_for_lang(lang, pot)
            elif mode == "update":
                result, renderable = _update_po_for_lang(lang, pot)
            else:
                raise Exit
            po_tree.add(renderable)
            success = success or result
            failure = failure or not result
            progress.update(progress_task, advance=1)

    print(po_tree, "")
    return None if success and failure else success and not failure


def _create_po_for_lang(lang: Lang, pot: polib.POFile) -> tuple[bool, RenderableType]:
    """Create a .po file for the given language and .pot file.

    :param lang: The language to create the .po file for
    :type lang: Lang
    :param pot: The .pot file to get the terms from
    :type pot: polib.POFile
    :return: A tuple containing `True` if the creation succeeded and `False` if it didn't,
        and the message to print to the console
    :rtype: tuple[bool, RenderableType]
    """
    try:
        po_file = Path(pot.fpath).parent / f"{lang.value}.po"
        po = polib.POFile()
        po.header = pot.header
        po.metadata = pot.metadata.copy()
        # Set the correct language and plural forms in the PO file.
        po.metadata.update({"Language": lang.value, "Plural-Forms": LANG_TO_PLURAL_RULES.get(lang, "")})
        for entry in pot:
            # Just add all entries in the POT to the PO file.
            po.append(entry)
        po.save(po_file)
    except OSError as e:
        return False, get_error_log_panel(str(e), f"Creating {po_file.name} failed!")
    else:
        return True, f"[d]{po_file.parent}{os.sep}[/d][b]{po_file.name}[/b] :white_check_mark:"


def _update_po_for_lang(lang: Lang, pot: polib.POFile) -> tuple[bool, RenderableType]:
    """Update a .po file for the given language and .pot file.

    :param lang: The language to update the .po file for
    :type lang: Lang
    :param pot: The .pot file to get the terms from
    :type pot: polib.POFile
    :return: A tuple containing `True` if the update succeeded and `False` if it didn't,
        and the message to print to the console
    :rtype: tuple[bool, RenderableType]
    """
    try:
        po_file = Path(pot.fpath).parent / f"{lang.value}.po"
        po = polib.POFile(po_file)
        # Update the PO header and metadata.
        po.header = pot.header
        po.metadata.update({"Language": lang.value, "Plural-Forms": LANG_TO_PLURAL_RULES.get(lang, "")})
        # Merge the PO file with the POT file to update all terms.
        po.merge(pot)
        # Remove entries that are obsolete or fuzzy.
        po[:] = [entry for entry in po if not entry.obsolete and not entry.fuzzy]
        # Sort the entries before saving, in the same way as `msgmerge -s`.
        po.sort(key=lambda entry: (entry.msgid, entry.msgctxt or ""))
        po.save()
    except OSError as e:
        return False, get_error_log_panel(str(e), f"Updating {po_file.name} failed!")
    else:
        return True, f"[d]{po_file.parent}{os.sep}[/d][b]{po_file.name}[/b] :white_check_mark:"
