"""The repositories of a multiverse, and how they are laid out.

```
<multiverse>/
├── multiverse.toml        optional: extra repositories and exceptions to the defaults below
├── .worktree-source/      the bare repositories of the versioned repositories
├── upgrade/  odoofin/ …   the clones of the shared, pinned and standalone repositories
└── 19.0/                  a version directory
    ├── odoo/ …            worktrees of the versioned repositories
    └── upgrade -> ../upgrade, …   links to the shared repositories, and to the pinned repositories running on this version
```
"""

import os
import re
import tomllib
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import requests

from odoo_toolkit.common import HTTP_HEADERS
from odoo_toolkit.git_cli import Git, GitError

CONFIG_FILE = "multiverse.toml"
WORKTREE_SOURCE_DIR = ".worktree-source"
# The URL to clone a repository from, given its GitHub owner and name.
GIT_URL = "git@github.com:{owner}/{repo}.git"
# Matches an Odoo version, like `19.0`, `saas-19.2` or `master`.
VERSION_PATTERN = re.compile(r"master|saas-\d+\.\d+|\d+\.\d+")
# Matches the Odoo version at the start of a module version, like `20.0` in `20.0.1.0`, or `saas~19.3` in
# `saas~19.3.1.0`.
MANIFEST_VERSION_PATTERN = re.compile(r"""["']version["']\s*:\s*["']((?:saas~)?\d+\.\d+)\.\d""")
# The error shown by the commands that only work inside a version directory.
NOT_IN_VERSION_DIR = "Run this command inside an Odoo Multiverse version directory (like [u]~/code/odoo/19.0[/u]).\n"
# The `runs_on` value of repositories that run on the version odoo.com runs on.
ODOO_COM = "odoo.com"
ODOO_COM_VERSION_URL = "https://www.odoo.com/web/webclient/version_info"


class RepoKind(StrEnum):
    """How a repository is laid out in the multiverse."""

    # One worktree per version, on the version's branch.
    VERSIONED = "versioned"
    # One clone on the default branch, linked into every version directory.
    SHARED = "shared"
    # One clone on the default branch, pinned to one Odoo version: linked into that version directory only.
    PINNED = "pinned"
    # One clone on the default branch, not linked into any version directory.
    STANDALONE = "standalone"


@dataclass(frozen=True)
class RepoSpec:
    """A repository of the multiverse."""

    name: str
    # How the repository is laid out, or None to detect it.
    kind: RepoKind | None
    owner: str = "odoo"
    # Whether the repository has a fork on `odoo-dev`, added as the `dev` remote, or None to detect it.
    dev_remote: bool | None = False
    # The Odoo version a pinned repository runs on, or None to detect it.
    runs_on: str | None = None
    # The version directories a pinned repository is always linked into, on top of the one it runs on.
    also_in: tuple[str, ...] = field(default_factory=tuple[str, ...])
    # The only versions a versioned repository gets a worktree in, or None for every version having its branch.
    versions: tuple[str, ...] | None = None

    @property
    def url(self) -> str:
        """The URL to clone the repository from."""
        return GIT_URL.format(owner=self.owner, repo=self.name)

    def dev_url(self) -> str:
        """Get the URL of the repository's fork on `odoo-dev`."""
        return GIT_URL.format(owner="odoo-dev", repo=self.name)


# The defaults for the known Odoo repositories.
KNOWN_REPOS: dict[str, RepoSpec] = {
    spec.name: spec
    for spec in (
        RepoSpec("odoo", RepoKind.VERSIONED, dev_remote=True),
        RepoSpec("enterprise", RepoKind.VERSIONED, dev_remote=True),
        RepoSpec("design-themes", RepoKind.VERSIONED, dev_remote=True),
        RepoSpec("documentation", RepoKind.VERSIONED),
        RepoSpec("industry", RepoKind.VERSIONED, dev_remote=True),
        RepoSpec("o-spreadsheet", RepoKind.VERSIONED),
        RepoSpec("upgrade", RepoKind.SHARED),
        RepoSpec("upgrade-util", RepoKind.SHARED, dev_remote=True),
        # Runs on the version odoo.com runs on.
        RepoSpec("internal", RepoKind.PINNED, runs_on=ODOO_COM),
        RepoSpec("iap-apps", RepoKind.PINNED),
        RepoSpec("mobile", RepoKind.STANDALONE),
        RepoSpec("odoofin", RepoKind.PINNED),
    )
}
DEFAULT_REPOS = ["odoo", "enterprise", "design-themes", "upgrade", "upgrade-util"]


