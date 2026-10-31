import json
import subprocess
import tomllib
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
    # Without a branch suffix, your branches are the ones ending with the start of your Git email.
    assert git(bare, "config", "--get-all", "remote.dev.fetch") == "+refs/heads/*-test:refs/remotes/dev/*-test"
    # Only the default branch is cloned as a local branch.
    assert git(bare, "for-each-ref", "--format=%(refname)", "refs/heads") == "refs/heads/master"
    assert git(bare, "remote", "get-url", "dev") == f"file://{remotes}/odoo-dev/odoo.git"
    assert git(bare, "remote", "get-url", "--push", "origin") == "NO_PUSH_TRY_DEV_REPO"
    assert git(bare, "config", "remote.origin.partialclonefilter") == "blob:none"
    assert (repo_dir / ".git").read_text() == "gitdir: ./.bare\n"
    # Running it again doesn't clone again, and a branch suffix given once is remembered.
    assert mv_setup.clone_bare_repo(KNOWN_REPOS["odoo"], repo_dir, branch_suffix="abcd").outcome == Outcome.UPDATED
    assert mv_setup.clone_bare_repo(KNOWN_REPOS["odoo"], repo_dir).outcome == Outcome.SKIPPED
    assert git(bare, "config", "--get-all", "remote.dev.fetch") == "+refs/heads/*-abcd:refs/remotes/dev/*-abcd"


@pytest.mark.usefixtures("remotes")
def test_clone_bare_repo_with_full_history(tmp_path: Path) -> None:
    repo_dir = tmp_path / "mv" / ".worktree-source" / "odoo"

    result = mv_setup.clone_bare_repo(KNOWN_REPOS["odoo"], repo_dir, full_history=True)

    assert result == RepoResult("odoo", Outcome.UPDATED, "cloned with its full history")
    bare = repo_dir / ".bare"
    assert git(bare, "config", "--get", "remote.origin.partialclonefilter", check=False) == ""
    assert not git(bare, "rev-list", "--objects", "--all", "--missing=print", "--filter-print-omitted").count("?")


@pytest.mark.usefixtures("remotes")
def test_clone_bare_repo_completes_a_partial_clone(tmp_path: Path) -> None:
    repo_dir = tmp_path / "mv" / ".worktree-source" / "odoo"
    mv_setup.clone_bare_repo(KNOWN_REPOS["odoo"], repo_dir)
    bare = repo_dir / ".bare"
    assert git(bare, "rev-list", "--objects", "--all", "--missing=print").count("?")

    result = mv_setup.clone_bare_repo(KNOWN_REPOS["odoo"], repo_dir, full_history=True)

    assert result == RepoResult("odoo", Outcome.UPDATED, "downloaded its full history")
    assert git(bare, "config", "--get", "remote.origin.promisor", check=False) == ""
    assert not git(bare, "rev-list", "--objects", "--all", "--missing=print").count("?")
    # Nothing is left to download, so blame works without GitHub.
    git(bare, "remote", "set-url", "origin", "file:///nonexistent")
    assert git(bare, "blame", "-L1,1", "master", "--", "requirements.txt")
    assert mv_setup.clone_bare_repo(KNOWN_REPOS["odoo"], repo_dir, full_history=True).outcome == Outcome.SKIPPED


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


