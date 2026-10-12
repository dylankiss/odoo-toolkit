import json
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from odoo_toolkit.main import app
from odoo_toolkit.mv import config as mv_config
from odoo_toolkit.mv.config import (
    KNOWN_REPOS,
    ConfigError,
    RepoKind,
    detect_runs_on,
    find_version_dir,
    get_repo_dirs,
    get_runs_on,
    load_config,
    resolve_repo,
)
from odoo_toolkit.mv.switch import get_remote_owner
from tests.mv.conftest import git


def test_load_config_overrides_and_adds_repos(tmp_path: Path) -> None:
    (tmp_path / "multiverse.toml").write_text(
        '[repos.odoofin]\nalso_in = ["19.0"]\n\n'
        '[repos.i18n-apps]\nkind = "pinned"\nruns_on = "20.0"\n',
    )

    specs = load_config(tmp_path)

    assert specs["odoofin"].kind == RepoKind.PINNED
    assert specs["odoofin"].also_in == ("19.0",)
    assert specs["i18n-apps"].kind == RepoKind.PINNED
    assert specs["i18n-apps"].runs_on == "20.0"
    assert specs["i18n-apps"].owner == "odoo"
    assert specs["odoo"].kind == RepoKind.VERSIONED


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ('[repos.x]\nkind = "weird"\n', "Invalid kind `weird`"),
        ("[repos.x]\nruns_on = 20.0\n", "Invalid setting `runs_on`"),
        ("[repos.x]\nunknown = true\n", "Invalid setting `unknown`"),
        ("[repos\n", "is not valid TOML"),
    ],
)
def test_load_config_rejects_invalid_settings(tmp_path: Path, content: str, error: str) -> None:
    (tmp_path / "multiverse.toml").write_text(content)
    with pytest.raises(ConfigError, match=error):
        load_config(tmp_path)


def test_resolve_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remotes = tmp_path / "remotes"
    for owner, repo, branches in [
        ("odoo-ps", "psbe-internal", ["master", "19.0"]),
        ("odoo-ps", "old-versions", ["18.0", "19.0", "tmp.19.0"]),
        ("odoo-ps", "one-version", ["18.0", "feature-x"]),
        ("odoo", "tools", ["main"]),
        ("odoo-dev", "tools", ["main"]),
    ]:
        seed = tmp_path / "seed" / owner / repo
        git(tmp_path, "init", "-q", str(seed))
        git(seed, "commit", "-q", "--allow-empty", "-m", "init")
        git(tmp_path, "init", "-q", "--bare", str(remotes / owner / f"{repo}.git"))
        for branch in branches:
            git(seed, "push", "-q", str(remotes / owner / f"{repo}.git"), f"HEAD:{branch}")
    monkeypatch.setattr(mv_config, "GIT_URL", f"file://{remotes}/{{owner}}/{{repo}}.git")
    specs = load_config(tmp_path)

    # Versioned, because it has branches for several versions, even if none of them is the one being set up.
    assert resolve_repo("odoo-ps/old-versions", specs).kind == RepoKind.VERSIONED
    # Shared, because it has only one version branch.
    assert resolve_repo("odoo-ps/one-version", specs).kind == RepoKind.SHARED
    # Versioned, because it has branches for several versions.
    versioned = resolve_repo("odoo-ps/psbe-internal", specs)
    assert (versioned.kind, versioned.owner, versioned.dev_remote) == (RepoKind.VERSIONED, "odoo-ps", False)
    # Shared, with the `odoo-dev` fork detected.
    shared = resolve_repo("tools", specs)
    assert (shared.kind, shared.owner, shared.dev_remote) == (RepoKind.SHARED, "odoo", True)
    # What isn't configured in `multiverse.toml` is detected as well.
    (tmp_path / "multiverse.toml").write_text('[repos.psbe-internal]\nowner = "odoo-ps"\n\n[repos.tools]\n')
    specs = load_config(tmp_path)
    assert specs["tools"].kind is None
    assert resolve_repo("psbe-internal", specs).kind == RepoKind.VERSIONED
    configured = resolve_repo("tools", specs)
    assert (configured.kind, configured.dev_remote) == (RepoKind.SHARED, True)
    # Known repositories keep their defaults, with another owner if given.
    assert resolve_repo("odoo", specs) == specs["odoo"]
    assert resolve_repo("my-fork/odoo", specs).owner == "my-fork"


