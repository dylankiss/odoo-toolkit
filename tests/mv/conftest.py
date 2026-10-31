import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from odoo_toolkit.git_cli import Git
from odoo_toolkit.main import app
from odoo_toolkit.mv.remotes import configure_fetching


def git(cwd: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True).stdout.strip()


def make_version_dir(multiverse_dir: Path, version: str = "19.0") -> Path:
    """Lay out a version directory with Community and Enterprise worktrees that each have a module."""
    version_dir = multiverse_dir / version
    for repo, modules_dir in (("odoo", "addons"), ("enterprise", "")):
        module_dir = version_dir / repo / modules_dir / "some_module"
        module_dir.mkdir(parents=True)
        (module_dir / "__manifest__.py").write_text("{}")
        (version_dir / repo / ".git").write_text("gitdir: elsewhere\n")
    return version_dir


def run_setup(multiverse_dir: Path, *args: str) -> Result:
    """Run `otk mv setup` for a multiverse."""
    return CliRunner().invoke(app, ["mv", "setup", "-d", str(multiverse_dir), *args])


def commit(work_dir: Path, filename: str, content: str = "content") -> str:
    (work_dir / filename).write_text(content)
    git(work_dir, "add", filename)
    git(work_dir, "commit", "-q", "-m", f"add {filename}")
    return git(work_dir, "rev-parse", "HEAD")


@pytest.fixture
def remotes(tmp_path: Path) -> dict[str, Path]:
    """Create an `origin` remote with `master` and `19.0`, and a `dev` remote with feature branches."""
    # Laid out like GitHub, by owner, so remotes can be matched by owner.
    origin = tmp_path / "remotes" / "odoo" / "odoo.git"
    dev = tmp_path / "remotes" / "odoo-dev" / "odoo.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    git(tmp_path, "init", "-q", "--bare", str(dev))

    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(origin), str(seed))
    commit(seed, "base.txt")
    git(seed, "push", "-q", "origin", "master")
    git(seed, "push", "-q", "origin", "master:19.0")
    git(seed, "push", "-q", str(dev), "master:19.0-fix-abc", "master:fix/master", "master:19.0-mine-test")
    return {"origin": origin, "dev": dev, "seed": seed}


@pytest.fixture
def multiverse(tmp_path: Path, remotes: dict[str, Path]) -> Path:
    """Create a multiverse like `otk mv setup` does: a bare repository with worktrees for `master` and `19.0`.

    The `dev` remote only fetches the branches ending with `-test`, like the ones of a user with email `test@...`.
    """
    root = tmp_path / "odoo-mv"
    bare = root / ".worktree-source" / "odoo" / ".bare"
    git(tmp_path, "clone", "-q", "--bare", str(remotes["origin"]), str(bare))
    git(bare, "remote", "add", "dev", str(remotes["dev"]))
    # Start from the configuration of earlier setups, fetching everything, which is narrowed down below.
    git(bare, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    git(bare, "fetch", "-q", "origin")
    for version in ("master", "19.0"):
        (root / version).mkdir(parents=True)
        git(bare, "worktree", "add", "-q", str(root / version / "odoo"), version)
        git(root / version / "odoo", "branch", "--set-upstream-to", f"origin/{version}", version)
    configure_fetching(Git(bare), branch_suffix="test")
    git(bare, "fetch", "-q", "--all")
    return root
