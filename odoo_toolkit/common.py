import re
import time
from collections.abc import Callable, Collection, Iterable
from concurrent.futures import Future
from dataclasses import dataclass
from enum import Enum
from fnmatch import fnmatch
from multiprocessing.managers import Namespace
from pathlib import Path
from typing import Any, TypeVar

from rich.console import Console
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TaskProgressColumn, TextColumn, TimeElapsedColumn
from typer import Typer, get_app_dir

APP_DIR = Path(get_app_dir("odoo-toolkit"))
EMPTY_LIST = []
T = TypeVar("T")


class NoUpdate:
    """Indicates data does not need to be updated."""
NO_UPDATE = NoUpdate()

# The main app to register all the commands on
app = Typer(no_args_is_help=True, rich_markup_mode="markdown")
# The console object to print all messages on stderr by default
console = Console(stderr=True, highlight=False)
# Override the native print method to use the custom console
print = console.print  # noqa: A001


class Status(Enum):
    """The status of a specific function call."""

    SUCCESS = 1
    FAILURE = 2
    PARTIAL = 3


@dataclass
class ProgressUpdate:
    """Update information for :class:`rich.progress.Progress` instances."""

    task_id: TaskID
    description: str
    completed: float = 0
    total: float | None = None
    status: Status | None = None
    message: str | None = None
    stacktrace: str | None = None

    @classmethod
    def update_in_dict(
        cls,
        progress_updates: dict[T, "ProgressUpdate"],
        key: T,
        *,
        description: str | NoUpdate = NO_UPDATE,
        completed: float | NoUpdate = NO_UPDATE,
        total: float | None | NoUpdate = NO_UPDATE,
        advance: float | NoUpdate = NO_UPDATE,
        status: Status | None | NoUpdate = NO_UPDATE,
        message: str | None | NoUpdate = NO_UPDATE,
        stacktrace: str | None | NoUpdate = NO_UPDATE,
    ) -> None:
        """Update :class:`odoo_toolkit.common.ProgressUpdate` information in the given dictionary.

        :param progress_updates: The dictionary to update the progress information in.
        :type progress_updates: dict[T, :class:`odoo_toolkit.common.ProgressUpdate`]
        :param key: The key of the dictionary item to update.
        :type key: T
        :param description: The description value to update, defaults to `NO_UPDATE`
        :type description: str, optional
        :param completed: The completed value to update, defaults to `NO_UPDATE`
        :type completed: float, optional
        :param total: The total value to update, defaults to `NO_UPDATE`
        :type total: float | None, optional
        :param advance: The number to advance the completed value, defaults to `NO_UPDATE`
        :type advance: float, optional
        """
        update = progress_updates[key]
        if description is not NO_UPDATE:
            update.description = description
        if completed is not NO_UPDATE:
            update.completed = completed
        elif advance is not NO_UPDATE:
            update.completed += advance
        if total is not NO_UPDATE:
            update.total = total
        if status is not NO_UPDATE:
            update.status = status
        if message is not NO_UPDATE:
            update.message = message
        if stacktrace is not NO_UPDATE:
            update.stacktrace = stacktrace
        progress_updates[key] = update


class StickyProgress(Progress):
    """Render auto-updating sticky progress bars using opinionated styling."""

    def __init__(self) -> None:
        """Initialize the :class:`rich.progress.Progress` instance with a specific styling."""
        super().__init__(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
        )


class TransientProgress(Progress):
    """Render auto-updating transient progress bars using opinionated styling."""

    def __init__(self) -> None:
        """Initialize the :class:`rich.progress.Progress` instance with a specific styling."""
        super().__init__(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        )


def print_command_title(title: str) -> None:
    """Print a styled command title to the console using a fitted box and bold magenta text and box borders.

    :param title: The title to render
    :type title: str
    """
    print(Panel.fit(title, style="bold magenta", border_style="bold magenta"), "")


def print_header(header: str) -> None:
    """Print a styled header to the console using a fitted box.

    :param header: The header text to render
    :type header: str
    """
    print(Panel.fit(header, style="bold cyan", border_style="bold cyan"), "")


def print_subheader(header: str) -> None:
    """Print a styled header to the console using a fitted box.

    :param header: The header text to render
    :type header: str
    """
    print(Panel.fit(header), "")


def print_error(error_msg: str, stacktrace: str | None = None) -> None:
    """Print a styled error message with optional stacktrace.

    :param error_msg: The error message to render
    :type error_msg: str
    :param stacktrace: The stacktrace to render, defaults to None
    :type stacktrace: str | None, optional
    """
    print(f":exclamation_mark: {error_msg}", style="red")
    if stacktrace:
        print("", Panel(stacktrace, title="Logs", title_align="left", style="red", border_style="bold red"))


def print_warning(warning_msg: str) -> None:
    """Print a styled warning message.

    :param warning_msg: The warning to render
    :type warning_msg: str
    """
    print(f":warning: {warning_msg}", style="yellow")


def print_success(success_msg: str) -> None:
    """Print a styled success message.

    :param success_msg: The success message to render
    :type success_msg: str
    """
    print(f":white_check_mark: {success_msg}", style="green")


def print_indent(content: str, indentation: int = 1) -> None:
    """Print indented content.

    :param content: The content to render with indentation
    :type content: str
    :param indentation: The number of characters to indent
    :type indentation: int, optional
    """
    print(Padding(content, (0, 0, 0, indentation)))


