from pathlib import Path
from typing import Annotated

from rich.table import Table
from typer import Exit, Option, Typer, confirm

from odoo_toolkit.common import TransientProgress, print, print_command_title, print_error, print_success
from odoo_toolkit.git_cli import Git, GitError

from .config import WORKTREE_SOURCE_DIR, ConfigError, find_multiverse_dir, get_current_dir, load_config
from .remotes import CleanupPlan, apply_cleanup, configure_fetching, plan_cleanup

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
            "to the one given to `otk mv setup`, or else the part of your Git email before the `@`.",
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

    root = find_multiverse_dir(multiverse_dir or get_current_dir())
    if not root:
        print_error("Run this command inside an Odoo Multiverse, or pass its directory with [b]-d[/b].\n")
        raise Exit(1)

    try:
        known_repos = load_config(root)
    except ConfigError as e:
        print_error(f"{e}\n")
        raise Exit(1) from e
    # The bare repositories, and the clones of the other known repositories. Your own repositories in the
    # multiverse directory are left alone.
    repos = {
        bare_dir.parent.name: Git(bare_dir) for bare_dir in sorted((root / WORKTREE_SOURCE_DIR).glob("*/.bare"))
    } | {name: Git(root / name) for name in sorted(known_repos) if (root / name / ".git").is_dir()}
    plans, failed = _plan_cleanups(repos, branch_suffix)
    if not plans:
        if failed:
            raise Exit(1)
        print_success("Nothing to clean up.\n")
        return

    table = Table(box=None, pad_edge=False)
    table.add_column("Repository", style="bold")
    table.add_column("Local branches", justify="right")
    table.add_column("Remote branches", justify="right")
    table.add_column("Size", justify="right")
    for name, plan in plans.items():
        table.add_row(
            name,
            str(len(plan.local_branches)),
            str(len(plan.remote_refs)),
            _format_bytes(_get_size(repos[name])),
        )
    print(table, "")

    if not yes and not confirm("Remove these branches and reclaim the disk space?"):
        raise Exit(1)

    for name, plan in plans.items():
        failed = not _apply_cleanup(name, repos[name], plan, branch_suffix) or failed
    print()
    if failed:
        raise Exit(1)


def _plan_cleanups(repos: dict[str, Git], branch_suffix: str | None) -> tuple[dict[str, CleanupPlan], bool]:
    """Find what to clean up in every repository.

    :return: The plans of the repositories with something to clean up, and whether inspecting any repository failed.
    """
    plans: dict[str, CleanupPlan] = {}
    failed = False
    with TransientProgress() as progress:
        task = progress.add_task("Finding branches to remove", total=len(repos))
        for name, repo in repos.items():
            try:
                configure_fetching(repo, branch_suffix=branch_suffix)
                if plan := plan_cleanup(repo):
                    plans[name] = plan
            except GitError as e:
                print_error(f"Could not inspect [b]{name}[/b]: {e.message}")
                failed = True
            progress.advance(task)
    return plans, failed


def _apply_cleanup(name: str, repo: Git, plan: CleanupPlan, branch_suffix: str | None) -> bool:
    """Clean up a repository, and reclaim the disk space.

    :return: Whether it succeeded.
    """
    size_before = _get_size(repo)
    try:
        with TransientProgress() as progress:
            progress.add_task(f"Cleaning up [b]{name}[/b]", total=None)
            apply_cleanup(repo, plan, branch_suffix=branch_suffix)
            repo("gc", "--prune=now", "--quiet")
    except GitError as e:
        print_error(f"Cleaning up [b]{name}[/b] failed: {e.message}")
        return False
    freed = size_before - _get_size(repo)
    print_success(
        f"[b]{name}[/b]: removed {len(plan.local_branches) + len(plan.remote_refs)} branches, "
        f"freed {_format_bytes(max(freed, 0))}",
    )
    return True


def _get_size(repo: Git) -> int:
    """Get the size in bytes of a repository's Git objects, which is what garbage collection reclaims."""
    stats = dict(line.split(": ", 1) for line in repo("count-objects", "--verbose").splitlines())
    # Git reports the sizes in KiB.
    return (int(stats.get("size", "0")) + int(stats.get("size-pack", "0"))) * 1024


def _format_bytes(size: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024:  # noqa: PLR2004
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"
