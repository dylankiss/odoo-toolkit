import subprocess
from pathlib import Path

import pytest

from odoo_toolkit.dev import allowed_domains, proxy, runtime


def test_get_proxy_domains_turns_wildcards_into_the_proxy_syntax() -> None:
    # A name starting with a dot is the domain and its subdomains, which is what `*.` means here.
    assert proxy.get_proxy_domains(["pypi.org", "*.github.com"]) == ["pypi.org", ".github.com"]


def test_get_proxy_domains_leaves_out_what_a_wildcard_already_covers() -> None:
    # Squid doesn't start when a domain is covered by another one in the list.
    domains = ["test.odoo.com", "*.test.odoo.com", "www.test.odoo.com", "*.a.test.odoo.com", "pypi.org", "pypi.org"]

    assert proxy.get_proxy_domains(domains) == [".test.odoo.com", "pypi.org"]


def test_get_proxy_domains_keeps_similar_names_that_are_not_covered() -> None:
    assert proxy.get_proxy_domains(["*.odoo.com", "notodoo.com", "odoo.com.evil.example"]) == [
        ".odoo.com",
        "notodoo.com",
        "odoo.com.evil.example",
    ]


def test_get_proxy_domains_is_never_empty() -> None:
    assert proxy.get_proxy_domains([]) == [proxy.PLACEHOLDER_DOMAIN]


def test_write_allowed_domains_writes_where_the_proxy_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config_file = tmp_path / "xdg-config" / "odoo-toolkit" / "config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text('[devcontainer]\nallowed_domains = ["pypi.org", "*.github.com"]\n')

    egress_dir, _ = proxy.write_allowed_domains()

    assert egress_dir == tmp_path / "state" / "odoo-toolkit" / "egress"
    lines = (egress_dir / proxy.ALLOWED_DOMAINS_FILE).read_text().splitlines()
    assert [line for line in lines if not line.startswith("#")] == ["pypi.org", ".github.com"]
    assert not list(egress_dir.glob("*.tmp"))


def test_write_allowed_domains_follows_a_changed_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    egress_dir, _ = proxy.write_allowed_domains()
    assert "api.anthropic.com" in (egress_dir / proxy.ALLOWED_DOMAINS_FILE).read_text()

    # The first run listed the default domains in your configuration, which you then edit.
    config_file = tmp_path / "xdg-config" / "odoo-toolkit" / "config.toml"
    assert "api.anthropic.com" in config_file.read_text()
    config_file.write_text('[devcontainer]\nallowed_domains = ["pypi.org"]\n')
    # The default domains are gone from it, so the proxy has to restart.
    assert proxy.write_allowed_domains()[1] is True

    content = (egress_dir / proxy.ALLOWED_DOMAINS_FILE).read_text()
    assert "pypi.org" in content
    assert "anthropic" not in content
    assert allowed_domains.get_allowed_domains() == ["pypi.org"]


def test_parse_denied_domains_lists_each_refused_domain_once_most_recent_last() -> None:
    lines = [
        "1.1 5 10.0.0.2 TCP_DENIED/403 3900 CONNECT a.example.com:443 - HIER_NONE/- text/html",
        "1.2 5 10.0.0.2 TCP_TUNNEL/200 3900 CONNECT api.anthropic.com:443 - HIER_DIRECT/1.2.3.4 -",
        "1.3 5 10.0.0.2 TCP_DENIED/403 3900 CONNECT b.example.com:443 - HIER_NONE/- text/html",
        "1.4 5 10.0.0.2 TCP_DENIED/403 3900 CONNECT a.example.com:443 - HIER_NONE/- text/html",
        "1.5 5 10.0.0.2 TCP_DENIED/403 3900 GET http://c.example.com/ - HIER_NONE/- text/html",
        "garbage",
    ]

    assert proxy.parse_denied_domains("\n".join(lines)) == ["b.example.com", "a.example.com"]


