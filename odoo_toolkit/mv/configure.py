"""Configure the version directories of a multiverse: links, Python and JavaScript tooling, editors and dev containers."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from string import Template
from subprocess import CalledProcessError
from typing import Any

from odoo_toolkit.common import (
    console,
)
from odoo_toolkit.dev.images import DOCKER_REPOSITORY
from odoo_toolkit.dev.runtime import (
    CLAUDE_DIR,
    DB_HOST,
    DEVCONTAINER_VOLUMES,
    NETWORK,
    ODOO_PORT,
    SMTP_HOST,
    VOLUMES,
    check_mountable,
    get_addons_paths,
    get_git_identity,
    get_odoo_args,
)
from odoo_toolkit.versions import version_sort_key

from .config import VERSION_PATTERN, WORKTREE_SOURCE_DIR, get_devcontainer_extras, get_linked_repos
from .repos import Outcome, RepoResult

MULTIVERSE_CONFIG_DIR = Path(__file__).parent.parent / "multiverse_config"


# The requirement files to install in each version's virtual environment, relative to the version directory.
VERSION_REQUIREMENTS = [
    Path("odoo/requirements.txt"),
    Path("documentation/requirements.txt"),
]


# Requirements the versions' own requirements need, but don't pin, like the setuptools Sphinx needs.
COMPAT_REQUIREMENTS_FILE = MULTIVERSE_CONFIG_DIR / "compat-requirements.txt"


# Constraints for building old packages from source, like the Cython version some need.
BUILD_CONSTRAINTS_FILE = MULTIVERSE_CONFIG_DIR / "build-constraints.txt"


# Odoo's Ruff rules of the master branch: the copy in the multiverse directory that the other Ruff configurations extend.
ODOO_RUFF_FILE = "ruff-odoo.toml"


# Matches the package name at the start of a requirement, like `Pillow` in `Pillow==10.2.0 ; python_version >= '3.12'`.
REQUIREMENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


# Files copied into the Community and Enterprise repositories to enable the JavaScript tooling.
JS_TOOLING_FILES = {
    "_eslintignore": ".eslintignore",
    "_eslintrc.json": ".eslintrc.json",
    "_jsconfig.json": "jsconfig.json",
    "_package.json": "package.json",
}


def write_root_config(multiverse_dir: Path, *, reset_config: bool) -> None:
    """Write the configuration shared by all versions to the multiverse directory."""
    # Odoo's rules of the master branch are the ones this toolkit came with, which a workflow keeps up to date.
    for filename in ("requirements.txt", ODOO_RUFF_FILE):
        if reset_config or not (multiverse_dir / filename).exists():
            shutil.copyfile(MULTIVERSE_CONFIG_DIR / filename, multiverse_dir / filename)
    if reset_config or not (multiverse_dir / "ruff.toml").exists():
        (multiverse_dir / "ruff.toml").write_text(_get_ruff_config(ODOO_RUFF_FILE), encoding="utf-8")


def _get_ruff_config(extend: str) -> str:
    """Get a Ruff configuration that follows Odoo's rules in another file, with the settings odoo-toolkit adds."""
    return f'extend = "{extend}"\nline-length = 120\n\n[format]\nquote-style = "preserve"\n'


def link_repos(version_dir: Path, names: list[str]) -> RepoResult:
    """Link shared and pinned repositories into a version directory.

    Existing links are kept, including the ones added with `otk mv link`.

    :param version_dir: The version directory.
    :param names: The repositories to link, which are cloned in the multiverse directory.
    :return: The result of linking.
    """
    linked: list[str] = []
    blocked: list[str] = []
    for name in dict.fromkeys(names):
        if link_repo(version_dir, name):
            linked.append(name)
        else:
            blocked.append(name)

    if blocked:
        return RepoResult(
            "links",
            Outcome.WARNING,
            f"[b]{'[/b], [b]'.join(blocked)}[/b] already exist as regular folders, so they aren't linked: remove "
            "them to link them",
        )
    if not linked:
        return RepoResult("links", Outcome.SKIPPED, "none")
    return RepoResult("links", Outcome.UPDATED, f"linked [b]{'[/b], [b]'.join(linked)}[/b]")