def test_get_runs_on_odoo_com(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, dict[str, str]]:
            return {"result": {"server_serie": "saas~19.3"}}

    def post(*_: object, **__: object) -> Response:
        return Response()

    monkeypatch.setattr(mv_config.requests, "post", post)
    assert get_runs_on(KNOWN_REPOS["internal"], tmp_path, "master") == "saas-19.3"

    def unreachable(*_: object, **__: object) -> Response:
        raise mv_config.requests.ConnectionError

    monkeypatch.setattr(mv_config.requests, "post", unreachable)
    assert get_runs_on(KNOWN_REPOS["internal"], tmp_path, "master") is None
    # An explicit version is used as is, without asking anything.
    assert get_runs_on(replace(KNOWN_REPOS["odoofin"], runs_on="19.0"), tmp_path, "18.0") == "19.0"


def test_detect_runs_on(tmp_path: Path) -> None:
    assert detect_runs_on(tmp_path, "18.0") == "18.0"
    assert detect_runs_on(tmp_path, "saas-19.2") == "saas-19.2"
    assert detect_runs_on(tmp_path, "master") is None
    for module, version in [("a", "20.0.1.0"), ("b", "20.0.2.1"), ("c", "1.0")]:
        (tmp_path / module).mkdir()
        (tmp_path / module / "__manifest__.py").write_text(f"{{'name': '{module}', 'version': '{version}'}}")
    assert detect_runs_on(tmp_path, "master") == "20.0"
    for module in ("d", "e", "f"):
        (tmp_path / module).mkdir()
        (tmp_path / module / "__manifest__.py").write_text("{'version': 'saas~19.3.1.0'}")
    assert detect_runs_on(tmp_path, "master") == "saas-19.3"


def test_find_version_dir_skips_shared_repos(multiverse: Path) -> None:
    (multiverse / "upgrade" / ".git").mkdir(parents=True)
    assert find_version_dir(multiverse / "upgrade") is None
    assert find_version_dir(multiverse / "19.0" / "odoo") == (multiverse / "19.0").resolve()


def test_get_repo_dirs_from_layout(multiverse: Path) -> None:
    (multiverse / "upgrade" / ".git").mkdir(parents=True)
    (multiverse / "19.0" / "upgrade").symlink_to(Path("..") / "upgrade")
    (multiverse / "19.0" / "my-notes").mkdir()

    assert [p.name for p in get_repo_dirs(multiverse / "19.0", include_linked=False)] == ["odoo"]
    assert [p.name for p in get_repo_dirs(multiverse / "19.0", include_linked=True)] == ["odoo", "upgrade"]


@pytest.mark.parametrize(
    ("url", "owner"),
    [
        ("git@github.com:odoo-dev/odoo.git", "odoo-dev"),
        ("https://github.com/odoo/enterprise.git", "odoo"),
        ("https://github.com/odoo/enterprise", "odoo"),
        ("/home/me/remotes/odoo-dev/odoo.git", "odoo-dev"),
    ],
)
def test_get_remote_owner(url: str, owner: str) -> None:
    assert get_remote_owner(url) == owner


def test_cli_link_and_unlink(multiverse: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (multiverse / "odoofin" / ".git").mkdir(parents=True)
    settings = multiverse / "19.0" / ".vscode" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"editor.rulers": [120]}')
    monkeypatch.chdir(multiverse / "19.0" / "odoo")
    runner = CliRunner()

    result = runner.invoke(app, ["mv", "link", "odoofin"])

    assert result.exit_code == 0, result.output
    assert (multiverse / "19.0" / "odoofin").readlink() == Path("../odoofin")
    assert json.loads(settings.read_text()) == {"editor.rulers": [120], "git.scanRepositories": ["odoofin"]}

    result = runner.invoke(app, ["mv", "unlink", "odoofin"])

    assert result.exit_code == 0, result.output
    assert not (multiverse / "19.0" / "odoofin").exists()
    assert (multiverse / "odoofin").is_dir()
    assert json.loads(settings.read_text())["git.scanRepositories"] == []
    assert runner.invoke(app, ["mv", "link", "missing"]).exit_code == 1
    assert runner.invoke(app, ["mv", "unlink", "odoofin"]).exit_code == 1