def test_is_allowed_matches_wildcards_and_the_domain_itself() -> None:
    allowed = ["pypi.org", "*.github.com"]

    assert proxy.is_allowed("pypi.org", allowed)
    assert proxy.is_allowed("github.com", allowed)
    assert proxy.is_allowed("api.github.com", allowed)
    assert not proxy.is_allowed("evilgithub.com", allowed)
    assert not proxy.is_allowed("files.pypi.org", allowed)


def test_get_probes_try_what_must_fail_and_what_must_work() -> None:
    probes = {probe.name: probe for probe in proxy.get_probes(["example.com", "*.github.com", "pypi.org"])}

    # example.com is allowed here, so the proxy is asked for another domain, and the first plain one is reached.
    assert probes["example.org is refused by the proxy"].should_succeed is False
    assert probes["example.com is reached through the proxy"].should_succeed is True
    assert "https://example.org" in probes["example.org is refused by the proxy"].script
    assert probes["No direct connection to the internet"].should_succeed is False
    assert probes["No direct DNS lookups"].should_succeed is False
    assert probes["PostgreSQL is reachable"].should_succeed is True
    # A superuser can run programs in the PostgreSQL container, so that has to be closed as well, from the inside.
    postgres = probes["PostgreSQL has no route out"]
    assert postgres.should_succeed is False
    assert postgres.container == "otk-postgres"
    assert "otk-egress:3128" in probes["example.org is refused by the proxy"].script


def test_get_probes_without_domains_only_checks_that_nothing_gets_out() -> None:
    names = [probe.name for probe in proxy.get_probes([])]

    assert "example.com is refused by the proxy" in names
    assert not [name for name in names if "reached through the proxy" in name]


@pytest.mark.parametrize(
    ("returncode", "should_succeed", "passed"), [(0, True, True), (1, True, False), (0, False, False), (6, False, True)]
)
def test_run_probe_passes_when_it_does_what_it_should(
    monkeypatch: pytest.MonkeyPatch, returncode: int, should_succeed: bool, passed: bool
) -> None:
    commands: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, "", "")

    monkeypatch.setattr(proxy.subprocess, "run", fake_run)

    assert proxy.run_probe(proxy.Probe("x", "true", should_succeed=should_succeed)) is passed
    # It runs on the network of the dev containers, which is where the dev containers are.
    assert commands[0][:5] == ["docker", "run", "--rm", "--network", "otk-dev"]


def test_squid_only_tunnels_to_https() -> None:
    config = (proxy.Path(proxy.__file__).parent.parent / "docker" / "egress" / "squid.conf").read_text()

    assert "acl ssl_ports port 443" in config
    assert "port 80" not in config
    assert "http_access deny !connect" in config
    assert config.rstrip().splitlines()[-1] != "http_access allow all"
    assert "http_access deny all" in config


def test_squid_sets_its_hostname() -> None:
    config = (proxy.Path(proxy.__file__).parent.parent / "docker" / "egress" / "squid.conf").read_text()

    # Without it, Squid stops at startup when the hostname of the container isn't a fully qualified domain name.
    assert "visible_hostname otk-egress" in config


def test_get_proxy_logs_returns_what_the_proxy_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, "line one\n", "FATAL: bad\n")

    monkeypatch.setattr(proxy.subprocess, "run", fake_run)

    assert proxy.get_proxy_logs() == "line one\nFATAL: bad"


def test_run_probe_runs_in_the_container_it_names(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 1 if cmd[-1] != "true" else 0, "", "")

    monkeypatch.setattr(proxy.subprocess, "run", fake_run)
    probe = proxy.Probe("x", "false", should_succeed=False, container="otk-postgres")

    assert proxy.run_probe(probe) is True
    assert commands[-1][:3] == ["docker", "exec", "otk-postgres"]


def test_run_probe_fails_when_its_container_is_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    # A container that isn't there can't connect to anything, which must not pass for a closed one.
    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, "", "")

    monkeypatch.setattr(proxy.subprocess, "run", fake_run)

    assert proxy.run_probe(proxy.Probe("x", "false", should_succeed=False, container="otk-postgres")) is False


