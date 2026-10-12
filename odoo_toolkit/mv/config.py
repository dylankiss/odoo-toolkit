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

    repos: dict[str, Any] = config.get("repos", {})
    for name, values in repos.items():
        if not isinstance(values, dict):
            msg = f"[repos.{name}] in {config_file} must be a table."
            raise ConfigError(msg)
        settings = cast("dict[str, Any]", values)
        # What isn't configured for a repository the multiverse doesn't know is detected.
        base = specs.get(name) or RepoSpec(name, kind=None, dev_remote=None)
        specs[name] = _apply_overrides(base, settings, config_file)
    return specs


def get_configured_repos(multiverse_dir: Path) -> list[str]:
    """Get the names of the repositories with a `[repos.<name>]` table in `multiverse.toml`."""
    config_file = multiverse_dir / CONFIG_FILE
    if not config_file.is_file():
        return []
    try:
        return list(tomllib.loads(config_file.read_text(encoding="utf-8")).get("repos", {}))
    except tomllib.TOMLDecodeError:
        return []


def _apply_overrides(spec: RepoSpec, values: dict[str, Any], config_file: Path) -> RepoSpec:
    allowed = {"owner": str, "kind": str, "dev_remote": bool, "runs_on": str, "also_in": list}
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
    )


def resolve_repo(argument: str, specs: dict[str, RepoSpec]) -> RepoSpec:
    """Get the repository for a command line argument, like `odoo`, `i18n-apps` or `odoo-ps/psbe-internal`.

    What isn't known about a repository is detected: it's versioned if it has branches for several Odoo versions, and
    shared otherwise, and its `odoo-dev` fork is used if you can access it. Pinned and standalone repositories must
    be configured in `multiverse.toml`.

    :param argument: The repository name, optionally prefixed with its GitHub owner.
    :param specs: The known repositories.
    :return: The repository.
    """
    owner, _, name = argument.rpartition("/")
    spec = specs.get(name) or RepoSpec(name, kind=None, dev_remote=None)
    if owner:
        spec = replace(spec, owner=owner)
    if spec.kind is None:
        branches = [line.rpartition("refs/heads/")[2] for line in _ls_remote("--heads", spec.url).splitlines()]
        version_branches = [branch for branch in branches if VERSION_PATTERN.fullmatch(branch)]
        spec = replace(spec, kind=RepoKind.VERSIONED if len(version_branches) > 1 else RepoKind.SHARED)
    if spec.dev_remote is None:
        # The fork only exists for some repositories, and you may not have access to it.
        spec = replace(spec, dev_remote=_remote_exists(spec.dev_url()))
    return spec


def _ls_remote(*args: str) -> str:
    try:
        return Git(Path.cwd())("ls-remote", *args)
    except GitError:
        return ""


def _remote_exists(url: str) -> bool:
    """Check whether a remote repository exists and is accessible."""
    try:
        Git(Path.cwd())("ls-remote", url, "HEAD")
    except GitError:
        return False
    return True


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
    """Detect the Odoo version a pinned repository runs on: its branch if that's a version, or else its modules' versions.

    :param repo_dir: The repository's clone.
    :param branch: The repository's branch.
    :return: The Odoo version, or None if it can't be detected.
    """
    if branch and VERSION_PATTERN.fullmatch(branch) and branch != "master":
        return branch
    versions: dict[str, int] = {}
    for manifest in repo_dir.glob("*/__manifest__.py"):
        if match := MANIFEST_VERSION_PATTERN.search(manifest.read_text(encoding="utf-8", errors="replace")):
            version = match.group(1).replace("~", "-")
            versions[version] = versions.get(version, 0) + 1
    return max(versions, key=lambda version: versions[version]) if versions else None


def find_multiverse_dir(start: Path) -> Path | None:
    """Find the multiverse directory containing a path, recognized by its `.worktree-source` directory."""
    start = start.expanduser().resolve()
    return next((d for d in (start, *start.parents) if (d / WORKTREE_SOURCE_DIR).is_dir()), None)


def find_version_dir(start: Path) -> Path | None:
    """Find the version directory (like `19.0`) containing a path.

    :param start: The path to start searching from, usually the current working directory.
    :return: The version directory, or None if the path is not inside one.
    """
    start = start.expanduser().resolve()
    for directory in (start, *start.parents):
        # The other repositories sit next to the version directories, but are repositories themselves.
        if (
            (directory.parent / WORKTREE_SOURCE_DIR).is_dir()
            and directory.name != WORKTREE_SOURCE_DIR
            and not (directory / ".git").exists()
        ):
            return directory
    return None


def get_repo_dirs(version_dir: Path, *, include_linked: bool) -> list[Path]:
    """Get the repositories inside a version directory.

    :param version_dir: The version directory.
    :param include_linked: Whether to include the linked shared and pinned repositories, which other versions use too.
    :return: The repository directories: first the worktrees, then the linked repositories.
    """
    repo_dirs = sorted(path for path in version_dir.iterdir() if (path / ".git").exists())
    worktrees = [path for path in repo_dirs if not path.is_symlink()]
    linked = [path for path in repo_dirs if path.is_symlink()]
    return worktrees + (linked if include_linked else [])


def get_linked_repos(version_dir: Path) -> list[str]:
    """Get the names of the repositories linked into a version directory."""
    return [path.name for path in get_repo_dirs(version_dir, include_linked=True) if path.is_symlink()]
