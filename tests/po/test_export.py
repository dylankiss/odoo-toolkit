import sys
import time
from pathlib import Path

import pytest
from rich.progress import Progress
from typer.testing import CliRunner

from odoo_toolkit.main import app
from odoo_toolkit.po import export
from odoo_toolkit.po.export import DEFAULT_EXCLUDE


def test_default_excludes_keep_the_other_excludes() -> None:
    assert export.resolve_excludes(["default", "my_module"]) == ["my_module", *DEFAULT_EXCLUDE]
    assert export.resolve_excludes(["my_module"]) == ["my_module"]


def make_module(addons: Path, name: str) -> Path:
    module = addons / name
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text("{'depends': []}")
    return module


def test_full_install_keeps_the_modules_of_extra_addons_paths(tmp_path: Path) -> None:
    com, ent, extra = tmp_path / "odoo", tmp_path / "enterprise", tmp_path / "extra"
    make_module(com / "addons", "sale")
    make_module(ent, "helpdesk")
    custom = make_module(extra, "my_module")

    modules = export.get_modules_per_server_type(
        module_to_path={"my_module": custom},
        com_path=com,
        ent_path=ent,
        extra_addons_paths=[extra],
        full_install=True,
    )

    to_export, to_install = modules[export.ServerType.CUSTOM]
    assert to_export == {"my_module"}
    assert to_install == {"my_module", "sale", "helpdesk"}


def test_server_stops_when_exporting_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(**_: object) -> None:
        raise ConnectionRefusedError

    monkeypatch.setattr(export, "_export_module_terms", fail)
    # A server that is ready, and would run forever.
    server = "import sys, time; print('Modules loaded.', file=sys.stderr, flush=True); time.sleep(60)"

    start = time.monotonic()
    with Progress(disable=True) as progress:
        exported = export.run_server_and_export_terms(
            server_name="custom",
            server_formatted="[CUSTOM]",
            odoo_cmd=[sys.executable, "-c", server],
            stop_cmd=None,
            dropdb_cmd=None,
            env={},
            url="http://localhost:1",
            database="db",
            username="admin",
            password="admin",  # noqa: S106
            module_to_path={},
            progress=progress,
        )

    assert time.monotonic() - start < 30
    assert exported is False


def test_unreadable_manifests_are_skipped(tmp_path: Path) -> None:
    com, ent = tmp_path / "odoo", tmp_path / "enterprise"
    sale = make_module(com / "addons", "sale")
    (make_module(com / "addons", "broken") / "__manifest__.py").write_text("{'depends': [")
    ent.mkdir()

    modules = export.get_modules_per_server_type(module_to_path={"sale": sale}, com_path=com, ent_path=ent)

    assert modules[export.ServerType.COM][0] == {"sale"}


def test_cli_rejects_full_and_quick_install_together() -> None:
    result = CliRunner().invoke(app, ["po", "export", "--full-install", "--quick-install", "sale"])

    assert result.exit_code == 1
    assert "not both" in result.output