def link_skills(version_dir: Path) -> RepoResult:
    """Link the skills of the Odoo repository into the `.claude/skills` folder of a version directory.

    Claude Code finds its project skills there, and with a link it reads them in the repository: they are the ones of
    the branch, and a skill that is added there is available at once. Only the newest versions have skills, so an older
    version links to the skills of the newest version that has them.

    :param version_dir: The version directory.
    :return: The result of linking.
    """
    link = version_dir / ".claude" / "skills"
    # A link is relative to the folder it is in, which is `.claude`.
    if (version_dir / "odoo" / "skills").is_dir():
        target, source = Path("..") / "odoo" / "skills", "its own"
    else:
        others = [
            path
            for path in version_dir.parent.iterdir()
            if path != version_dir and VERSION_PATTERN.fullmatch(path.name) and (path / "odoo" / "skills").is_dir()
        ]
        if not others:
            return RepoResult("skills", Outcome.SKIPPED, "no skills in this multiverse")
        newest = max(others, key=lambda path: version_sort_key(path.name))
        target, source = Path("..") / ".." / newest.name / "odoo" / "skills", f"the ones of [b]{newest.name}[/b]"

    if link.is_symlink():
        if link.readlink() == target:
            return RepoResult("skills", Outcome.SKIPPED, "already linked")
        # Only the links of this toolkit are replaced, like when the version gets skills of its own.
        if not re.fullmatch(r"\.\./odoo/skills|\.\./\.\./[^/]+/odoo/skills", link.readlink().as_posix()):
            return RepoResult("skills", Outcome.WARNING, "[b].claude/skills[/b] links elsewhere, so it is kept")
        link.unlink()
    elif link.exists():
        return RepoResult(
            "skills", Outcome.WARNING, "[b].claude/skills[/b] already exists, so Odoo's skills aren't linked"
        )
    link.parent.mkdir(exist_ok=True)
    link.symlink_to(target)
    return RepoResult("skills", Outcome.UPDATED, f"linked [b].claude/skills[/b] to {source}")


def link_repo(version_dir: Path, name: str) -> bool:
    """Link a repository cloned in the multiverse directory into a version directory, using a relative link.

    :return: Whether the repository is in the version directory, which is not the case if a regular folder has its
        name. A pinned repository living in the version directory counts as linked.
    """
    link = version_dir / name
    target = Path("..") / name
    if link.is_symlink():
        # A link of your own, to another clone, is kept.
        return True
    if link.exists():
        return (target_dir := version_dir.parent / name).exists() and link.samefile(target_dir)
    link.symlink_to(target, target_is_directory=True)
    return True


def place_pinned_repo(multiverse_dir: Path, name: str, version: str) -> RepoResult:
    """Move a pinned repository into the version directory it runs on, and link it from the multiverse directory.

    Editors and language servers, like the Odoo one, see a regular folder that way, instead of a link they may resolve
    to another path. The link in the multiverse directory keeps it reachable at the same place for everything else.

    :param multiverse_dir: The multiverse directory.
    :param name: The repository name.
    :param version: The version it runs on, like `19.0`.
    :return: The result, skipped if it's already there.
    """
    root_link = multiverse_dir / name
    repo_dir = multiverse_dir / version / name
    link_target = Path(version) / name
    if root_link.is_symlink() and root_link.readlink() == link_target and not repo_dir.is_symlink():
        return RepoResult(name, Outcome.SKIPPED, f"in [b]{version}[/b]")
    current_dir = root_link.resolve()
    if repo_dir.is_symlink():
        # Linked into the version, by an older setup or `otk mv link`.
        repo_dir.unlink()
    elif repo_dir.exists():
        return RepoResult(
            name,
            Outcome.WARNING,
            f"[u]{repo_dir}[/u] already exists as a regular folder, so it can't move there: remove it to move it",
        )
    repo_dir.parent.mkdir(exist_ok=True)
    current_dir.rename(repo_dir)
    if root_link.is_symlink():
        root_link.unlink()
    root_link.symlink_to(link_target, target_is_directory=True)
    return RepoResult(name, Outcome.UPDATED, f"moved into [b]{version}[/b]")


