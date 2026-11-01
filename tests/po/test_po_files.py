import shutil
import subprocess
from pathlib import Path

import pytest
from polib import pofile
from rich.tree import Tree
from typer.testing import CliRunner

from odoo_toolkit.main import app
from odoo_toolkit.po.common import get_cldr_lang, get_plural_forms, update_module_po
from odoo_toolkit.po.update import get_retry_command, is_translated

PO_FILE = r"""msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\n"

#, fuzzy
msgid "fuzzy"
msgstr "flou"

msgid "empty"
msgstr ""

msgid "one"
msgid_plural "many"
msgstr[0] "un"
msgstr[1] ""

msgid "one2"
msgid_plural "many2"
msgstr[0] ""
msgstr[1] "des"

msgid "ok"
msgstr "bien"
"""
TRANSLATED = ["fuzzy", "one", "ok"]
POT_FILE = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\nmsgid "Hello"\nmsgstr ""\n'


def test_is_translated_keeps_what_msgattrib_keeps(tmp_path: Path) -> None:
    po_path = tmp_path / "fr.po"
    po_path.write_text(PO_FILE)

    assert [entry.msgid for entry in pofile(str(po_path)) if is_translated(entry)] == TRANSLATED


@pytest.mark.skipif(not shutil.which("msgattrib"), reason="gettext isn't installed")
def test_msgattrib_keeps_the_same_entries(tmp_path: Path) -> None:
    po_path = tmp_path / "fr.po"
    po_path.write_text(PO_FILE)

    output = subprocess.run(["msgattrib", "--translated", str(po_path)], capture_output=True, text=True, check=True)

    assert [entry.msgid for entry in pofile(output.stdout) if entry.msgid] == TRANSLATED


def test_modules_without_po_files_are_not_a_failure(tmp_path: Path) -> None:
    (tmp_path / "i18n").mkdir()
    (tmp_path / "i18n" / "my_module.pot").write_text(POT_FILE)

    def fail(*_: object) -> tuple[bool, str]:
        raise AssertionError

    failures = update_module_po(
        action=fail,
        module="my_module",
        languages=[],
        module_path=tmp_path,
        module_tree=Tree("my_module"),
        description="Updating",
    )

    # Nothing to do, which isn't a failure.
    assert failures is None


def test_retry_command_repeats_every_option() -> None:
    command = get_retry_command(
        modules=["sale"],
        languages=["fr"],
        only_translated=True,
        exclude=["*test*"],
        path_filters=[Path("my addons")],
        com_path=Path("odoo"),
        ent_path=Path("../enterprise"),
        extra_addons_paths=[Path("extra")],
    )

    assert command == [
        "otk",
        "po",
        "update",
        "sale",
        "-l",
        "fr",
        "-t",
        "-x",
        "*test*",
        "-f",
        "my addons",
        "-e",
        "../enterprise",
        "-a",
        "extra",
    ]


def test_cli_create_reports_the_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = tmp_path / "odoo" / "addons" / "my_module"
    (module / "i18n").mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}")
    (module / "i18n" / "my_module.pot").write_text(POT_FILE)
    (tmp_path / "enterprise").mkdir()
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["po", "create", "-l", "fr", "my_module"])

    assert result.exit_code == 0, result.output
    assert "All translation files were created correctly" in result.output
    assert (module / "i18n" / "fr.po").is_file()


def test_cli_update_skips_modules_without_pot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for module in ("with_pot", "without_pot"):
        (tmp_path / "odoo" / "addons" / module / "i18n").mkdir(parents=True)
        (tmp_path / "odoo" / "addons" / module / "__manifest__.py").write_text("{}")
    (tmp_path / "odoo" / "addons" / "with_pot" / "i18n" / "with_pot.pot").write_text(POT_FILE)
    (tmp_path / "odoo" / "addons" / "with_pot" / "i18n" / "fr.po").write_text(POT_FILE)
    (tmp_path / "enterprise").mkdir()
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["po", "update", "all"])

    assert result.exit_code == 0, result.output
    assert "All translation files were updated correctly" in result.output


@pytest.mark.parametrize("gettext", [True, False])
def test_cli_create_uses_the_plural_rules_of_weblate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    gettext: bool,
) -> None:
    if gettext and not shutil.which("msginit"):
        pytest.skip("gettext isn't installed")
    if not gettext:

        def not_found(_: str) -> None:
            return None

        monkeypatch.setattr(shutil, "which", not_found)
    module = tmp_path / "odoo" / "addons" / "my_module"
    (module / "i18n").mkdir(parents=True)
    (module / "__manifest__.py").write_text("{}")
    (module / "i18n" / "my_module.pot").write_text(
        POT_FILE + '\nmsgid "One"\nmsgid_plural "Many"\nmsgstr[0] ""\nmsgstr[1] ""\n',
    )
    (tmp_path / "enterprise").mkdir()
    monkeypatch.chdir(tmp_path)

    assert CliRunner().invoke(app, ["po", "create", "-l", "fr", "my_module"]).exit_code == 0

    # gettext's own rule for French is `nplurals=2; plural=(n > 1);`, while CLDR has a third form.
    po = pofile(str(module / "i18n" / "fr.po"))
    assert po.metadata["Plural-Forms"] == get_plural_forms("fr")
    assert [len(entry.msgstr_plural) for entry in po if entry.msgid_plural] == [3]


def test_language_codes_of_weblate() -> None:
    assert get_cldr_lang("zh_CN") == "zh_Hans"
    assert get_cldr_lang("sr@latin") == "sr_Latn"
    assert get_cldr_lang("fr") == "fr"
