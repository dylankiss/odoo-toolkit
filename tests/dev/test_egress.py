from pathlib import Path

import pytest
from typer.testing import CliRunner

from odoo_toolkit.dev import egress
from odoo_toolkit.dev.proxy import Probe

runner = CliRunner()


class Proxy:
    """Stand in for the proxy and what the `otk dev egress` command asks of it."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        running: bool = True,
        starts: bool = True,
        removed: bool = False,
        denied: list[str] | None = None,
        probes: dict[str, bool] | None = None,
    ) -> None:
        self.running = running
        self.starts = starts
        self.restarts: list[bool] = []
        self.started = 0
        self.denied = denied or []
        self.probes = probes or {}
        monkeypatch.setattr(egress, "check_docker", lambda: None)
        monkeypatch.setattr(egress, "write_allowed_domains", lambda: (Path("/state"), removed))
        monkeypatch.setattr(egress, "reload_proxy", self.reload)
        monkeypatch.setattr(egress, "start_services", self.start)
        monkeypatch.setattr(egress, "get_allowed_domains", lambda: ["api.anthropic.com", "*.github.com"])
        monkeypatch.setattr(egress, "get_denied_domains", self.get_denied)
        monkeypatch.setattr(egress, "get_probes", self.get_probes)
        monkeypatch.setattr(egress, "run_probe", self.run_probe)

    def get_denied(self) -> list[str]:
        return self.denied

    def get_probes(self, _: list[str]) -> list[Probe]:
        return [Probe(name, "x", should_succeed=True) for name in self.probes]

    def run_probe(self, probe: Probe) -> bool:
        return self.probes[probe.name]

    def reload(self, *, restart: bool = False) -> bool:
        self.restarts.append(restart)
        return self.running

    def start(self) -> None:
        self.started += 1
        self.running = self.starts


def test_egress_applies_the_domains_and_lists_them(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = Proxy(monkeypatch)

    result = runner.invoke(egress.app, [])

    assert result.exit_code == 0
    assert "The proxy allows 2 domains" in result.output
    assert "api.anthropic.com" in result.output
    assert "*.github.com" in result.output
    assert proxy.restarts == [False]


def test_egress_restarts_the_proxy_when_a_domain_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = Proxy(monkeypatch, removed=True)

    assert runner.invoke(egress.app, []).exit_code == 0

    assert proxy.restarts == [True]


def test_egress_shows_the_domains_the_proxy_refused_that_are_not_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    Proxy(monkeypatch, denied=["raw.githubusercontent.com", "api.anthropic.com"])

    result = runner.invoke(egress.app, [])

    assert "recently refused" in result.output
    assert "raw.githubusercontent.com" in result.output
    # A domain that is allowed now isn't one to add.
    assert result.output.count("api.anthropic.com") == 1


def test_egress_stops_when_the_proxy_is_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = Proxy(monkeypatch, running=False)

    result = runner.invoke(egress.app, [])

    assert result.exit_code == 1
    assert "isn't running" in result.output
    assert proxy.started == 0


def test_egress_starts_the_services_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = Proxy(monkeypatch, running=False)

    result = runner.invoke(egress.app, ["--start"])

    assert result.exit_code == 0
    assert proxy.started == 1
    assert "The proxy allows 2 domains" in result.output


def test_egress_reports_a_proxy_that_does_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    Proxy(monkeypatch, running=False, starts=False)

    result = runner.invoke(egress.app, ["--start"])

    assert result.exit_code == 1
    assert "didn't start" in result.output


def test_egress_check_passes_when_every_probe_does(monkeypatch: pytest.MonkeyPatch) -> None:
    Proxy(monkeypatch, probes={"No direct connection": True, "Allowed is reached": True})

    result = runner.invoke(egress.app, ["--check"])

    assert result.exit_code == 0
    assert "No direct connection" in result.output
    assert "have no route out except through the proxy" in result.output


def test_egress_check_fails_when_a_probe_does(monkeypatch: pytest.MonkeyPatch) -> None:
    Proxy(monkeypatch, probes={"No direct connection": False, "Allowed is reached": True})

    result = runner.invoke(egress.app, ["--check"])

    assert result.exit_code == 1
    assert "aren't closed as they should be" in result.output