class ConfigError(Exception):
    """The multiverse configuration is invalid."""


def load_config(multiverse_dir: Path) -> dict[str, RepoSpec]:
    """Get the repositories the multiverse knows: the known Odoo repositories, and the ones in `multiverse.toml`.

    `multiverse.toml` can add repositories and override the defaults of known ones, like:

    ```toml
    [repos.i18n-apps]
    kind = "pinned"
    runs_on = "20.0"

    [repos.odoofin]
    also_in = ["19.0"]

    [repos.runbot]
    versions = ["17.0", "19.0"]
    ```

    :param multiverse_dir: The multiverse directory.
    :raises ConfigError: If `multiverse.toml` is invalid.
    :return: The repositories by name.
    """
    specs = dict(KNOWN_REPOS)
    config_file = multiverse_dir / CONFIG_FILE
    if not config_file.is_file():
        return specs
    try:
        config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        msg = f"{config_file} is not valid TOML: {e}"
        raise ConfigError(msg) from e

    repos = config.get("repos", {})
    if not isinstance(repos, dict):
        msg = f"`repos` in {config_file} must be a table, like [repos.<name>]."
        raise ConfigError(msg)
    for name, values in cast("dict[str, Any]", repos).items():
        if not isinstance(values, dict):
            msg = f"[repos.{name}] in {config_file} must be a table."
            raise ConfigError(msg)
        settings = cast("dict[str, Any]", values)
        # What isn't configured for a repository the multiverse doesn't know is detected.
        base = specs.get(name) or RepoSpec(name, kind=None, dev_remote=None)
        specs[name] = _apply_overrides(base, settings, config_file)
    return specs