def test_write_commit_graph(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    git(tmp_path, "init", "-q", str(repo_dir))
    git(repo_dir, "commit", "-q", "--allow-empty", "-m", "first")
    chain = repo_dir / ".git" / "objects" / "info" / "commit-graphs" / "commit-graph-chain"

    assert mv_setup.write_commit_graph(Git(repo_dir), "repo") == RepoResult(
        "repo", Outcome.UPDATED, "commit graph written",
    )
    assert chain.is_file()
    # Without new commits, there's nothing to do.
    assert mv_setup.write_commit_graph(Git(repo_dir), "repo").outcome == Outcome.SKIPPED
    git(repo_dir, "commit", "-q", "--allow-empty", "-m", "second")
    assert mv_setup.write_commit_graph(Git(repo_dir), "repo") == RepoResult(
        "repo", Outcome.UPDATED, "commit graph updated",
    )


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


def test_write_ruff_config(tmp_path: Path) -> None:
    version_dir = tmp_path / "19.0"
    version_dir.mkdir()
    config_file = version_dir / "ruff.toml"

    # Without Odoo's own rules to extend, it follows the ones of master in the multiverse directory.
    assert mv_setup.write_ruff_config(version_dir, reset=False).outcome == Outcome.UPDATED
    assert 'extend = "../ruff-odoo.toml"' in config_file.read_text()
    assert mv_setup.write_ruff_config(version_dir, reset=False).outcome == Outcome.SKIPPED

    # With them, it follows the rules of this version.
    (version_dir / "odoo").mkdir()
    (version_dir / "odoo" / "ruff.toml").write_text("")
    assert mv_setup.write_ruff_config(version_dir, reset=True).outcome == Outcome.UPDATED
    assert 'extend = "odoo/ruff.toml"' in config_file.read_text()


def test_write_root_config_follows_odoo_ruff_rules(tmp_path: Path) -> None:
    odoo_ruff_file = tmp_path / "ruff-odoo.toml"
    bundled = (mv_setup.MULTIVERSE_CONFIG_DIR / "ruff-odoo.toml").read_text()

    mv_setup.write_root_config(tmp_path, reset_config=False)

    assert odoo_ruff_file.read_text() == bundled
    assert 'extend = "ruff-odoo.toml"' in (tmp_path / "ruff.toml").read_text()
    # Existing files are kept, unless resetting.
    odoo_ruff_file.write_text("# mine\n")
    mv_setup.write_root_config(tmp_path, reset_config=False)
    assert odoo_ruff_file.read_text() == "# mine\n"
    mv_setup.write_root_config(tmp_path, reset_config=True)
    assert odoo_ruff_file.read_text() == bundled


def test_write_vscode_config(tmp_path: Path) -> None:
    version_dir = tmp_path / "19.0"
    version_dir.mkdir()
    (tmp_path / "upgrade" / ".git").mkdir(parents=True)
    (version_dir / "upgrade").symlink_to(Path("..") / "upgrade")

    result = mv_setup.write_vscode_config(version_dir, reset=False)

    assert result.outcome == Outcome.UPDATED
    settings = json.loads((version_dir / ".vscode" / "settings.json").read_text())
    # The interpreter is the one VS Code finds in `.venv`, so the dev container can use the image's instead.
    assert "python.defaultInterpreterPath" not in settings
    assert settings["git.scanRepositories"] == ["upgrade"]
    # Vendored libraries are left out of searching and watching, but translations stay in, since people work on them.
    assert settings["search.exclude"]["**/static/lib"] is True
    assert settings["files.watcherExclude"]["**/static/lib/**"] is True
    assert "**/i18n/*.po" not in settings["search.exclude"]
    launch = json.loads((version_dir / ".vscode" / "launch.json").read_text())
    tasks = json.loads((version_dir / ".vscode" / "tasks.json").read_text())
    run_and_debug = launch["configurations"][0]
    # F5 runs Odoo with the debugger, and the paths are the same in the container, so no mappings are needed.
    assert run_and_debug["preLaunchTask"] in {task["label"] for task in tasks["tasks"]}
    assert run_and_debug["postDebugTask"] in {task["label"] for task in tasks["tasks"]}
    assert "pathMappings" not in run_and_debug
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


def test_configure_language_server_gives_it_its_paths(tmp_path: Path) -> None:
    version_dir = tmp_path / "mv" / "19.0"
    (version_dir / "odoo" / "odoo").mkdir(parents=True)
    (version_dir / "odoo" / "odoo" / "release.py").write_text("")
    for repo, modules_dir in (("odoo", "addons"), ("enterprise", "")):
        module_dir = version_dir / repo / modules_dir / "some_module"
        module_dir.mkdir(parents=True)
        (module_dir / "__manifest__.py").write_text("{}")
        (version_dir / repo / ".git").write_text("gitdir: elsewhere\n")
    (version_dir / ".venv" / "bin").mkdir(parents=True)
    (version_dir / ".venv" / "bin" / "python").write_text("")
    (version_dir / "odoo" / "node_modules" / ".bin").mkdir(parents=True)
    (version_dir / "odoo" / "node_modules" / ".bin" / "tsserver").write_text("")

    assert mv_setup.configure_language_server(version_dir).outcome == Outcome.UPDATED

    config = tomllib.loads((version_dir / "odools.toml").read_text())["config"][0]
    # Relative to the workspace folder, so the same file works in a dev container. Enterprise comes first.
    assert config["python_path"] == "${workspaceFolder}/.venv/bin/python"
    assert config["odoo_path"] == "${workspaceFolder}/odoo"
    assert config["addons_paths"] == ["${workspaceFolder}/enterprise", "${workspaceFolder}/odoo/addons"]
    # It only looks for `tsserver` on the `PATH`, so it gets the one of the JavaScript tooling.
    assert config["tsserver_command"] == str(version_dir / "odoo" / "node_modules" / ".bin" / "tsserver")


def test_configure_language_server_leaves_out_what_is_not_there(tmp_path: Path) -> None:
    version_dir = tmp_path / "mv" / "19.0"
    version_dir.mkdir(parents=True)

    mv_setup.configure_language_server(version_dir)

    # An empty list of addons paths would turn the automatic detection off, and a missing `tsserver` the default on.
    assert tomllib.loads((version_dir / "odools.toml").read_text())["config"] == [{}]


def test_write_devcontainer_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mv_setup, "get_git_identity", lambda: {"GIT_AUTHOR_NAME": "Abcd"})
    multiverse_dir = tmp_path / "mv"
    version_dir = multiverse_dir / "19.0"
    for repo, modules_dir in (("odoo", "addons"), ("enterprise", "")):
        module_dir = version_dir / repo / modules_dir / "some_module"
        module_dir.mkdir(parents=True)
        (module_dir / "__manifest__.py").write_text("{}")
        (version_dir / repo / ".git").write_text("gitdir: elsewhere\n")
    (version_dir / ".vscode").mkdir()
    bare_dir = multiverse_dir / ".worktree-source" / "odoo" / ".bare"
    bare_dir.mkdir(parents=True)
    (bare_dir / "config").write_text("[core]\n")

    result = mv_setup.write_devcontainer_config(version_dir, reset=False)

    assert result == RepoResult("devcontainer", Outcome.UPDATED, "wrote [b]devcontainer.json[/b]")
    config_file = version_dir / ".devcontainer" / "devcontainer.json"
    config = json.loads(config_file.read_text())
    assert config["image"] == "dylankiss/odoo-dev:19.0"
    # The multiverse is mounted at the same path, so links and worktrees work, and nothing else of your machine is.
    assert config["workspaceMount"] == f"source={multiverse_dir},target={multiverse_dir},type=bind"
    assert config["workspaceFolder"] == str(version_dir)
    # What runs on your machine later can't be changed from the container, if it exists: there is no `.venv` here.
    read_only = [mount for mount in config["mounts"] if mount.endswith("readonly")]
    assert read_only[:2] == [
        f"source={version_dir / name},target={version_dir / name},type=bind,readonly" for name in (".devcontainer", ".vscode")
    ]
    # The hooks and configuration of the bare repositories, which every version shares, are Git's way to run code later.
    assert read_only[2:4] == [
        f"source={bare_dir / name},target={bare_dir / name},type=bind,readonly" for name in ("config", "hooks")
    ]
    # Claude Code finds the instructions of the container where it reads its own, and they can't be changed from inside.
    instructions_file = version_dir / ".devcontainer" / "CLAUDE.md"
    assert read_only[4:] == [f"source={instructions_file},target=/home/odoo/.claude/CLAUDE.md,type=bind,readonly"]
    # Claude Code keeps its login in a volume of its own, so you log in once and your own login stays out.
    assert "source=otk-claude,target=/home/odoo/.claude,type=volume" in config["mounts"]
    assert config["containerEnv"]["CLAUDE_CONFIG_DIR"] == "/home/odoo/.claude"
    extensions = config["customizations"]["vscode"]["extensions"]
    recommended = json.loads((mv_setup.MULTIVERSE_CONFIG_DIR / ".vscode" / "extensions.json").read_text())
    assert extensions == ["anthropic.claude-code", *recommended["recommendations"]]
    assert config["customizations"]["vscode"]["settings"]["python.defaultInterpreterPath"] == "/venv/main/bin/python"
    assert "--cap-drop=ALL" in config["runArgs"]
    assert "--name=otk-19.0-dev" in config["runArgs"]
    # The services are only started when they don't run yet, which is quick to check.
    command = config["initializeCommand"]
    assert command.index("State.Health.Status") < command.index("otk dev db")
    assert "otk-postgres" in command
    assert "otk-mailpit" in command
    # Odoo's Ruff rules can be newer than the Ruff in the image of a version, so the extension brings its own.
    assert config["customizations"]["vscode"]["settings"]["ruff.importStrategy"] == "useBundled"
    assert "--network=otk" in config["runArgs"]
    assert config["containerEnv"]["GIT_AUTHOR_NAME"] == "Abcd"
    assert config["containerEnv"]["GIT_CONFIG_KEY_0"] == "core.checkStat"
    assert config["remoteEnv"]["SSH_AUTH_SOCK"] is None
    launch = config["customizations"]["vscode"]["settings"]["launch"]["configurations"][0]
    assert launch["program"] == str(version_dir / "odoo" / "odoo-bin")
    assert launch["args"][0] == f"--addons-path={version_dir / 'enterprise'},{version_dir / 'odoo' / 'addons'}"
    assert launch["args"][-2:] == ["-d", "${workspaceFolderBasename}"]
    # Existing configuration is kept, unless resetting.
    config_file.write_text("{}")
    assert mv_setup.write_devcontainer_config(version_dir, reset=False).outcome == Outcome.SKIPPED
    assert config_file.read_text() == "{}"
    assert mv_setup.write_devcontainer_config(version_dir, reset=True).outcome == Outcome.UPDATED


