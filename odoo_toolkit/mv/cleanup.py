from pathlib import Path
from typing import Annotated

from rich.table import Table
from typer import Exit, Option, Typer, confirm

from odoo_toolkit.common import TransientProgress, print, print_command_title, print_error, print_success
from odoo_toolkit.git_cli import Git, GitError

from .remotes import CleanupPlan, apply_cleanup, configure_fetching, get_branch_suffix, plan_cleanup
from .setup import WORKTREE_SOURCE_DIR

app = Typer()


@app.command()
def cleanup(
    multiverse_dir: Annotated[
        Path | None,
        Option(
            "--multiverse-dir",
            "-d",
            help="The multiverse directory. Defaults to the one containing the current directory.",
        ),
    ] = None,
    branch_suffix: Annotated[
        str | None,
        Option(
            "--branch-suffix",
            help="The suffix of your own branches on the `dev` remote, like `abcd` for `master-fix-xyz-abcd`. Defaults "
            "to the part of your Git email before the `@`.",
        ),
    ] = None,
    yes: Annotated[bool, Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
) -> None:
    """Remove the branches :broom: the Odoo Multiverse repositories don't need, and reclaim their disk space.

    This removes:\n
    - local branches that aren't checked out in any version, and whose commits all exist on GitHub,\n
    - the copies of branches the remotes don't fetch anymore, like other people's `dev` branches.\n
    \n
    Nothing that only exists on your machine is removed: branches with local commits are kept, and everything removed
    can be fetched again from GitHub. Afterwards, Git's garbage collection reclaims the disk space, which can take a
    few minutes for large repositories.
    """
    print_command_title(":broom: Clean Up Multiverse Repositories")

    root = _find_multiverse_dir(multiverse_dir or Path.cwd())
    if not root:
        print_error("Run this command inside an Odoo Multiverse, or pass its directory with [b]-d[/b].\n")
        raise Exit(1)
    branch_suffix = branch_suffix or get_branch_suffix()

    repos = {
        bare_dir.parent.name: Git(bare_dir)
        for bare_dir in sorted((root / WORKTREE_SOURCE_DIR).glob("*/.bare"))
    }
    plans: dict[str, CleanupPlan] = {}
    with TransientProgress() as progress:
        task = progress.add_task("Finding branches to remove", total=len(repos))
        for name, repo in repos.items():
            try:
                configure_fetching(repo, branch_suffix=branch_suffix)
                plans[name] = plan_cleanup(repo)
            except GitError as e:
                print_error(f"Could not inspect [b]{name}[/b]: {e.message}")
            progress.advance(task)

    plans = {name: plan for name, plan in plans.items() if plan}
    if not plans:
        print_success("Nothing to clean up.\n")
        return

    table = Table(box=None, pad_edge=False)
    table.add_column("Repository", style="bold")
    table.add_column("Local branches", justify="right")
    table.add_column("Remote branches", justify="right")
    table.add_column("Size", justify="right")
    for name, plan in plans.items():
        table.add_row(name, str(len(plan.local_branches)), str(len(plan.remote_refs)), _format_size(repos[name].path))
    print(table, "")

    if not yes and not confirm("Remove these branches and reclaim the disk space?"):
        raise Exit

    for name, plan in plans.items():
        repo = repos[name]
        size_before = _get_size(repo.path)
        with TransientProgress() as progress:
            progress.add_task(f"Cleaning up [b]{name}[/b]", total=None)
            apply_cleanup(repo, plan, branch_suffix=branch_suffix)
            repo("gc", "--prune=now", "--quiet")
        freed = size_before - _get_size(repo.path)
        print_success(
            f"[b]{name}[/b]: removed {len(plan.local_branches) + len(plan.remote_refs)} branches, "
            f"freed {_format_bytes(max(freed, 0))}",
        )
    print()


def _find_multiverse_dir(start: Path) -> Path | None:
    """Find the multiverse directory, recognized by its `.worktree-source` directory, containing the given path."""
    start = start.expanduser().resolve()
    return next((d for d in (start, *start.parents) if (d / WORKTREE_SOURCE_DIR).is_dir()), None)


def _get_size(path: Path) -> int:
    """Get the total size in bytes of the files in a directory."""
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file() and not file.is_symlink())


def _format_size(path: Path) -> str:
    return _format_bytes(_get_size(path))


def _format_bytes(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:  # noqa: PLR2004
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