def setup_python_env(version_dir: Path, *, python: str | None, requirements: list[Path], reset: bool) -> RepoResult:
    """Create a virtual environment for a version and install its dependencies, using uv.

    The environment serves the editor (code completion, linting, ...). Odoo itself runs in Docker, so packages that
    fail to build on this machine are reported, but don't fail the setup.

    :param version_dir: The version directory to create the `.venv` in.
    :param python: The Python version to use, like `3.12`, or None for uv's default.
    :param requirements: The requirement files to install, if they exist.
    :param reset: Whether to recreate an existing environment.
    :return: The result of the setup.
    """
    venv_dir = version_dir / ".venv"
    if venv_dir.exists() and (reset or (python and get_venv_python(venv_dir) != python)):
        shutil.rmtree(venv_dir)

    venv_cmd = ["uv", "venv", "--seed", "--allow-existing", "--quiet"]
    if python:
        venv_cmd += ["--python", python]
    try:
        _run([*venv_cmd, str(venv_dir)])
    except CalledProcessError as e:
        return RepoResult("python", Outcome.FAILED, f"creating the environment failed: {_get_error(e)}")

    failed = install_requirements(venv_dir / "bin" / "python", [path for path in requirements if path.is_file()])
    version = get_venv_python(venv_dir) or "?"
    if failed:
        return RepoResult(
            "python",
            Outcome.WARNING,
            f"Python {version}, without [b]{'[/b], [b]'.join(failed)}[/b], which only affects code completion, "
            "since Odoo runs in Docker",
        )
    return RepoResult("python", Outcome.UPDATED, f"Python {version} with all requirements")


def install_requirements(python: Path, requirement_files: list[Path]) -> list[str]:
    """Install requirement files into a virtual environment, skipping the requirements that fail to install.

    The requirements are installed with uv. The ones it can't install are tried with pip as well, which is more lenient
    with old source archives, like the malformed one of `PyPDF2==1.26.0`.

    :param python: The Python interpreter of the virtual environment.
    :param requirement_files: The requirement files to install.
    :return: The requirements that failed to install.
    """
    requirements = parse_requirements(requirement_files)
    if not requirements:
        return []
    install_cmd = [
        "uv",
        "pip",
        "install",
        "--quiet",
        "--python",
        str(python),
        "--build-constraint",
        str(BUILD_CONSTRAINTS_FILE),
    ]
    try:
        _run([*install_cmd, *requirements])
    except CalledProcessError:
        pass
    else:
        return []

    # Installing everything at once failed, so find out which requirements are to blame.
    failed: list[str] = []
    for requirement in requirements:
        try:
            _run([*install_cmd, requirement])
        except CalledProcessError:
            try:
                _run([str(python), "-m", "pip", "install", "--quiet", requirement])
            except CalledProcessError:
                failed.append(requirement.split(";")[0].strip())
    return failed


def parse_requirements(requirement_files: list[Path]) -> list[str]:
    """Get the requirements in requirement files, without comments, options and duplicates.

    The first file requiring a package decides its version, so Odoo's own pins win over the ones of the other files.

    :param requirement_files: The requirement files to read, the most important first.
    :return: The requirements, including their version specifiers and environment markers.
    """
    requirements: dict[str, None] = {}
    claimed: set[str] = set()
    for path in requirement_files:
        names: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            requirement = line.split(" #")[0].strip()
            if not requirement or requirement.startswith(("#", "-")):
                continue
            match = REQUIREMENT_NAME_PATTERN.match(requirement)
            name = re.sub(r"[-_.]+", "-", match.group(0)).lower() if match else requirement
            if name not in claimed:
                requirements[requirement] = None
                names.add(name)
        claimed |= names
    return list(requirements)