def test_write_devcontainer_config_tells_claude_how_the_container_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mv_setup, "get_git_identity", dict)
    version_dir = tmp_path / "mv" / "19.0"
    for repo in ("odoo", "enterprise"):
        (version_dir / repo).mkdir(parents=True)
        (version_dir / repo / ".git").write_text("gitdir: elsewhere\n")

    mv_setup.write_devcontainer_config(version_dir, reset=False)

    instructions = (version_dir / ".devcontainer" / "CLAUDE.md").read_text()
    assert "Odoo 19.0" in instructions
    assert f"`{version_dir / 'odoo' / 'odoo-bin'}`" in instructions
    assert "`enterprise`, `odoo`" in instructions
    assert "otk-postgres" in instructions
    assert "Ask the user before you fetch an external site" in instructions
    # Every placeholder is filled in.
    assert "$" not in instructions


def test_link_skills_reads_the_skills_in_the_repository(tmp_path: Path) -> None:
    version_dir = tmp_path / "mv" / "20.0"
    (version_dir / "odoo" / "skills" / "odoo-review").mkdir(parents=True)
    (version_dir / "odoo" / "skills" / "odoo-review" / "SKILL.md").write_text("---\nname: odoo-review\n---\n")

    assert mv_setup.link_skills(version_dir).outcome == Outcome.UPDATED

    link = version_dir / ".claude" / "skills"
    # A relative link, which Claude Code follows to the repository, so nothing is copied.
    assert link.readlink() == Path("..") / "odoo" / "skills"
    assert (link / "odoo-review" / "SKILL.md").is_file()
    # A skill that is added later is there at once, and linking again changes nothing.
    (version_dir / "odoo" / "skills" / "odoo-git").mkdir()
    assert (link / "odoo-git").is_dir()
    assert mv_setup.link_skills(version_dir).outcome == Outcome.SKIPPED