def get_code_profile(multiverse_dir: Path) -> str | None:
    """Get the Visual Studio Code profile `otk code` opens the versions with: `profile` in `[code]` of `multiverse.toml`.

    :param multiverse_dir: The multiverse directory.
    :return: The profile name, or None if there is none.
    """
    try:
        config = tomllib.loads((multiverse_dir / CONFIG_FILE).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    code = config.get("code")
    profile = cast("dict[str, Any]", code).get("profile") if isinstance(code, dict) else None
    return profile if isinstance(profile, str) else None


def get_devcontainer_extras(multiverse_dir: Path) -> tuple[list[str], dict[str, Any]]:
    """Get the extensions and settings your dev containers get on top of the standard ones.

    They are the `extensions` and `settings` in the `[devcontainer]` table of `multiverse.toml`:

    ```toml
    [devcontainer]
    extensions = ["streetsidesoftware.code-spell-checker"]

    [devcontainer.settings]
    "cSpell.allowCompoundWords" = true
    ```

    :param multiverse_dir: The multiverse directory.
    :return: The extensions and the settings, which are empty if there are none.
    """
    try:
        config = tomllib.loads((multiverse_dir / CONFIG_FILE).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return [], {}
    table = config.get("devcontainer")
    values = cast("dict[str, Any]", table) if isinstance(table, dict) else {}
    extensions = values.get("extensions")
    settings = values.get("settings")
    return (
        [extension for extension in cast("list[Any]", extensions) if isinstance(extension, str)]
        if isinstance(extensions, list)
        else [],
        cast("dict[str, Any]", settings) if isinstance(settings, dict) else {},
    )


def get_configured_repos(multiverse_dir: Path) -> list[str]:
    """Get the names of the repositories with a `[repos.<name>]` table in `multiverse.toml`."""
    config_file = multiverse_dir / CONFIG_FILE
    if not config_file.is_file():
        return []
    try:
        repos = tomllib.loads(config_file.read_text(encoding="utf-8")).get("repos", {})
    except tomllib.TOMLDecodeError:
        return []
    return list(cast("dict[str, Any]", repos)) if isinstance(repos, dict) else []


def _apply_overrides(spec: RepoSpec, values: dict[str, Any], config_file: Path) -> RepoSpec:
    allowed = {"owner": str, "kind": str, "dev_remote": bool, "runs_on": str, "also_in": list, "versions": list}
    for key, value in values.items():
        if key not in allowed or not isinstance(value, allowed[key]):
            msg = (
                f"Invalid setting `{key}` for [repos.{spec.name}] in {config_file}. "
                f"Allowed: {', '.join(f'{k} ({t.__name__})' for k, t in allowed.items())}."
            )
            raise ConfigError(msg)
    if "kind" in values and values["kind"] not in set(RepoKind):
        msg = f"Invalid kind `{values['kind']}` for [repos.{spec.name}] in {config_file}: use {', '.join(RepoKind)}."
        raise ConfigError(msg)
    return replace(
        spec,
        owner=values.get("owner", spec.owner),
        kind=RepoKind(values["kind"]) if "kind" in values else spec.kind,
        dev_remote=values.get("dev_remote", spec.dev_remote),
        runs_on=values.get("runs_on", spec.runs_on),
        also_in=tuple(str(version) for version in values.get("also_in", spec.also_in)),
        versions=tuple(str(version) for version in values["versions"]) if "versions" in values else spec.versions,
    )


def resolve_repo(argument: str, specs: dict[str, RepoSpec], multiverse_dir: Path) -> RepoSpec:
    """Get the repository for a command line argument, like `odoo`, `i18n-apps` or `odoo-ps/psbe-internal`.

    A repository that's already set up keeps its layout and owner. What isn't known about a new repository is detected:
    it's versioned if it has branches for several Odoo versions, and shared otherwise, and its `odoo-dev` fork is used
    if you can access it. Pinned and standalone repositories must be configured in `multiverse.toml`.

    :param argument: The repository name, optionally prefixed with its GitHub owner.
    :param specs: The known repositories.
    :param multiverse_dir: The multiverse directory, to find out how repositories that are already set up are laid out.
    :raises ConfigError: If a new repository's branches can't be listed, to detect its layout.
    :return: The repository.
    """
    owner, _, name = argument.rpartition("/")
    spec = specs.get(name) or RepoSpec(name, kind=None, dev_remote=None)
    bare_dir = multiverse_dir / WORKTREE_SOURCE_DIR / name / ".bare"
    clone_dir = multiverse_dir / name
    existing = Git(bare_dir) if bare_dir.is_dir() else Git(clone_dir) if (clone_dir / ".git").is_dir() else None
    if owner:
        spec = replace(spec, owner=owner)
    elif existing and (existing_owner := get_remote_owner(existing.remote_url("origin"))):
        spec = replace(spec, owner=existing_owner)
    if spec.kind is None and existing:
        spec = replace(spec, kind=RepoKind.VERSIONED if bare_dir.is_dir() else RepoKind.SHARED)
    if spec.kind is None:
        try:
            output = _ls_remote("--heads", spec.url)
        except GitError as e:
            msg = f"Can't list the branches of {spec.url} to tell how to set it up: {e.message}"
            raise ConfigError(msg) from e
        branches = [line.rpartition("refs/heads/")[2] for line in output.splitlines()]
        version_branches = [branch for branch in branches if VERSION_PATTERN.fullmatch(branch)]
        spec = replace(spec, kind=RepoKind.VERSIONED if len(version_branches) > 1 else RepoKind.SHARED)
    if spec.dev_remote is None:
        if existing:
            spec = replace(spec, dev_remote="dev" in existing.remotes())
        else:
            # The fork only exists for some repositories, and you may not have access to it.
            try:
                _ls_remote(spec.dev_url(), "HEAD")
            except GitError:
                spec = replace(spec, dev_remote=False)
            else:
                spec = replace(spec, dev_remote=True)
    return spec


def _ls_remote(*args: str) -> str:
    """List the references of a remote repository given by its URL.

    :raises GitError: If the repository doesn't exist or can't be reached.
    """
    return Git(Path.home())("ls-remote", *args)


def get_remote_owner(url: str) -> str | None:
    """Get the owner of a remote's repository, like `odoo-dev` for `git@github.com:odoo-dev/odoo.git`."""
    match = re.search(r"[:/]([^/:]+)/[^/:]+?(?:\.git)?/?$", url)
    return match.group(1) if match else None


def get_runs_on(spec: RepoSpec, repo_dir: Path, branch: str | None) -> str | None:
    """Get the Odoo version a pinned repository runs on.

    That's its `runs_on` setting (where `odoo.com` means the version odoo.com runs on), or else its branch if that's a
    version, or else the version of its modules.

    :param spec: The repository.
    :param repo_dir: The repository's clone.
    :param branch: The repository's branch.
    :return: The Odoo version, or None if it can't be determined.
    """
    if spec.runs_on == ODOO_COM:
        return get_odoo_com_version()
    if spec.runs_on:
        return spec.runs_on
    return detect_runs_on(repo_dir, branch)


def get_odoo_com_version() -> str | None:
    """Get the Odoo version odoo.com runs on, like `saas-19.3`, or None if odoo.com can't be reached."""
    try:
        response = requests.post(
            ODOO_COM_VERSION_URL,
            json={"jsonrpc": "2.0", "method": "call", "params": {}},
            headers=HTTP_HEADERS,
            timeout=15,
        )
        response.raise_for_status()
        serie: str = response.json()["result"]["server_serie"]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None
    return serie.replace("~", "-")


def detect_runs_on(repo_dir: Path, branch: str | None) -> str | None:
    """Detect the Odoo version a pinned repository runs on: its branch's version, or else its modules' versions.

    The branch's version is the branch itself, like `19.0`, or the version a feature branch starts with, like `19.0` in
    `19.0-fix-something-abcd`.

    :param repo_dir: The repository's clone.
    :param branch: The repository's branch.
    :return: The Odoo version, or None if it can't be detected.
    """
    if branch and (match := re.match(rf"({VERSION_PATTERN.pattern})(?:-|$)", branch)) and match.group(1) != "master":
        return match.group(1)
    versions: dict[str, int] = {}
    for manifest in repo_dir.glob("*/__manifest__.py"):
        if match := MANIFEST_VERSION_PATTERN.search(manifest.read_text(encoding="utf-8", errors="replace")):
            version = match.group(1).replace("~", "-")
            versions[version] = versions.get(version, 0) + 1
    return max(versions, key=lambda version: versions[version]) if versions else None


def get_current_dir() -> Path:
    """Get the current directory as you see it in your terminal, without resolving links.

    Inside a linked repository, like `19.0/upgrade`, that's still inside the version directory, while the operating
    system only knows the directory the link points to.
    """
    logical = os.environ.get("PWD")
    if logical and Path(logical).is_absolute() and Path(logical).exists() and Path(logical).samefile(Path.cwd()):
        return Path(logical)
    return Path.cwd()


def find_multiverse_dir(start: Path) -> Path | None:
    """Find the multiverse directory containing a path, recognized by its `.worktree-source` directory."""
    # Not `resolve()`, which would follow links out of the version directory.
    start = Path(os.path.abspath(start.expanduser()))  # noqa: PTH100
    return next((d for d in (start, *start.parents) if (d / WORKTREE_SOURCE_DIR).is_dir()), None)


def find_version_dir(start: Path | None = None) -> Path | None:
    """Find the version directory (like `19.0`) containing a path.

    Links aren't resolved, so a linked repository inside a version directory is part of it.

    :param start: The path to start searching from, or None for the current directory.
    :return: The version directory, or None if the path is not inside one.
    """
    start = Path(os.path.abspath((start or get_current_dir()).expanduser()))  # noqa: PTH100
    for directory in (start, *start.parents):
        # The other repositories sit next to the version directories, but are repositories themselves.
        if (
            (directory.parent / WORKTREE_SOURCE_DIR).is_dir()
            and directory.name != WORKTREE_SOURCE_DIR
            and not (directory / ".git").exists()
        ):
            return directory
    return None


def get_default_repo_path(path: Path, name: str) -> Path:
    """Get the path of a repository given as option, like `--com-path`.

    Its default (a relative path like `odoo`) means the repository of the version directory you're in, if any, so the
    commands work anywhere inside it.

    :param path: The path given as option.
    :param name: The repository name, which is also the default path.
    :return: The path to use.
    """
    if path == Path(name) and (version_dir := find_version_dir()) and (version_dir / name).is_dir():
        return version_dir / name
    return path


def get_repo_dirs(version_dir: Path, *, include_linked: bool) -> list[Path]:
    """Get the repositories inside a version directory.

    :param version_dir: The version directory.
    :param include_linked: Whether to include the shared and pinned repositories, which aren't on the version's branch.
    :return: The repository directories: first the worktrees, then the shared and pinned repositories.
    """
    repo_dirs = sorted(path for path in version_dir.iterdir() if (path / ".git").exists())
    worktrees = [path for path in repo_dirs if is_worktree(path)]
    linked = [path for path in repo_dirs if not is_worktree(path)]
    return worktrees + (linked if include_linked else [])


def is_worktree(repo_dir: Path) -> bool:
    """Check whether a repository in a version directory is a worktree of a versioned repository.

    The other ones are shared repositories linked into it, and pinned repositories, which live in the version they run
    on and are linked into the other ones.
    """
    return not repo_dir.is_symlink() and (repo_dir / ".git").is_file()


def get_linked_repos(version_dir: Path) -> list[str]:
    """Get the names of the repositories linked into a version directory."""
    return [path.name for path in get_repo_dirs(version_dir, include_linked=True) if path.is_symlink()]