def print_panel(content: str, title: str | None = None) -> None:
    """Print a fitted panel with some content and an optional title.

    :param content: The content to render in the panel
    :type content: str
    :param title: The title to render on the panel, defaults to None
    :type title: str | None, optional
    """
    print(Panel.fit(content, title=title, title_align="left"))


def get_error_log_panel(error_logs: str, title: str = "Error") -> Panel:
    """Return a :class:`rich.panel.Panel` containing the provided error log and title.

    :param error_logs: The error logs to render in the Panel
    :type error_logs: str
    :param title: The title to use on the Panel, defaults to "Error"
    :type title: str, optional
    :return: A Panel to be used in any rich objects for printing
    :rtype: :class:`rich.panel.Panel`
    """
    return Panel(error_logs, title=title, title_align="left", style="red", border_style="bold red")


def get_valid_modules_to_path_mapping(
    modules: Collection[str],
    com_path: Path,
    ent_path: Path,
    extra_addons_paths: Iterable[Path] = EMPTY_LIST,
    filter_fn: Callable[[str], bool] | None = None,
) -> dict[str, Path]:
    """Determine the valid modules and their directories.

    :param modules: The requested modules, or `all`, `community`, or `enterprise`.
    :type modules: Collection[str]
    :param com_path: The Odoo Community repository.
    :type com_path: :class:`pathlib.Path`
    :param ent_path: The Odoo Enterprise repository.
    :type ent_path: :class:`pathlib.Path`
    :param extra_addons_paths: Optional extra directories containing Odoo modules, defaults to `[]`.
    :type extra_addons_paths: Iterable[:class:`pathlib.Path`], optional
    :param filter_fn: A function to filter the modules when using `all`, `community`, or `enterprise`,
        defaults to `None`.
    :type filter_fn: Callable[[str], bool] | None, optional
    :return: A mapping from all valid modules to their directories.
    :rtype: dict[str, :class:`pathlib.Path`]
    """
    base_module_path = com_path.expanduser().resolve() / "odoo" / "addons"
    com_modules_path = com_path.expanduser().resolve() / "addons"
    ent_modules_path = ent_path.expanduser().resolve()
    extra_modules_paths = [p.expanduser().resolve() for p in extra_addons_paths]

    com_modules = {f.parent.name for f in com_modules_path.glob("*/__manifest__.py")}
    ent_modules = {f.parent.name for f in ent_modules_path.glob("*/__manifest__.py")}

    modules_path_tuples = [
        ({"base"}, base_module_path),
        (com_modules, com_modules_path),
        (ent_modules, ent_modules_path),
    ]
    modules_path_tuples.extend(({f.parent.name for f in p.glob("*/__manifest__.py")}, p) for p in extra_modules_paths)

    all_modules = {"base"} | com_modules | ent_modules
    all_modules.update(m for t in modules_path_tuples[3:] for m in t[0])

    # Determine all modules to consider.
    if len(modules) == 1:
        match modules[0]:
            case "all":
                modules_to_consider = {m for m in all_modules if not filter_fn or filter_fn(m)}
            case "community":
                modules_to_consider = {m for m in {"base"} | com_modules if not filter_fn or filter_fn(m)}
            case "enterprise":
                modules_to_consider = {m for m in ent_modules if not filter_fn or filter_fn(m)}
            case _:
                modules = modules[0].split(",")
                modules_to_consider = {m for m in all_modules if any(fnmatch(m, p) for p in modules)}
    else:
        modules = {re.sub(r",", "", m) for m in modules}
        modules_to_consider = {m for m in all_modules if any(fnmatch(m, p) for p in modules)}

    if not modules_to_consider:
        return {}

    # Map each module to its directory.
    return {
        module: path / module
        for modules, path in modules_path_tuples
        for module in modules & modules_to_consider
    }


def update_remote_progress(
    progress: Progress,
    progress_updates: dict[Any, ProgressUpdate],
    futures: dict[Future, Any],
) -> None:
    """Update tasks in a :class:`rich.progress.Progress` instance by methods running in multiple threads or processes.

    :param progress: The progress instance containing the tasks.
    :type progress: :class:`rich.progress.Progress`
    :param progress_updates: A shared mapping to progress update information.
    :type progress_updates: dict[Any, :class:`odoo_toolkit.common.ProgressUpdate`]
    :param futures: The remote threads/processes information.
    :type futures: dict[:class:`concurrent.futures.Future`, Any]
    """
    def update_progress_internal() -> None:
        for key, value in progress_updates.items():
            progress.update(
                value.task_id,
                description=value.description,
                completed=value.completed,
                total=value.total,
            )

            if value.status and value.message:
                # Print error, warning, or success messages.
                if value.status == Status.FAILURE:
                    print_error(value.message, value.stacktrace)
                elif value.status == Status.PARTIAL:
                    print_warning(value.message)
                elif value.status == Status.SUCCESS:
                    print_success(value.message)
                ProgressUpdate.update_in_dict(progress_updates, key, status=None, message=None, stacktrace=None)


    while any(not task.done() for task in futures):
        update_progress_internal()
        # Reduce CPU usage by only checking every 0.5 seconds.
        time.sleep(0.5)

    update_progress_internal()