def get_venv_python(venv_dir: Path) -> str | None:
    """Get the `major.minor` Python version of a virtual environment."""
    config = venv_dir / "pyvenv.cfg"
    if not config.is_file():
        return None
    for line in config.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() in {"version_info", "version"}:
            return ".".join(value.strip().split(".")[:2])
    return None


def setup_js_tooling(version_dir: Path) -> RepoResult:
    """Set up the JavaScript tooling (ESLint, type checking) in the Community and Enterprise repositories."""
    com_dir = version_dir / "odoo"
    ent_dir = version_dir / "enterprise"
    tooling_dir = com_dir / "addons" / "web" / "tooling"
    if not tooling_dir.is_dir():
        return RepoResult("javascript", Outcome.SKIPPED, "no Community repository")
    if not shutil.which("npm"):
        return RepoResult("javascript", Outcome.WARNING, "[b]npm[/b] not found, so the tooling isn't installed")

    for source, target in JS_TOOLING_FILES.items():
        shutil.copyfile(tooling_dir / source, com_dir / target)
    try:
        _run(["npm", "install", "--silent"], cwd=com_dir)
        # The Odoo language server needs `tsserver` for JavaScript, which Odoo's tooling doesn't install. Not saved, so
        # it doesn't change the `package.json` that comes from Odoo. TypeScript 7 is a native rewrite without `tsserver`,
        # so this is the last version that has it.
        _run(["npm", "install", "--silent", "--no-save", "typescript@6"], cwd=com_dir)
    except CalledProcessError as e:
        return RepoResult("javascript", Outcome.FAILED, f"npm install failed: {_get_error(e)}")

    if ent_dir.is_dir():
        for source, target in JS_TOOLING_FILES.items():
            shutil.copyfile(tooling_dir / source, ent_dir / target)
        # Enterprise refers to the Community addons, so make the paths relative to Enterprise.
        jsconfig = ent_dir / "jsconfig.json"
        jsconfig.write_text(
            jsconfig.read_text(encoding="utf-8").replace("addons", f"{os.path.relpath(com_dir, ent_dir)}/addons"),
            encoding="utf-8",
        )
        # Reuse the Community dependencies instead of installing them twice.
        shutil.copyfile(com_dir / "package-lock.json", ent_dir / "package-lock.json")
        node_modules = ent_dir / "node_modules"
        if node_modules.is_dir() and not node_modules.is_symlink():
            # Earlier setups copied them.
            shutil.rmtree(node_modules)
        if not node_modules.is_symlink():
            node_modules.symlink_to(Path(os.path.relpath(com_dir, ent_dir)) / "node_modules", target_is_directory=True)
    return RepoResult("javascript", Outcome.UPDATED, "ESLint and type checking set up")


