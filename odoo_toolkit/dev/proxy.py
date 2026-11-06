"""The proxy that lets the dev containers reach the domains you allow, and nothing else.

The dev containers are on a network with no route out. The proxy is on it and on the open network, and only lets the
domains in your odoo-toolkit configuration file through (see `odoo_toolkit.dev.allowed_domains`).
"""

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from odoo_toolkit.common import console, get_config_file, write_file_atomically

from .allowed_domains import ensure_allowed_domains_configured, get_allowed_domains
from .runtime import DB_HOST, DEV_NETWORK, EGRESS_HOST, PROXY_URL

ALLOWED_DOMAINS_FILE = "allowed-domains.txt"
ACCESS_LOG = "/var/log/squid/access.log"
# Squid doesn't accept an empty list of domains.
PLACEHOLDER_DOMAIN = "invalid.invalid"


def get_egress_dir() -> Path:
    """Get the folder the proxy reads its domains from, in `$XDG_STATE_HOME` or `~/.local/state`."""
    state_home = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(state_home) / "odoo-toolkit" / "egress"


def matches(pattern: str, domain: str) -> bool:
    """Whether a domain is covered by a pattern, in the proxy's syntax: `.example.com` is `example.com` and its subdomains."""
    return domain.endswith(pattern) or domain == pattern[1:] if pattern.startswith(".") else domain == pattern


def get_proxy_domains(domains: list[str]) -> list[str]:
    """Get the domains the way the proxy reads them: `*.github.com` as `.github.com`, which includes `github.com`.

    Squid refuses to start when a domain is also covered by another one in the list, like `github.com` next to
    `.github.com`, or `api.github.com` next to it, so those are left out: the wildcard allows them anyway.

    :param domains: The allowed domains, like `["github.com", "*.github.com", "pypi.org"]`.
    :return: The domains for the proxy, like `[".github.com", "pypi.org"]`, never empty.
    """
    converted = list(dict.fromkeys(domain.removeprefix("*") for domain in domains))
    wildcards = [domain for domain in converted if domain.startswith(".")]
    kept = [
        domain
        for domain in converted
        if not any(domain != wildcard and matches(wildcard, domain) for wildcard in wildcards)
    ]
    return kept or [PLACEHOLDER_DOMAIN]


def seed_default_domains() -> None:
    """Write the default allowed domains in your configuration if it has none yet, and say so."""
    if ensure_allowed_domains_configured():
        console.print(
            f"Added the default allowed domains of the dev containers to [u]{get_config_file()}[/u], to edit there. "
            "Apply changes with [b]otk dev egress[/b].\n",
        )


def read_written_domains(domains_file: Path) -> set[str]:
    """Get the domains in a file written by `write_allowed_domains`, without the placeholder, or none if there is none."""
    try:
        lines = domains_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {line for line in lines if line and not line.startswith("#") and line != PLACEHOLDER_DOMAIN}


def write_allowed_domains() -> tuple[Path, bool]:
    """Write the allowed domains of your configuration where the proxy reads them.

    :return: The folder the proxy reads them from, and whether a domain it allowed before is gone, in which case a
        proxy that runs should restart to cut the connections open to it.
    """
    seed_default_domains()
    egress_dir = get_egress_dir()
    domains = get_proxy_domains(get_allowed_domains())
    domains_file = egress_dir / ALLOWED_DOMAINS_FILE
    removed = bool(read_written_domains(domains_file) - set(domains))
    content = "# Written by odoo-toolkit from your configuration. Don't modify.\n"
    content += "".join(f"{domain}\n" for domain in domains)
    # The folder is mounted, not the file, so replacing the file shows up in the container.
    write_file_atomically(domains_file, content)
    return egress_dir, removed


def reload_proxy(*, restart: bool = False) -> bool:
    """Make a running proxy read the allowed domains again.

    Reading them again doesn't close a connection that is open already, so a domain that is gone stays reachable
    until it closes. Restarting the proxy cuts them all, and the clients connect again.

    :param restart: Whether to restart the proxy, instead of only having it read the domains again.
    :return: Whether the proxy is running.
    """
    cmd = ["docker", "restart", EGRESS_HOST] if restart else ["docker", "kill", "--signal", "HUP", EGRESS_HOST]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return not result.returncode


def get_proxy_logs() -> str:
    """Get the last lines the proxy logged, like why it stopped, or an empty text if there is no such container."""
    result = subprocess.run(
        ["docker", "logs", "--tail", "20", EGRESS_HOST],
        capture_output=True,
        text=True,
        check=False,
    )
    return "" if result.returncode else (result.stdout + result.stderr).strip()


