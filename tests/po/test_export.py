import sys
import time
import xmlrpc.client
from base64 import b64encode
from pathlib import Path

import pytest
from rich.progress import Progress
from typer.testing import CliRunner

from odoo_toolkit.dev import runtime
from odoo_toolkit.main import app
from odoo_toolkit.po import export
from odoo_toolkit.po.common import DEFAULT_EXCLUDE, select_modules
from odoo_toolkit.versions import get_versions


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


POT = b'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\nmsgid "Hello"\nmsgstr ""\n'
EMPTY_POT = b'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'


def run_fake_server(
    script: str,
    module_to_path: dict[str, Path] | None = None,
    *,
    export_cmd: list[str] | None = None,
    config_file: Path | None = None,
) -> tuple[bool, float]:
    server = export._Server(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        name="Custom",
        formatted="[CUSTOM]",
        url="http://localhost:1",
        database="db",
        module_to_path=module_to_path or {},
        odoo_cmd=[sys.executable, "-c", script],
        export_cmd=export_cmd,
        config_file=config_file,
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


# Without an export command, it exports with XML-RPC.
@pytest.mark.parametrize("export_cmd", [None, ["odoo-bin", "i18n", "export"]])
def test_the_cause_of_a_failed_install_is_shown(
    capsys: pytest.CaptureFixture[str], export_cmd: list[str] | None
) -> None:
    exported, _ = run_fake_server(
        "import sys, time\n"
        "print('ERROR odoo.modules.registry: Failed to load registry', file=sys.stderr, flush=True)\n"
        "print('Traceback (most recent call last):', file=sys.stderr, flush=True)\n"
        "print('ValueError: THE REAL CAUSE', file=sys.stderr, flush=True)\n"
        "time.sleep(60)\n",
        export_cmd=export_cmd,
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


def test_a_server_that_stops_by_itself_is_a_failure(capsys: pytest.CaptureFixture[str]) -> None:
    exported, _ = run_fake_server("import sys; print('THE LAST WORDS', file=sys.stderr); sys.exit(3)")

    assert exported is False
    output = capsys.readouterr().err
    assert "exited with code: 3" in output
    assert "THE LAST WORDS" in output


def test_the_export_command_runs_after_the_install() -> None:
    chain = export._chain_export  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]

    assert chain(["odoo-bin", "--init", "sale"], None) == ["odoo-bin", "--init", "sale"]
    assert chain(["odoo-bin", "--db_password=it's"], ["odoo-bin", "i18n", "export", "sale"]) == [
        "sh",
        "-c",
        "odoo-bin '--db_password=it'\"'\"'s' && odoo-bin i18n export sale",
    ]


def test_export_with_odoo_bin_i18n_export(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    modules = {name: tmp_path / name for name in ("sale", "mrp", "stock")}
    for module in modules.values():
        (module / "i18n").mkdir(parents=True)
    # An empty .pot file that was there before, and one with terms that gets replaced.
    (modules["mrp"] / "i18n" / "mrp.pot").write_bytes(POT)
    (modules["stock"] / "i18n" / "stock.pot").write_bytes(POT)
    # Installs, then writes the .pot files like `odoo-bin i18n export`, but skips `stock`, which isn't installed.
    script = f"""
import sys, pathlib
print('INFO db odoo.modules.loading: Modules loaded.', file=sys.stderr, flush=True)
for name, contents in (('sale', {POT!r}), ('mrp', {EMPTY_POT!r})):
    print(f'INFO db odoo.cli.i18n: Exporting {{name}} (pot) to x', file=sys.stderr, flush=True)
    pathlib.Path({str(tmp_path)!r}, name, 'i18n', name + '.pot').write_bytes(contents)
"""

    config_file = tmp_path / "otk-export.conf"
    config_file.write_text("[options]\ndb_password = secret\n")

    exported, _ = run_fake_server(script, modules, export_cmd=["odoo-bin", "i18n", "export"], config_file=config_file)

    # `stock` wasn't exported.
    assert exported is False
    # It holds the database password.
    assert not config_file.exists()
    assert b'msgid "Hello"' in (modules["sale"] / "i18n" / "sale.pot").read_bytes()
    assert not (modules["mrp"] / "i18n" / "mrp.pot").exists()
    assert (modules["stock"] / "i18n" / "stock.pot").read_bytes() == POT
    output = " ".join(capsys.readouterr().err.split())
    assert "✅ (1 terms)" in output
    assert "Removed empty mrp.pot" in output
    assert "stock Not installed, so not exported" in output


def test_a_failed_i18n_export_shows_why(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = (
        "import sys\n"
        "print('INFO db odoo.modules.loading: Modules loaded.', file=sys.stderr, flush=True)\n"
        "print('odoo-bin i18n export: error: No valid module has been provided', file=sys.stderr, flush=True)\n"
        "sys.exit(2)\n"
    )

    exported, _ = run_fake_server(script, {"sale": tmp_path / "sale"}, export_cmd=["odoo-bin", "i18n", "export"])

    assert exported is False
    output = " ".join(capsys.readouterr().err.split())
    assert "Exporting the terms failed and exited with code: 2" in output
    assert "No valid module has been provided" in output


class FakeOdoo:
    """Answer the XML-RPC calls of the export like Odoo, with `sale` installed and `mrp` not."""

    def __init__(self, *, uid: int | bool = 2) -> None:
        self.uid = uid

    def authenticate(self, *_: object) -> int | bool:
        return self.uid

    def execute_kw(self, _db: str, _uid: int, _password: str, model: str, method: str, _args: list[object]) -> object:
        match model, method:
            case "ir.module.module", "search_read":
                return [{"id": 1, "name": "sale"}]
            case "base.language.export", "create":
                return 5
            case "base.language.export", "read":
                return [{"data": b64encode(POT).decode()}]
            case _:
                return True


def export_with_xmlrpc(monkeypatch: pytest.MonkeyPatch, odoo: FakeOdoo, module_to_path: dict[str, Path]) -> bool:
    def server_proxy(_url: str) -> FakeOdoo:
        return odoo

    monkeypatch.setattr(export, "_server_proxy", server_proxy)
    with Progress(disable=True) as progress:
        return export._export_module_terms(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            server_name="Custom",
            server_formatted="[CUSTOM]",
            module_to_path=module_to_path,
            url="http://localhost:8069",
            database="db",
            username="admin",
            password="admin",  # noqa: S106
            progress=progress,
        )


def test_export_with_xmlrpc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    modules = {"sale": tmp_path / "sale", "mrp": tmp_path / "mrp"}
    for module in modules.values():
        module.mkdir()

    # `mrp` isn't installed.
    assert export_with_xmlrpc(monkeypatch, FakeOdoo(), modules) is False
    assert b'msgid "Hello"' in (modules["sale"] / "i18n" / "sale.pot").read_bytes()
    output = " ".join(capsys.readouterr().err.split())
    assert "mrp Not installed, so not exported" in output


def test_export_with_xmlrpc_needs_to_log_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert export_with_xmlrpc(monkeypatch, FakeOdoo(uid=False), {"sale": tmp_path / "sale"}) is False
    assert "Logging in as admin on database db failed" in " ".join(capsys.readouterr().err.split())


def test_save_pot_file(tmp_path: Path) -> None:
    save = export._save_pot_file  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    pot_path = tmp_path / "i18n" / "sale.pot"

    assert save("sale", tmp_path, EMPTY_POT, existed=False) == (
        True,
        "[d]No terms to translate[/d] :negative_squared_cross_mark:",
    )
    assert save("sale", tmp_path, POT, existed=False)[0]
    assert pot_path.is_file()
    assert "Removed empty" in save("sale", tmp_path, b"", existed=True)[1]
    assert not pot_path.exists()


@pytest.mark.parametrize(("version_info", "with_i18n_export"), [("('saas~18', 3)", False), ("('saas~18', 4)", True)])
def test_cli_exports_with_odoo_bin_i18n_export_since_saas_18_4(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repos: tuple[Path, Path],
    version_info: str,
    *,
    with_i18n_export: bool,
) -> None:
    com, _ = repos
    (com / "odoo").mkdir()
    (com / "odoo" / "release.py").write_text(f"version_info = {version_info}\n")
    servers: list[export._Server] = []  # pyright: ignore[reportPrivateUsage]

    def run(server: export._Server, **_: object) -> bool:  # pyright: ignore[reportPrivateUsage]
        servers.append(server)
        return True

    monkeypatch.setattr(export, "run_server_and_export_terms", run)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["po", "export", "--local", "sale"])

    assert result.exit_code == 0, result.output
    (server,) = servers
    if not with_i18n_export:
        # XML-RPC, while the server runs.
        assert server.export_cmd is None
        assert "--stop-after-init" not in server.odoo_cmd
        return
    assert server.config_file is not None
    config = str(server.config_file)
    # Odoo installs the modules, saves its options and stops, then `i18n export` reads them.
    assert server.odoo_cmd[:2] == ["sh", "-c"]
    assert f"--config {config} --save --stop-after-init" in server.odoo_cmd[2]
    assert server.odoo_cmd[2].endswith(f"&& python3 {com / 'odoo-bin'} i18n export --config {config} sale")


@pytest.mark.usefixtures("repos")
def test_cli_says_when_your_server_has_no_xmlrpc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def no_xmlrpc(**_: object) -> bool:
        url = "localhost:8069/xmlrpc/2/common"
        raise xmlrpc.client.ProtocolError(url, 404, "NOT FOUND", {})

    monkeypatch.setattr(export, "_export_module_terms", no_xmlrpc)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["po", "export", "--own-server", "sale"])

    assert result.exit_code == 1
    assert "install the rpc module" in " ".join(result.output.split())


def test_a_server_in_docker_exports_after_installing(tmp_path: Path) -> None:
    server = export._Server(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        name="Community",
        formatted="[COM]",
        url="http://localhost:8069",
        database="db",
        module_to_path={},
        odoo_cmd=["python3", "odoo-bin", "--config", export.CONTAINER_CONFIG_FILE, "--save", "--stop-after-init"],
        export_cmd=["python3", "odoo-bin", "i18n", "export", "--config", export.CONTAINER_CONFIG_FILE, "sale"],
    )
    container = export._Container(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        version_dir=tmp_path / "19.0",
        version=get_versions()["19.0"],
        multiverse_dir=tmp_path,
    )

    export._run_in_container(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        server, container, port=8069, arch="arm64", keep_db=True, git_identity={}, ssh_agent=None
    )

    *_, shell, option, script = server.odoo_cmd
    assert [shell, option] == ["sh", "-c"]
    # The connection to the shared PostgreSQL goes with the install, which saves it for the export.
    install, export_cmd = script.split(" && ")
    assert "--db_host=" in install
    assert export_cmd == f"python3 odoo-bin i18n export --config {export.CONTAINER_CONFIG_FILE} sale"