def test_link_skills_links_older_versions_to_the_newest_skills(tmp_path: Path) -> None:
    multiverse_dir = tmp_path / "mv"
    for name in ("19.0", "20.0", "master"):
        (multiverse_dir / name / "odoo").mkdir(parents=True)
    for name in ("20.0", "master"):
        (multiverse_dir / name / "odoo" / "skills" / "odoo-git").mkdir(parents=True)
    version_dir = multiverse_dir / "19.0"

    assert mv_setup.link_skills(version_dir).outcome == Outcome.UPDATED

    link = version_dir / ".claude" / "skills"
    # `master` is the newest, and the link is relative to the `.claude` folder.
    assert link.readlink() == Path("..") / ".." / "master" / "odoo" / "skills"
    assert (link / "odoo-git").is_dir()
    assert mv_setup.link_skills(version_dir).outcome == Outcome.SKIPPED
    # When the version gets skills of its own, it follows them.
    (version_dir / "odoo" / "skills").mkdir()
    assert mv_setup.link_skills(version_dir).outcome == Outcome.UPDATED
    assert link.readlink() == Path("..") / "odoo" / "skills"


def test_link_skills_leaves_a_multiverse_without_skills_alone(tmp_path: Path) -> None:
    version_dir = tmp_path / "mv" / "17.0"
    (version_dir / "odoo").mkdir(parents=True)

    result = mv_setup.link_skills(version_dir)

    assert result.outcome == Outcome.SKIPPED
    assert not (version_dir / ".claude").exists()


