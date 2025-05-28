from collections import defaultdict
from pathlib import Path
from typing import Annotated

from typer import Argument, Option, Typer

from odoo_toolkit.common import (
    EMPTY_LIST,
    TransientProgress,
    get_valid_modules_to_path_mapping,
    print,
    print_command_title,
    print_error,
    print_header,
    print_success,
)

from .common import TxConfig, TxConfigError

app = Typer()


@app.command()
def add(
    modules: Annotated[
        list[str],
        Argument(help='Add these Odoo modules to .tx/config, or either "all", "community", or "enterprise".'),
    ],
    tx_project: Annotated[str, Option("--tx-project", "-p", help="Specify the Transifex project name.")],
    tx_org: Annotated[str, Option("--tx-org", "-o", help="Specify the Transifex organization name.")] = "odoo",
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
    extra_addons_paths: Annotated[
        list[Path],
        Option(
            "--addons-path",
            "-a",
            help="Specify extra addons paths if your modules are not in Community or Enterprise.",
        ),
    ] = EMPTY_LIST,
) -> None:
    """Add modules to the Transifex config file.

    This command will add module entries to `.tx/config` files. The `.tx/config` files need to be located at the
    provided addons paths' roots. If the entries already exists, they will potentially be updated.

    For `odoo` and `enterprise`, the project name follows the format `odoo-18` for major versions and `odoo-s18-1` for
    SaaS versions. Other repos have their own project names.
    """
    print_command_title(":memo: Odoo Transifex Config Add")

    module_to_path = get_valid_modules_to_path_mapping(
        modules=modules,
        com_path=com_path,
        ent_path=ent_path,
        extra_addons_paths=extra_addons_paths,
    )

    print(f"Modules to add: [b]{'[/b], [b]'.join(sorted(module_to_path.keys()))}[/b]\n")

    # Combine all paths into one list.
    all_addons_paths = [p.expanduser().resolve() for p in [com_path, ent_path, *extra_addons_paths]]

    # Create a mapping from each addons path to the relevant containing modules.
    addons_path_to_modules: dict[Path, list[str]] = defaultdict(list[str])
    for module, module_path in module_to_path.items():
        addons_path = next((ap for ap in all_addons_paths if module_path.is_relative_to(ap)), None)
        if addons_path:
            addons_path_to_modules[addons_path].append(module)
    addons_path_to_modules = dict(addons_path_to_modules)

    # For each addons path, add the given modules to the .tx/config.
    for addons_path, local_modules in addons_path_to_modules.items():
        tx_config_path = addons_path / ".tx" / "config"

        print_header(f"Updating [u]{tx_config_path}[/u]")

        tx_config = TxConfig(addons_path / ".tx" / "config")
        for m in TransientProgress().track(local_modules, description="Adding modules ..."):
            tx_config.add_module(module_to_path[m], tx_project, tx_org)

        try:
            tx_config.save()
            print_success("Config file successfully updated.\n")
        except TxConfigError as e:
            print_error("Config file update failed.", str(e))