def configure_language_server(version_dir: Path) -> RepoResult:
    """Write the configuration of the Odoo language server for a version directory.

    The server gets the interpreter of the `.venv` and the paths of Odoo and the addons explicitly, relative to the
    workspace folder, which it expands: without them, it searches the whole version directory for them first, which
    takes seconds over a mounted folder. And it gets the `tsserver` of the JavaScript tooling, because it only finds one
    on the `PATH`, where there is none. That path is absolute, since it isn't known whether a command expands variables.
    It is the same on your machine and in a dev container, which mounts the multiverse at the same path.

    :param version_dir: The version directory.
    :return: The result of writing the configuration.
    """
    lines = ["[[config]]"]
    if (version_dir / ".venv" / "bin" / "python").exists():
        lines.append('python_path = "${workspaceFolder}/.venv/bin/python"')
    if (version_dir / "odoo" / "odoo" / "release.py").is_file():
        lines.append('odoo_path = "${workspaceFolder}/odoo"')
    if addons_paths := get_addons_paths(version_dir):
        paths = (f'  "${{workspaceFolder}}/{path.relative_to(version_dir).as_posix()}",' for path in addons_paths)
        lines += ["addons_paths = [", *paths, "]"]
    tsserver = version_dir / "odoo" / "node_modules" / ".bin" / "tsserver"
    if tsserver.exists():
        lines.append(f'tsserver_command = "{tsserver.absolute()}"')
    (version_dir / "odools.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return RepoResult("language server", Outcome.UPDATED, "wrote [b]odools.toml[/b]")


def write_ruff_config(version_dir: Path, *, reset: bool) -> RepoResult:
    """Write the Ruff configuration of a version directory, for the repositories that don't have one of their own.

    It follows the rules of the `ruff.toml` in Odoo's repository of that version. Without one, like for a version that
    predates it, it follows the ones of Odoo's master branch in the multiverse directory.

    :param version_dir: The version directory.
    :param reset: Whether to overwrite an existing configuration.
    :return: The result of writing the configuration.
    """
    config_file = version_dir / "ruff.toml"
    if config_file.exists() and not reset:
        return RepoResult("ruff", Outcome.SKIPPED, "already configured (use [b]--update-config[/b] to overwrite)")
    extend = "odoo/ruff.toml" if (version_dir / "odoo" / "ruff.toml").is_file() else f"../{ODOO_RUFF_FILE}"
    config_file.write_text(_get_ruff_config(extend), encoding="utf-8")
    return RepoResult("ruff", Outcome.UPDATED, "wrote [b]ruff.toml[/b]")


def write_vscode_config(version_dir: Path, *, reset: bool) -> RepoResult:
    """Write the Visual Studio Code configuration of a version directory.

    The configuration covers every repository linked into the version directory, including the ones linked by an
    earlier setup with other repositories selected, or with `otk mv link`.

    :param version_dir: The version directory.
    :param reset: Whether to overwrite existing configuration files.
    :return: The result of writing the configuration.
    """
    vscode_dir = version_dir / ".vscode"
    vscode_dir.mkdir(exist_ok=True)
    source_dir = MULTIVERSE_CONFIG_DIR / ".vscode"
    linked_repos = get_linked_repos(version_dir)

    settings = _read_json(source_dir / "settings.json")
    # Source Control doesn't look inside linked folders by itself.
    settings["git.scanRepositories"] = linked_repos

    # The debug configurations need no path mappings: `otk dev run` mounts the multiverse at the same path.
    files = {
        vscode_dir / "settings.json": settings,
        vscode_dir / "launch.json": _read_json(source_dir / "launch.json"),
        vscode_dir / "tasks.json": _read_json(source_dir / "tasks.json"),
        vscode_dir / "extensions.json": _read_json(source_dir / "extensions.json"),
    }
    written = [path.name for path, content in files.items() if _write_json(path, content, overwrite=reset)]
    # An existing settings file still needs the repositories linked since.
    update_scanned_repos(version_dir)

    if not written:
        return RepoResult("vscode", Outcome.SKIPPED, "already configured (use [b]--update-config[/b] to overwrite)")
    return RepoResult("vscode", Outcome.UPDATED, f"wrote [b]{'[/b], [b]'.join(written)}[/b]")


def write_devcontainer_config(version_dir: Path, *, reset: bool) -> RepoResult:
    """Write the dev container configuration of a version directory.

    A window opened in the dev container only sees the multiverse directory, which is mounted at the same path as on
    your machine, so the links between repositories and the Git worktrees keep working. Your home folder, SSH keys and
    Git credentials aren't available there, and VS Code's sharing of them is switched off as far as a project can.

    :param version_dir: The version directory.
    :param reset: Whether to overwrite an existing configuration.
    :return: The result of writing the configuration.
    """
    multiverse_dir = version_dir.parent
    if problem := check_mountable(multiverse_dir):
        return RepoResult("devcontainer", Outcome.WARNING, problem)
    config_file = version_dir / ".devcontainer" / "devcontainer.json"
    if config_file.exists() and not reset:
        return RepoResult(
            "devcontainer",
            Outcome.SKIPPED,
            "already configured (use [b]--update-config[/b] to overwrite)",
        )

    template = _read_json(MULTIVERSE_CONFIG_DIR / ".devcontainer" / "devcontainer.json")
    template["containerEnv"] = {
        "PYTHONPYCACHEPREFIX": VOLUMES["otk-pycache"],
        "CLAUDE_CONFIG_DIR": CLAUDE_DIR,
        **get_git_identity(),
        **template["containerEnv"],
    }
    # Inside the container, Odoo starts directly: `otk dev run` needs Docker, which isn't available there.
    launch = template["customizations"]["vscode"]["settings"]["launch"]["configurations"][0]
    launch["program"] = str(version_dir / "odoo" / "odoo-bin")
    odoo_args = get_odoo_args(version_dir)
    launch["args"] = [*odoo_args, "-d", "${workspaceFolderBasename}"]

    # The same extensions as the native setup recommends.
    recommended = _read_json(MULTIVERSE_CONFIG_DIR / ".vscode" / "extensions.json")["recommendations"]
    template["customizations"]["vscode"]["extensions"] += recommended
    # And the ones you add in `multiverse.toml`, which win over the standard settings.
    extra_extensions, extra_settings = get_devcontainer_extras(multiverse_dir)
    extensions: list[str] = template["customizations"]["vscode"]["extensions"]
    extensions += [extension for extension in extra_extensions if extension not in extensions]
    template["customizations"]["vscode"]["settings"].update(extra_settings)

    # What runs on your machine later, like the tasks and the dev container's own configuration, can only be changed from
    # your machine, so nothing in the container can use it to run code outside it.
    config_file.parent.mkdir(exist_ok=True)
    protected = [path for name in (".devcontainer", ".vscode", ".venv") if (path := version_dir / name).is_dir()]
    protected += _get_protected_git_paths(multiverse_dir)
    # How this container works, for Claude Code. It is in the read-only `.devcontainer` folder and mounted where Claude
    # Code reads its user instructions, so nothing in the container can change it.
    instructions_file = config_file.parent / "CLAUDE.md"
    instructions_file.write_text(
        _get_devcontainer_instructions(version_dir, odoo_args=odoo_args),
        encoding="utf-8",
    )

    config: dict[str, Any] = {
        "name": f"Odoo {version_dir.name}",
        "image": f"{DOCKER_REPOSITORY}:{version_dir.name}",
        "workspaceMount": f"source={multiverse_dir},target={multiverse_dir},type=bind",
        "workspaceFolder": str(version_dir),
        # Runs on your machine. The network only exists once the shared services are running, which `otk dev db` starts.
        # Checking that they already run takes a fraction of a second, where starting them again takes seconds.
        "initializeCommand": (
            f"[ \"$(docker inspect -f '{{{{.State.Health.Status}}}}' {DB_HOST} 2>/dev/null)\" = healthy ] "
            f"&& [ \"$(docker inspect -f '{{{{.State.Running}}}}' {SMTP_HOST} 2>/dev/null)\" = true ] "
            f"|| otk dev db > /dev/null 2>&1 || docker network inspect {NETWORK} > /dev/null 2>&1 "
            "|| { echo 'Run `otk dev db` first.' >&2; exit 1; }"
        ),
        "runArgs": [
            f"--name=otk-{version_dir.name}-dev",
            f"--network={NETWORK}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--init",
        ],
        "mounts": [
            *(f"source={volume},target={path},type=volume" for volume, path in DEVCONTAINER_VOLUMES.items()),
            *(f"source={path},target={path},type=bind,readonly" for path in protected),
            f"source={instructions_file},target={CLAUDE_DIR}/CLAUDE.md,type=bind,readonly",
        ],
        "forwardPorts": [ODOO_PORT],
        **template,
    }
    _write_json(config_file, config, overwrite=True)
    return RepoResult("devcontainer", Outcome.UPDATED, "wrote [b]devcontainer.json[/b]")


def _get_devcontainer_instructions(version_dir: Path, *, odoo_args: list[str]) -> str:
    """Get the instructions for Claude Code in the dev container of a version directory."""
    repos = sorted(path.name for path in version_dir.iterdir() if (path / ".git").exists())
    template = (MULTIVERSE_CONFIG_DIR / ".devcontainer" / "CLAUDE.md").read_text(encoding="utf-8")
    return Template(template).safe_substitute(
        version=version_dir.name,
        multiverse_dir=version_dir.parent,
        version_dir=version_dir,
        repos=", ".join(f"`{repo}`" for repo in repos) or "`odoo`",
        odoo_bin=version_dir / "odoo" / "odoo-bin",
        odoo_args=" ".join(odoo_args),
        db_host=DB_HOST,
        smtp_host=SMTP_HOST,
    )


def _get_protected_git_paths(multiverse_dir: Path) -> list[Path]:
    """Get what a bare repository holds that Git runs on your machine: its hooks and its configuration.

    Every dev container sees all the bare repositories, since the worktrees of all versions share them, and a hook or a
    setting like `core.fsmonitor` or an alias would run the next time you use Git on your machine. A missing `hooks`
    folder is created, since a bind mount needs something to mount.
    """
    paths: list[Path] = []
    for bare_dir in sorted((multiverse_dir / WORKTREE_SOURCE_DIR).glob("*/.bare")):
        (bare_dir / "hooks").mkdir(exist_ok=True)
        paths += [bare_dir / "config", bare_dir / "hooks"]
    return paths


def print_devcontainer_notes() -> None:
    """Explain what a dev container doesn't cover, and the settings that only you can change."""
    console.print(
        "[b]Dev containers[/b] are opened with [b]Dev Containers: Reopen in Container[/b]. They only isolate windows "
        "opened that way, not agents started from a terminal on your machine.\n\n"
        "Visual Studio Code shares your Git credential helper and Git config with every dev container, which a project "
        "can't turn off. To keep your GitHub login out, add this to your user settings:\n"
        '  "dev.containers.gitCredentialHelperConfigLocation": "none"\n\n'
        "Your Git config can hold secrets too, so unless you've checked yours, also add:\n"
        '  "dev.containers.copyGitConfig": false\n\n'
        "To sign commits in a dev container, you need it on instead: see the README of odoo-toolkit.\n",
    )


def update_scanned_repos(version_dir: Path) -> None:
    """Make Visual Studio Code's Source Control show the repositories currently linked into a version directory.

    Only that setting is changed, so the rest of your settings stay as they are.
    """
    settings_file = version_dir / ".vscode" / "settings.json"
    if not settings_file.is_file():
        return
    try:
        settings = _read_json(settings_file)
    except json.JSONDecodeError:
        # Visual Studio Code allows comments in its settings, which Python can't read. Leave the file alone.
        return
    linked_repos = get_linked_repos(version_dir)
    if settings.get("git.scanRepositories") != linked_repos:
        settings["git.scanRepositories"] = linked_repos
        _write_json(settings_file, settings, overwrite=True)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _write_json(path: Path, content: dict[str, Any], *, overwrite: bool) -> bool:
    """Write a JSON file, unless it exists and shouldn't be overwritten.

    :return: Whether the file was written.
    """
    if path.exists() and not overwrite:
        return False
    path.write_text(json.dumps(content, indent=4) + "\n", encoding="utf-8")
    return True


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    """Run a command, capturing its output.

    :raises CalledProcessError: If the command failed.
    """
    subprocess.run(cmd, cwd=cwd, capture_output=True, check=True, text=True)


def _get_error(error: CalledProcessError) -> str:
    """Get the last line a failed command printed, which says why it failed, or else the error itself."""
    lines = (error.stderr or "").strip().splitlines()
    return lines[-1] if lines else str(error)