def test_link_skills_keeps_a_link_you_made_yourself(tmp_path: Path) -> None:
    version_dir = tmp_path / "mv" / "20.0"
    (version_dir / "odoo" / "skills").mkdir(parents=True)
    (version_dir / ".claude").mkdir()
    (version_dir / ".claude" / "skills").symlink_to(tmp_path / "my-skills")

    result = mv_setup.link_skills(version_dir)

    assert result.outcome == Outcome.WARNING
    assert (version_dir / ".claude" / "skills").readlink() == tmp_path / "my-skills"


def test_link_skills_keeps_your_own_skills_folder(tmp_path: Path) -> None:
    version_dir = tmp_path / "mv" / "20.0"
    (version_dir / "odoo" / "skills").mkdir(parents=True)
    (version_dir / ".claude" / "skills").mkdir(parents=True)

    result = mv_setup.link_skills(version_dir)

    assert result.outcome == Outcome.WARNING
    assert not (version_dir / ".claude" / "skills").is_symlink()


def test_write_devcontainer_config_adds_the_extras_of_your_multiverse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get_git_identity() -> dict[str, str]:
        return {}

    monkeypatch.setattr(mv_setup, "get_git_identity", get_git_identity)
    version_dir = tmp_path / "mv" / "19.0"
    version_dir.mkdir(parents=True)
    (tmp_path / "mv" / "multiverse.toml").write_text(
        '[devcontainer]\n'
        'extensions = ["streetsidesoftware.code-spell-checker", "odoo.odoo"]\n'
        '[devcontainer.settings]\n'
        '"cSpell.allowCompoundWords" = true\n'
        '"terminal.integrated.defaultProfile.linux" = "zsh"\n',
    )

    mv_setup.write_devcontainer_config(version_dir, reset=False)

    config = json.loads((version_dir / ".devcontainer" / "devcontainer.json").read_text())
    vscode = config["customizations"]["vscode"]
    # Your extensions come on top of the standard ones, without duplicates, and your settings win.
    assert vscode["extensions"][-1] == "streetsidesoftware.code-spell-checker"
    assert vscode["extensions"].count("odoo.odoo") == 1
    assert vscode["settings"]["cSpell.allowCompoundWords"] is True
    assert vscode["settings"]["terminal.integrated.defaultProfile.linux"] == "zsh"