def test_compose_keeps_postgres_off_the_open_network() -> None:
    compose = (proxy.Path(proxy.__file__).parent.parent / "docker" / "compose.yaml").read_text()
    postgres = compose.split("  postgres:\n")[1].split("\n  postgres-forward:\n")[0]
    forward = compose.split("  postgres-forward:\n")[1].split("\n  mailpit:\n")[0]

    # PostgreSQL has no route out and publishes nothing, and its forwarder is the way in from the open network.
    assert "networks: [db, dev]" in postgres
    assert "ports:" not in postgres
    assert "127.0.0.1:${OTK_DB_PORT:-5432}:5432" in forward
    assert "aliases: [otk-postgres]" in forward
    assert "TCP:postgres:5432" in forward
    assert "name: otk-db\n    internal: true" in compose


def test_write_allowed_domains_says_when_a_domain_is_gone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config_file = tmp_path / "xdg-config" / "odoo-toolkit" / "config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text('[devcontainer]\nallowed_domains = ["pypi.org", "github.com"]\n')

    # Nothing was allowed before, and nothing that was allowed is gone.
    assert proxy.write_allowed_domains()[1] is False
    assert proxy.write_allowed_domains()[1] is False
    # Adding a domain doesn't cut anything.
    config_file.write_text('[devcontainer]\nallowed_domains = ["pypi.org", "github.com", "claude.ai"]\n')
    assert proxy.write_allowed_domains()[1] is False
    # Removing one does, since a connection to it may be open.
    config_file.write_text('[devcontainer]\nallowed_domains = ["pypi.org", "claude.ai"]\n')
    assert proxy.write_allowed_domains()[1] is True


@pytest.mark.parametrize("restart", [False, True])
def test_reload_proxy_restarts_it_to_cut_open_connections(monkeypatch: pytest.MonkeyPatch, restart: bool) -> None:
    commands: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(proxy.subprocess, "run", fake_run)

    assert proxy.reload_proxy(restart=restart) is True
    expected = (
        ["docker", "exec", "otk-egress", "squid", "-k", "reconfigure"]
        if not restart
        else ["docker", "restart", "otk-egress"]
    )
    assert commands == [expected]


def test_squid_never_tunnels_to_private_addresses() -> None:
    config = (proxy.Path(proxy.__file__).parent.parent / "docker" / "egress" / "squid.conf").read_text()
    rules = [line for line in config.splitlines() if line.startswith("http_access")]

    # What an allowed name resolves to is checked before the allowed domains, so none can lead into a private network.
    assert rules.index("http_access deny private_destination") < rules.index("http_access allow allowed")
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16", "fc00::/7"):
        assert network in config


def test_the_proxy_port_is_the_same_everywhere_it_is_written() -> None:
    docker_dir = proxy.Path(proxy.__file__).parent.parent / "docker"
    port = runtime.EGRESS_PORT

    # The port is in the Python code, in Squid's configuration and in the healthcheck, which can't share a constant.
    assert f"http_port {port}\n" in (docker_dir / "egress" / "squid.conf").read_text()
    assert f"/dev/tcp/127.0.0.1/{port}" in (docker_dir / "compose.yaml").read_text()
    assert f"http://otk-egress:{port}" == runtime.PROXY_URL


def test_get_denied_domains_reads_the_access_log_of_the_running_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    log = "1.1 5 10.0.0.2 TCP_DENIED/403 3900 CONNECT a.example.com:443 - HIER_NONE/- text/html\n"

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, log, "")

    monkeypatch.setattr(proxy.subprocess, "run", fake_run)

    assert proxy.get_denied_domains() == ["a.example.com"]
    assert commands == [["docker", "exec", "otk-egress", "tail", "-n", "2000", "/var/log/squid/access.log"]]


def test_get_denied_domains_is_empty_without_a_running_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, "", "no such container")

    monkeypatch.setattr(proxy.subprocess, "run", fake_run)

    assert proxy.get_denied_domains() == []