def parse_denied_domains(access_log: str) -> list[str]:
    """Get the domains the proxy refused, most recent last, from its access log, without repeating them.

    :param access_log: The contents of the access log, which has lines like
        `1736170000.123 5 10.0.0.2 TCP_DENIED/403 3900 CONNECT example.com:443 - HIER_NONE/- text/html`.
    :return: The domains, like `example.com`.
    """
    denied: dict[str, None] = {}
    for line in access_log.splitlines():
        fields = line.split()
        if len(fields) < 7 or not fields[3].startswith("TCP_DENIED") or fields[5] != "CONNECT":  # noqa: PLR2004
            continue
        domain = fields[6].rsplit(":", 1)[0]
        denied.pop(domain, None)
        denied[domain] = None
    return list(denied)


def get_denied_domains() -> list[str]:
    """Get the domains the running proxy recently refused."""
    result = subprocess.run(
        ["docker", "exec", EGRESS_HOST, "tail", "-n", "2000", ACCESS_LOG],
        capture_output=True,
        text=True,
        check=False,
    )
    return [] if result.returncode else parse_denied_domains(result.stdout)


# The image the checks run in, a small one with curl, on the network of the dev containers.
CHECK_IMAGE = "curlimages/curl:8.23.0"
# Domains no default or usual list allows, to check that the proxy refuses them.
REFUSED_CANDIDATES = ("example.com", "example.org", "example.net")


@dataclass(frozen=True)
class Probe:
    """One thing to try from the network of the dev containers, and whether it should work."""

    name: str
    script: str
    should_succeed: bool = field(kw_only=True)
    # The container to run it in, instead of a throwaway one on the network of the dev containers.
    container: str | None = field(default=None, kw_only=True)


def is_allowed(domain: str, allowed_domains: list[str]) -> bool:
    """Whether a domain matches one of the allowed domains, where `*.example.com` includes `example.com` itself."""
    return any(matches(allowed.removeprefix("*"), domain) for allowed in allowed_domains)


def get_probes(allowed_domains: list[str]) -> list[Probe]:
    """Get what to try from the network of the dev containers to check that it has no route out but the proxy.

    :param allowed_domains: The domains the proxy allows.
    :return: The probes: the ones that must fail, and the ones that must work.
    """
    refused = next((domain for domain in REFUSED_CANDIDATES if not is_allowed(domain, allowed_domains)), None)
    allowed = next((domain for domain in allowed_domains if not domain.startswith("*.")), None)
    probes = [
        Probe(
            "No direct connection to the internet",
            "curl --noproxy '*' -sS -m 5 -o /dev/null https://1.1.1.1",
            should_succeed=False,
        ),
        Probe("No direct DNS lookups", "nslookup example.com", should_succeed=False),
        # A connection that was made but answered with something else, like PostgreSQL does, still reached it.
        Probe(
            "PostgreSQL is reachable",
            f"curl --noproxy '*' -sS -m 5 -o /dev/null http://{DB_HOST}:5432; code=$?; "
            "[ $code -ne 6 ] && [ $code -ne 7 ] && [ $code -ne 28 ]",
            should_succeed=True,
        ),
    ]
    # A superuser can run programs in the PostgreSQL container, so it must have no route out either.
    probes.append(
        Probe(
            "PostgreSQL has no route out",
            "timeout 5 bash -c 'exec 3<>/dev/tcp/1.1.1.1/443'",
            should_succeed=False,
            container=DB_HOST,
        ),
    )
    if refused:
        refused_script = f"curl -x {PROXY_URL} -sS -m 10 -o /dev/null https://{refused}"
        probes.append(Probe(f"{refused} is refused by the proxy", refused_script, should_succeed=False))
    if allowed:
        allowed_script = f"curl -x {PROXY_URL} -sS -m 15 -o /dev/null https://{allowed}"
        probes.append(Probe(f"{allowed} is reached through the proxy", allowed_script, should_succeed=True))
    return probes


def run_probe(probe: Probe) -> bool:
    """Run a probe in a throwaway container on the network of the dev containers, or in its own container.

    :return: Whether it did what it should: worked if it should, and failed if it should.
    """
    if (
        probe.container
        and subprocess.run(["docker", "exec", probe.container, "true"], capture_output=True, check=False).returncode
    ):
        # A container that isn't running can't try anything, which would look like a connection that failed.
        return False
    cmd = (
        ["docker", "exec", probe.container, "sh", "-c", probe.script]
        if probe.container
        else ["docker", "run", "--rm", "--network", DEV_NETWORK, "--entrypoint", "sh", CHECK_IMAGE, "-c", probe.script]
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return (result.returncode == 0) == probe.should_succeed