def test_write_devcontainer_config_refuses_a_multiverse_the_container_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def check_mountable(_multiverse_dir: Path) -> str:
        return "The multiverse can't be here."

    monkeypatch.setattr(mv_setup, "check_mountable", check_mountable)
    version_dir = tmp_path / "mv" / "19.0"
    version_dir.mkdir(parents=True)

    result = mv_setup.write_devcontainer_config(version_dir, reset=False)

    assert result == RepoResult("devcontainer", Outcome.WARNING, "The multiverse can't be here.")
    assert not (version_dir / ".devcontainer").exists()


@pytest.mark.usefixtures("remotes")
def test_cli_setup_devcontainer_is_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_slow_steps(monkeypatch)
    root = tmp_path / "mv"
    runner = CliRunner()

    assert runner.invoke(app, ["mv", "setup", "-d", str(root), "-b", "19.0", "-r", "odoo"]).exit_code == 0
    assert not (root / "19.0" / ".devcontainer").exists()

    result = runner.invoke(app, ["mv", "setup", "-d", str(root), "-b", "19.0", "-r", "odoo", "--devcontainer"])

    assert result.exit_code == 0, result.output
    assert (root / "19.0" / ".devcontainer" / "devcontainer.json").is_file()
    # The settings only you can change are explained.
    assert "dev.containers.copyGitConfig" in result.output

    # A version that has one keeps it without the option, and it's only overwritten when asked to.
    setup = ["mv", "setup", "-d", str(root), "-b", "19.0", "-r", "odoo"]
    config_file = root / "19.0" / ".devcontainer" / "devcontainer.json"
    config_file.write_text("{}")
    assert runner.invoke(app, setup).exit_code == 0
    assert config_file.read_text() == "{}"
    assert runner.invoke(app, [*setup, "--update-config"]).exit_code == 0
    assert "image" in json.loads(config_file.read_text())
    # Unless you opt out.
    config_file.write_text("{}")
    assert runner.invoke(app, [*setup, "--update-config", "--no-devcontainer"]).exit_code == 0
    assert config_file.read_text() == "{}"


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
    # Odoo's pins of a package win over the ones of later files, also written differently.
    extra.write_text("requests==2.0\ndebugpy\nbabel==2.9.1\n")

    assert mv_setup.parse_requirements([odoo, extra]) == [
        "Babel==2.10.3 ; python_version < '3.11'",
        "requests",
        "debugpy",
    ]


def test_compat_requirements_keep_pkg_resources() -> None:
    # Sphinx 4.3.2, which every version's documentation pins, imports `pkg_resources`.
    assert mv_setup.parse_requirements([mv_setup.COMPAT_REQUIREMENTS_FILE]) == ["setuptools<82"]


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
    assert (root / "19.0" / "ruff.toml").is_file()
    assert sorted(calls) == ["19.0", "master"]
    # Every repository gets a commit graph: the bare repository of a versioned one, and the clone of the others.
    graph_chain = Path("objects") / "info" / "commit-graphs" / "commit-graph-chain"
    assert (root / ".worktree-source" / "odoo" / ".bare" / graph_chain).is_file()
    assert (root / "upgrade" / ".git" / graph_chain).is_file()
    # Running it again changes nothing and still succeeds.
    assert CliRunner().invoke(app, ["mv", "setup", "-d", str(root), "-b", "19.0", "-r", "odoo"]).exit_code == 0


