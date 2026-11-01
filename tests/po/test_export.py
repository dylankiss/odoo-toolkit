import sys
import time
from pathlib import Path

import pytest
from rich.progress import Progress
from typer.testing import CliRunner

from odoo_toolkit.dev import runtime
from odoo_toolkit.main import app
from odoo_toolkit.po import export
from odoo_toolkit.po.common import DEFAULT_EXCLUDE, select_modules


def make_module(addons: Path, name: str, depends: tuple[str, ...] = ()) -> Path:
    module = addons / name
    module.mkdir(parents=True)
    (module / "__manifest__.py").write_text(f"{{'depends': {list(depends)!r}}}")
    return module


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path]:
    com, ent = tmp_path / "odoo", tmp_path / "enterprise"
    make_module(com / "addons", "sale")
    make_module(com / "addons", "sale_test", ("sale",))
    make_module(com / "addons", "my_module")
    ent.mkdir()
    return com, ent


def test_default_excludes_keep_the_other_excludes(repos: tuple[Path, Path]) -> None:
    com, ent = repos
    selection = select_modules(
        ["all"],
        exclude=["default,my_module"],
        path_filters=[],
        com_path=com,
        ent_path=ent,
        extra_addons_paths=[],
    )

    assert set(selection.module_to_path) == {"sale"}
    assert not selection.include_module("my_module")
    assert all(not selection.include_module(pattern.strip("*") + "x") for pattern in ["*test*"])
    assert "*test*" in DEFAULT_EXCLUDE


def test_base_is_only_selected_if_it_exists(repos: tuple[Path, Path]) -> None:
    com, ent = repos
    selection = select_modules(
        ["all"],
        exclude=[],
        path_filters=[],
        com_path=com,
        ent_path=ent,
        extra_addons_paths=[],
    )
    assert "base" not in selection.module_to_path


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


def test_quick_install_of_base_only_installs_base(tmp_path: Path) -> None:
    com, ent = tmp_path / "odoo", tmp_path / "enterprise"
    make_module(com / "addons", "sale")
    base = make_module(com / "odoo" / "addons", "base")
    ent.mkdir()

    modules = export.get_modules_per_server_type(
        module_to_path={"base": base},
        com_path=com,
        ent_path=ent,
        quick_install=True,
    )

    assert modules[export.ServerType.CUSTOM] == ({"base"}, {"base"})


def test_find_dependents(tmp_path: Path) -> None:
    addons = tmp_path / "addons"
    make_module(addons, "sale")
    make_module(addons, "sale_stock", ("sale", "stock"))
    make_module(addons, "sale_stock_extra", ("sale_stock",))
    make_module(addons, "stock")
    make_module(addons, "sale_test", ("sale",))
    make_module(addons, "l10n_be", ("sale",))
    make_module(addons, "l10n_multilang")

    def include(module: str) -> bool:
        return "test" not in module

    assert export.find_dependents([addons], include, {"sale"}) == {"sale_stock", "sale_stock_extra", "l10n_be"}
    assert export.find_dependents([addons], include, {"l10n_be"}) == {"l10n_multilang"}


def test_unreadable_manifests_are_skipped(tmp_path: Path) -> None:
    com, ent = tmp_path / "odoo", tmp_path / "enterprise"
    sale = make_module(com / "addons", "sale")
    (make_module(com / "addons", "broken") / "__manifest__.py").write_text("{'depends': [")
    ent.mkdir()

    modules = export.get_modules_per_server_type(module_to_path={"sale": sale}, com_path=com, ent_path=ent)

    assert modules[export.ServerType.COM][0] == {"sale"}


def run_fake_server(script: str) -> tuple[bool, float]:
    server = export._Server(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        name="Custom",
        formatted="[CUSTOM]",
        url="http://localhost:1",
        database="db",
        module_to_path={},
        odoo_cmd=[sys.executable, "-c", script],
    )
    start = time.monotonic()
    with Progress(disable=True) as progress:
        exported = export.run_server_and_export_terms(server, username="admin", password="admin", progress=progress)  # noqa: S106
    return exported, time.monotonic() - start


def test_server_stops_when_exporting_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(**_: object) -> None:
        raise ConnectionRefusedError

    monkeypatch.setattr(export, "_export_module_terms", fail)

    # A server that is ready, and would run forever.
    exported, duration = run_fake_server(
        "import sys, time; print('Modules loaded.', file=sys.stderr, flush=True); time.sleep(60)",
    )

    assert exported is False
    assert duration < 30


def test_the_cause_of_a_failed_install_is_shown(capsys: pytest.CaptureFixture[str]) -> None:
    exported, _ = run_fake_server(
        "import sys, time\n"
        "print('ERROR odoo.modules.registry: Failed to load registry', file=sys.stderr, flush=True)\n"
        "print('Traceback (most recent call last):', file=sys.stderr, flush=True)\n"
        "print('ValueError: THE REAL CAUSE', file=sys.stderr, flush=True)\n"
        "time.sleep(60)\n",
    )

    assert exported is False
    output = capsys.readouterr().err
    assert "THE REAL CAUSE" in output
    assert "[CUSTOM]" in output


def test_servers_get_different_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    # 8070 is taken, like by `otk dev run`.
    def is_port_free(port: int, _host: str = "127.0.0.1") -> bool:
        return port != 8070

    monkeypatch.setattr(runtime, "is_port_free", is_port_free)

    assert export.free_ports("localhost", 8069, 4) == [8069, 8071, 8072, 8073]


def test_cli_rejects_full_and_quick_install_together() -> None:
    result = CliRunner().invoke(app, ["po", "export", "--full-install", "--quick-install", "sale"])

    assert result.exit_code == 1
    assert "not both" in result.output
