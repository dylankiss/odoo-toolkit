import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from odoo_toolkit.git_cli import Git
from odoo_toolkit.main import app
from odoo_toolkit.mv import config as mv_config
from odoo_toolkit.mv import setup as mv_setup
from odoo_toolkit.mv.config import KNOWN_REPOS
from odoo_toolkit.mv.repos import Outcome, RepoResult


def git(cwd: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True).stdout.strip()


def stub_slow_steps(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub the Python environment and JavaScript tooling steps, which need the network and take minutes.

    :return: The names of the version directories the Python environment step was called for.
    """
    calls: list[str] = []

    def setup_python_env(version_dir: Path, **_: object) -> RepoResult:
        calls.append(version_dir.name)
        return RepoResult("python", Outcome.UPDATED, "stub")

    def setup_js_tooling(_: Path) -> RepoResult:
        return RepoResult("javascript", Outcome.SKIPPED, "stub")

    monkeypatch.setattr(mv_setup, "setup_python_env", setup_python_env)
    monkeypatch.setattr(mv_setup, "setup_js_tooling", setup_js_tooling)
    return calls


@pytest.fixture
def remotes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create local GitHub-like remotes, and clone from them.

    - `odoo/odoo` has version branches, and staging and forward-port branches that must not be fetched.
    - `odoo/upgrade` is a shared repository.
    - `odoo/odoofin` is a pinned repository with version branches, running on its default branch `18.0`.
    - `odoo/i18n-apps` is a pinned repository on `master`, with modules for Odoo 20.0.
    - `odoo/mobile` is a standalone repository.
    """
    remotes_dir = tmp_path / "remotes"
    seed = tmp_path / "seed"
    remote_branches: list[tuple[str, str, list[str]]] = [
        ("odoo", "odoo", ["master", "19.0", "20.0", "tmp.19.0", "staging.19.0", "saas-19.2-fix-abc-fw"]),
        ("odoo-dev", "odoo", []),
        ("odoo", "upgrade", ["master"]),
        ("odoo", "odoofin", ["master", "18.0", "19.0"]),
        ("odoo", "i18n-apps", ["master"]),
        ("odoo", "mobile", ["master"]),
    ]
    for owner, repo, branches in remote_branches:
        remote = remotes_dir / owner / f"{repo}.git"
        git(tmp_path, "init", "-q", "--bare", str(remote))
        git(remote, "config", "uploadpack.allowFilter", "true")
        if branches:
            git(tmp_path, "init", "-q", str(seed / repo))
            (seed / repo / "requirements.txt").write_text("requests\n")
            if repo == "i18n-apps":
                (seed / repo / "i18n_sync").mkdir()
                (seed / repo / "i18n_sync" / "__manifest__.py").write_text("{'name': 'Sync', 'version': '20.0.1.0'}")
            git(seed / repo, "add", ".")
            git(seed / repo, "commit", "-q", "-m", "init")
            for branch in branches:
                git(seed / repo, "push", "-q", str(remote), f"master:{branch}")
    git(remotes_dir / "odoo" / "odoofin.git", "symbolic-ref", "HEAD", "refs/heads/18.0")
    monkeypatch.setattr(mv_config, "GIT_URL", f"file://{remotes_dir}/{{owner}}/{{repo}}.git")
    return remotes_dir


def test_clone_bare_repo_configures_remotes(tmp_path: Path, remotes: Path) -> None:
    repo_dir = tmp_path / "mv" / ".worktree-source" / "odoo"

    result = mv_setup.clone_bare_repo(KNOWN_REPOS["odoo"], repo_dir)

    assert result.outcome == Outcome.UPDATED
    bare = repo_dir / ".bare"
    assert git(bare, "config", "--get-all", "remote.origin.fetch").splitlines() == [
        "+refs/heads/master:refs/remotes/origin/master",
    ]
    # No `dev` branch is fetched without a branch suffix.
    assert not git(bare, "config", "--get-all", "remote.dev.fetch", check=False)
    # Only the default branch is cloned as a local branch.
    assert git(bare, "for-each-ref", "--format=%(refname)", "refs/heads") == "refs/heads/master"
    assert git(bare, "remote", "get-url", "dev") == f"file://{remotes}/odoo-dev/odoo.git"
    assert git(bare, "remote", "get-url", "--push", "origin") == "NO_PUSH_TRY_DEV_REPO"
    assert git(bare, "config", "remote.origin.partialclonefilter") == "blob:none"
    assert (repo_dir / ".git").read_text() == "gitdir: ./.bare\n"
    # Running it again doesn't clone again.
    assert mv_setup.clone_bare_repo(KNOWN_REPOS["odoo"], repo_dir).outcome == Outcome.SKIPPED


@pytest.mark.usefixtures("remotes")
def test_add_worktree_tracks_origin(tmp_path: Path) -> None:
    repo_dir = tmp_path / "mv" / ".worktree-source" / "odoo"
    mv_setup.clone_bare_repo(KNOWN_REPOS["odoo"], repo_dir)
    worktree = tmp_path / "mv" / "19.0" / "odoo"

    result = mv_setup.add_worktree(repo_dir / ".bare", worktree, "19.0")

    assert result == RepoResult("19.0/odoo", Outcome.UPDATED, "added")
    assert git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == "19.0"
    assert git(worktree, "rev-parse", "--abbrev-ref", "@{upstream}") == "origin/19.0"
    assert mv_setup.add_worktree(repo_dir / ".bare", worktree, "19.0").outcome == Outcome.SKIPPED

    # Fetching only updates the branches in use, not the staging, temporary or forward-port branches.
    git(repo_dir / ".bare", "fetch", "-q", "origin")
    fetched = git(repo_dir / ".bare", "for-each-ref", "--format=%(refname:lstrip=3)", "refs/remotes/origin").split()
    assert sorted(set(fetched) - {"HEAD"}) == ["19.0", "master"]

    missing = mv_setup.add_worktree(repo_dir / ".bare", tmp_path / "mv" / "saas-19.2" / "odoo", "saas-19.2")
    assert missing.outcome == Outcome.SKIPPED
    assert "no [b]saas-19.2[/b] branch" in missing.message


def test_link_repos_uses_relative_links(tmp_path: Path) -> None:
    version_dir = tmp_path / "mv" / "19.0"
    version_dir.mkdir(parents=True)
    (tmp_path / "mv" / "upgrade").mkdir()
    # A regular folder is never replaced by a link.
    (version_dir / "upgrade-util").mkdir()

    result = mv_setup.link_repos(version_dir, ["upgrade", "upgrade-util"])

    assert result.outcome == Outcome.WARNING
    assert "[b]upgrade-util[/b] already exist" in result.message
    assert (version_dir / "upgrade").readlink() == Path("../upgrade")
    assert (version_dir / "upgrade").resolve() == (tmp_path / "mv" / "upgrade").resolve()
    assert not (version_dir / "upgrade-util").is_symlink()


def test_write_vscode_config(tmp_path: Path) -> None:
    version_dir = tmp_path / "19.0"
    version_dir.mkdir()
    (tmp_path / "upgrade" / ".git").mkdir(parents=True)
    (version_dir / "upgrade").symlink_to(Path("..") / "upgrade")

    result = mv_setup.write_vscode_config(version_dir, reset=False)

    assert result.outcome == Outcome.UPDATED
    settings = json.loads((version_dir / ".vscode" / "settings.json").read_text())
    assert settings["python.defaultInterpreterPath"] == "${workspaceFolder}/.venv/bin/python"
    assert settings["git.scanRepositories"] == ["upgrade"]
    launch = json.loads((version_dir / ".vscode" / "launch.json").read_text())
    attach = next(c for c in launch["configurations"] if c["request"] == "attach")
    assert {"localRoot": "${workspaceFolder}", "remoteRoot": "/code/19.0"} in attach["pathMappings"]
    assert {"localRoot": "${workspaceFolder}/upgrade", "remoteRoot": "/code/upgrade"} in attach["pathMappings"]
    # Existing configuration is kept, unless resetting.
    assert mv_setup.write_vscode_config(version_dir, reset=False).outcome == Outcome.SKIPPED
    assert mv_setup.write_vscode_config(version_dir, reset=True).outcome == Outcome.UPDATED


@pytest.mark.usefixtures("remotes")
def test_cli_reset_config_keeps_shared_repos_of_earlier_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_slow_steps(monkeypatch)
    root = tmp_path / "mv"
    runner = CliRunner()
    assert runner.invoke(app, ["mv", "setup", "-d", str(root), "-b", "19.0", "-r", "odoo", "-r", "upgrade"]).exit_code == 0

    # Setting up only `odoo` again must not drop the `upgrade` link from the configuration.
    result = runner.invoke(app, ["mv", "setup", "-d", str(root), "-b", "19.0", "-r", "odoo", "--reset-config"])

    assert result.exit_code == 0, result.output
    assert (root / "19.0" / "upgrade").is_symlink()
    settings = json.loads((root / "19.0" / ".vscode" / "settings.json").read_text())
    assert settings["git.scanRepositories"] == ["upgrade"]


def test_parse_requirements(tmp_path: Path) -> None:
    odoo = tmp_path / "odoo.txt"
    odoo.write_text(
        "# comment\n"
        "-r other.txt\n"
        "Babel==2.10.3 ; python_version < '3.11'  # min version = 2.6.0 (Focal with security backports)\n"
        "requests\n"
        "\n",
    )
    extra = tmp_path / "extra.txt"
    extra.write_text("requests\ndebugpy\n")

    assert mv_setup.parse_requirements([odoo, extra]) == [
        "Babel==2.10.3 ; python_version < '3.11'",
        "requests",
        "debugpy",
    ]


def test_get_venv_python(tmp_path: Path) -> None:
    (tmp_path / "pyvenv.cfg").write_text("home = /usr/bin\nimplementation = CPython\nversion_info = 3.12.8\n")
    assert mv_setup.get_venv_python(tmp_path) == "3.12"
    assert mv_setup.get_venv_python(tmp_path / "missing") is None


@pytest.mark.usefixtures("remotes")
def test_cli_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = stub_slow_steps(monkeypatch)
    root = tmp_path / "mv"

    result = CliRunner().invoke(
        app, ["mv", "setup", "-d", str(root), "-b", "19.0,master", "-r", "odoo", "-r", "upgrade"],
    )

    assert result.exit_code == 0, result.output
    assert git(root / "19.0" / "odoo", "rev-parse", "--abbrev-ref", "HEAD") == "19.0"
    assert git(root / "master" / "odoo", "rev-parse", "--abbrev-ref", "HEAD") == "master"
    assert (root / "19.0" / "upgrade").readlink() == Path("../upgrade")
    assert (root / "19.0" / ".vscode" / "settings.json").is_file()
    assert (root / "ruff.toml").is_file()
    assert sorted(calls) == ["19.0", "master"]
    # Running it again changes nothing and still succeeds.
    assert CliRunner().invoke(app, ["mv", "setup", "-d", str(root), "-b", "19.0", "-r", "odoo"]).exit_code == 0


@pytest.mark.usefixtures("remotes")
def test_cli_setup_links_pinned_repos_into_the_version_they_run_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_slow_steps(monkeypatch)
    root = tmp_path / "mv"
    root.mkdir()
    (root / "multiverse.toml").write_text('[repos.i18n-apps]\nkind = "pinned"\n')

    result = CliRunner().invoke(
        app, ["mv", "setup", "-d", str(root), "-b", "19.0,20.0", "-r", "odoo,upgrade,odoofin,i18n-apps,mobile"],
    )

    assert result.exit_code == 0, result.output
    # i18n-apps runs on 20.0, detected from its manifests.
    assert (root / "20.0" / "i18n-apps").readlink() == Path("../i18n-apps")
    assert not (root / "19.0" / "i18n-apps").exists()
    # odoofin runs on its default branch, 18.0, which isn't set up.
    assert git(root / "odoofin", "branch", "--show-current") == "18.0"
    assert "runs on 18.0, which isn't set up" in result.output
    assert not (root / "19.0" / "odoofin").exists()
    # Standalone repositories aren't linked anywhere.
    assert (root / "mobile" / ".git").is_dir()
    assert not (root / "19.0" / "mobile").exists()
    assert not (root / "20.0" / "mobile").exists()
    # Shared repositories are in every version.
    assert (root / "19.0" / "upgrade").is_symlink()
    assert (root / "20.0" / "upgrade").is_symlink()
    # Running it again without `-r` keeps everything that is set up.
    result = CliRunner().invoke(app, ["mv", "setup", "-d", str(root), "-b", "19.0,20.0"])
    assert result.exit_code == 0, result.output
    assert (root / "20.0" / "i18n-apps").is_symlink()


def test_follow_default_branch(tmp_path: Path, remotes: Path) -> None:
    clone_dir = tmp_path / "mv" / "odoofin"
    result = mv_setup.clone_repo(KNOWN_REPOS["odoofin"], clone_dir)
    assert result.message == "cloned on [b]18.0[/b]"
    repo = Git(clone_dir)
    # `origin` fetches its default branch, not `master`.
    assert repo("config", "--get-all", "remote.origin.fetch") == "+refs/heads/18.0:refs/remotes/origin/18.0"
    assert mv_setup.follow_default_branch(repo, "odoofin").outcome == Outcome.SKIPPED

    git(remotes / "odoo" / "odoofin.git", "symbolic-ref", "HEAD", "refs/heads/19.0")
    result = mv_setup.follow_default_branch(repo, "odoofin")

    assert result.outcome == Outcome.UPDATED
    assert repo.current_branch() == "19.0"
    assert repo.upstream() == ("origin", "19.0")


def test_follow_default_branch_leaves_your_work_alone(tmp_path: Path, remotes: Path) -> None:
    clone_dir = tmp_path / "mv" / "odoofin"
    mv_setup.clone_repo(KNOWN_REPOS["odoofin"], clone_dir)
    repo = Git(clone_dir)
    (clone_dir / "requirements.txt").write_text("changed")
    git(remotes / "odoo" / "odoofin.git", "symbolic-ref", "HEAD", "refs/heads/19.0")

    result = mv_setup.follow_default_branch(repo, "odoofin")

    assert result.outcome == Outcome.WARNING
    assert "uncommitted changes" in result.message
    assert repo.current_branch() == "18.0"