@pytest.mark.usefixtures("remotes")
def test_cli_setup_only_adds_worktrees_in_the_configured_versions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_slow_steps(monkeypatch)
    root = tmp_path / "mv"
    root.mkdir()
    (root / "multiverse.toml").write_text('[repos.odoo]\nversions = ["19.0"]\n')

    result = CliRunner().invoke(app, ["mv", "setup", "-d", str(root), "-b", "19.0,master", "-r", "odoo"])

    assert result.exit_code == 0, result.output
    assert (root / "19.0" / "odoo" / ".git").is_file()
    assert not (root / "master" / "odoo").exists()


@pytest.mark.usefixtures("remotes")
def test_cli_setup_places_pinned_repos_in_the_version_they_run_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_slow_steps(monkeypatch)
    root = tmp_path / "mv"
    root.mkdir()
    (root / "multiverse.toml").write_text('[repos.i18n-apps]\nkind = "pinned"\n')

    result = CliRunner().invoke(
        app, ["mv", "setup", "-d", str(root), "-b", "19.0,20.0", "-r", "odoo,upgrade,odoofin,i18n-apps,mobile"],
    )

    assert result.exit_code == 0, result.output
    # i18n-apps runs on 20.0, detected from its manifests. It lives there, and is linked from the multiverse directory.
    assert (root / "20.0" / "i18n-apps" / ".git").is_dir()
    assert (root / "i18n-apps").readlink() == Path("20.0/i18n-apps")
    assert not (root / "19.0" / "i18n-apps").exists()
    # odoofin runs on its default branch, 18.0, which isn't set up, so it stays in the multiverse directory.
    assert git(root / "odoofin", "branch", "--show-current") == "18.0"
    assert "runs on 18.0, which isn't set up" in result.output
    assert (root / "odoofin" / ".git").is_dir()
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
    assert (root / "20.0" / "i18n-apps" / ".git").is_dir()

    # When it runs on another version, it moves there, and the versions in `also_in` link to it.
    (root / "multiverse.toml").write_text('[repos.i18n-apps]\nkind = "pinned"\nruns_on = "19.0"\nalso_in = ["20.0"]\n')
    result = CliRunner().invoke(app, ["mv", "setup", "-d", str(root), "-b", "19.0,20.0"])
    assert result.exit_code == 0, result.output
    assert (root / "19.0" / "i18n-apps" / ".git").is_dir()
    assert (root / "i18n-apps").readlink() == Path("19.0/i18n-apps")
    assert (root / "20.0" / "i18n-apps").readlink() == Path("../i18n-apps")
    assert git(root / "20.0" / "i18n-apps", "branch", "--show-current") == "master"


def test_place_pinned_repo(tmp_path: Path) -> None:
    (tmp_path / "odoofin" / ".git").mkdir(parents=True)
    (tmp_path / "19.0").mkdir()
    # Linked into the version by an older setup.
    (tmp_path / "19.0" / "odoofin").symlink_to(Path("..") / "odoofin")

    assert mv_setup.place_pinned_repo(tmp_path, "odoofin", "19.0").outcome == Outcome.UPDATED
    assert (tmp_path / "19.0" / "odoofin" / ".git").is_dir()
    assert (tmp_path / "odoofin").readlink() == Path("19.0/odoofin")
    assert mv_setup.place_pinned_repo(tmp_path, "odoofin", "19.0").outcome == Outcome.SKIPPED

    # A regular folder in the way is never replaced.
    (tmp_path / "20.0" / "odoofin").mkdir(parents=True)
    result = mv_setup.place_pinned_repo(tmp_path, "odoofin", "20.0")
    assert result.outcome == Outcome.WARNING
    assert (tmp_path / "19.0" / "odoofin" / ".git").is_dir()
    assert (tmp_path / "odoofin").readlink() == Path("19.0/odoofin")


