import shutil
import subprocess
from typing import Annotated
from urllib.parse import quote

from typer import Argument, Exit, Option

from .common import print_error
from .mv.config import (
    find_multiverse_dir,
    find_version_dir,
    get_code_profile,
    get_current_dir,
    require_version_dir,
)


def code(
    version: Annotated[
        str | None,
        Argument(help="The version to open, like `19.0`. Defaults to the version directory you are in."),
    ] = None,
    profile: Annotated[
        str | None,
        Option(
            "--profile",
            "-p",
            help="The Visual Studio Code profile to open it with, like `Odoo`. Defaults to `profile` in the `[code]` "
            "table of `multiverse.toml`. A profile that doesn't exist yet is created empty.",
        ),
    ] = None,
    local: Annotated[
        bool,
        Option("--local", help="Open it on your machine, even if it has a dev container."),
    ] = False,
) -> None:
    """Open :keyboard: an Odoo Multiverse version directory in Visual Studio Code.

    Run this command anywhere inside your multiverse, and it opens the version directory you are in, or the one you
    name. If it has a dev container (see `otk mv setup --devcontainer`), it opens in it, so the window and any AI agent
    in it only see your multiverse. With a profile, the window always has your extensions and settings for Odoo,
    without having to associate every version directory with it in Visual Studio Code.\n
    \n
    It needs the `code` command: run *Shell Command: Install 'code' command in PATH* in Visual Studio Code.
    """
    if not (code_command := shutil.which("code")):
        print_error(
            "The [b]code[/b] command isn't available. Run [b]Shell Command: Install 'code' command in PATH[/b] in "
            "Visual Studio Code.\n",
        )
        raise Exit(1)

    if version:
        if not (multiverse_dir := find_multiverse_dir(get_current_dir())):
            print_error("Run this command inside an Odoo Multiverse to open one of its versions.\n")
            raise Exit(1)
        version_dir = multiverse_dir / version
        if not version_dir.is_dir() or find_version_dir(version_dir) != version_dir:
            print_error(f"There is no version directory [b]{version}[/b] in [u]{multiverse_dir}[/u].\n")
            raise Exit(1)
    else:
        version_dir = require_version_dir()

    profile = profile or get_code_profile(version_dir.parent)
    target = [str(version_dir)]
    if not local and (version_dir / ".devcontainer" / "devcontainer.json").is_file():
        # A dev container is addressed by the hex of its folder on your machine, and the folder it opens inside it.
        host_path = str(version_dir).encode().hex()
        target = ["--folder-uri", f"vscode-remote://dev-container+{host_path}{quote(str(version_dir))}"]
    subprocess.run([code_command, *(["--profile", profile] if profile else []), *target], check=False)
