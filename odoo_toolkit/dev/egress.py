"""The command to apply the domains the dev containers can reach, and to see what they were refused."""

from typing import Annotated

from typer import Exit, Option, Typer

from odoo_toolkit.common import (
    console,
    get_config_file,
    print_command_title,
    print_error,
    print_success,
    print_warning,
)

from .allowed_domains import get_allowed_domains
from .common import check_docker, start_services
from .proxy import get_denied_domains, get_probes, reload_proxy, run_probe, write_allowed_domains

app = Typer()


@app.command()
def egress(
    restart: Annotated[
        bool,
        Option("--start", help="Start the shared services if the proxy isn't running."),
    ] = False,
    check: Annotated[
        bool,
        Option(
            "--check",
            help="Check that the network of the dev containers has no route out except the proxy, and that the proxy "
            "only lets the allowed domains through, with a throwaway container.",
        ),
    ] = False,
) -> None:
    """Apply the allowed :globe_with_meridians: domains of the dev containers, and show what they were refused.

    The dev containers have no route out, except through a proxy that only lets the domains in `allowed_domains` in the
    `[devcontainer]` table of your odoo-toolkit configuration file (`~/.config/odoo-toolkit/config.toml`, or in
    `$XDG_CONFIG_HOME`) through. It replaces the default list:\n
    \n
    `[devcontainer]`\n
    `allowed_domains = ["api.anthropic.com", "claude.ai", "pypi.org", "*.github.com"]`\n
    \n
    The first time, it writes the default list into that file for you to edit. `*.github.com` allows `github.com` and
    its subdomains. Run this command after changing the list: the dev containers
    pick it up right away. `otk dev db` applies it as well.

    With `--check`, it also tries to reach the internet from the network of the dev containers, directly and through the
    proxy, to prove that nothing gets out that shouldn't.
    """
    print_command_title(":globe_with_meridians: Dev Container Network")
    check_docker()
    _, domains_removed = write_allowed_domains()
    if not reload_proxy(restart=domains_removed):
        if not restart:
            print_warning(
                "The proxy isn't running. Start it with [b]otk dev egress --start[/b] or [b]otk dev db[/b].\n"
            )
            raise Exit(1)
        start_services()
        if not reload_proxy():
            print_error("The proxy didn't start.")
            raise Exit(1)
    domains = get_allowed_domains()
    print_success(f"The proxy allows {len(domains)} domains:\n")
    for domain in domains:
        console.print(f"  {domain}")
    console.print(f"\n[d]From [u]{get_config_file()}[/u][/d]\n")
    if denied := [domain for domain in get_denied_domains() if domain not in domains]:
        console.print("The proxy recently refused these domains. Add the ones you trust to your list:\n")
        for domain in denied:
            console.print(f"  {domain}")
        console.print()
    if check:
        failed = False
        for probe in get_probes(domains):
            passed = run_probe(probe)
            failed |= not passed
            console.print(f"  {'[green]:heavy_check_mark:[/green]' if passed else '[red]:x:[/red]'} {probe.name}")
        console.print()
        if failed:
            print_error(
                "The dev containers aren't closed as they should be. Don't trust them with data you can't share."
            )
            raise Exit(1)
        print_success(
            "The dev containers have no route out except through the proxy, and it only lets the allowed domains.\n"
        )