def test_follow_default_branch(tmp_path: Path, remotes: Path) -> None:
    clone_dir = tmp_path / "mv" / "odoofin"
    result = mv_setup.clone_repo(KNOWN_REPOS["odoofin"], clone_dir)
    assert result.message == "cloned on [b]18.0[/b]"
    repo = Git(clone_dir)
    # `origin` fetches its default branch, not `master`. Without a `dev` remote, it also fetches your own branches.
    assert repo("config", "--get-all", "remote.origin.fetch").splitlines() == [
        "+refs/heads/18.0:refs/remotes/origin/18.0",
        "+refs/heads/*-test:refs/remotes/origin/*-test",
    ]
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


def test_existing_versions_are_the_default(tmp_path: Path) -> None:
    for name in ("master", "19.0", "18.0", "not-a-version"):
        (tmp_path / name).mkdir()

    assert mv_setup.get_existing_versions(tmp_path) == ["18.0", "19.0", "master"]
    assert mv_setup.get_existing_versions(tmp_path / "new") == []



def test_setup_js_tooling_installs_typescript_without_saving_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    version_dir = tmp_path / "mv" / "19.0"
    tooling_dir = version_dir / "odoo" / "addons" / "web" / "tooling"
    tooling_dir.mkdir(parents=True)
    for name in mv_setup.JS_TOOLING_FILES:
        (tooling_dir / name).write_text("{}")
    commands: list[tuple[list[str], Path | None]] = []
    monkeypatch.setattr(mv_setup.shutil, "which", lambda _: "/usr/bin/npm")
    monkeypatch.setattr(mv_setup, "_run", lambda cmd, cwd=None: commands.append((cmd, cwd)))

    assert mv_setup.setup_js_tooling(version_dir).outcome == Outcome.UPDATED

    # Odoo's tooling has no TypeScript, which the Odoo language server needs. Not saved in Odoo's `package.json`, and
    # not version 7, which has no `tsserver`.
    assert commands == [
        (["npm", "install", "--silent"], version_dir / "odoo"),
        (["npm", "install", "--silent", "--no-save", "typescript@6"], version_dir / "odoo"),
    ]


def test_install_requirements_tries_pip_for_what_uv_can_not_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("good\nold-archive\nbroken\n")
    installed: list[tuple[str, str]] = []

    def run(cmd: list[str], cwd: Path | None = None) -> None:  # noqa: ARG001
        tool, requirement = cmd[0], cmd[-1]
        at_once = "good" in cmd and requirement != "good"
        # Installing everything at once fails, uv can't read the old archive, and nothing can install the broken one.
        if at_once or requirement == "broken" or (tool == "uv" and requirement == "old-archive"):
            raise subprocess.CalledProcessError(1, cmd)
        installed.append((Path(tool).name, requirement))

    monkeypatch.setattr(mv_setup, "_run", run)

    assert mv_setup.install_requirements(tmp_path / "python", [requirements]) == ["broken"]
    assert installed == [("uv", "good"), ("python", "old-archive")]


@pytest.mark.usefixtures("remotes")
@pytest.mark.parametrize("git_version", [(2, 39), (2, 49)])
def test_complete_clone_works_with_and_without_backfill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_version: tuple[int, int],
) -> None:
    repo_dir = tmp_path / "mv" / ".worktree-source" / "odoo"
    mv_setup.clone_bare_repo(KNOWN_REPOS["odoo"], repo_dir)
    monkeypatch.setattr(mv_setup, "get_git_version", lambda: git_version)
    bare = Git(repo_dir / ".bare")

    progress: list[str] = []

    assert mv_setup.complete_clone(bare, lambda stage, _current, _total: progress.append(stage))

    # Both ways report what they download.
    assert any("objects" in stage or "batch" in stage for stage in progress)
    assert not git(repo_dir / ".bare", "rev-list", "--objects", "--all", "--missing=print").count("?")
    assert not mv_setup.complete_clone(bare)
