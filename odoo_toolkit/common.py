import importlib.util
import re
import time
from collections.abc import Callable, Collection, Iterable
from concurrent.futures import Future
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum, auto
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, TypeVar

from rich.console import Console
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TaskProgressColumn, TextColumn, TimeElapsedColumn
from typer import Typer, get_app_dir

APP_DIR = Path(get_app_dir("odoo-toolkit"))
EMPTY_LIST: list[Any] = []
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

    SUCCESS = auto()
    PARTIAL = auto()
    FAILURE = auto()


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
        :param key: The key of the dictionary item to update.
        :param description: The description value to update, defaults to `NO_UPDATE`
        :param completed: The completed value to update, defaults to `NO_UPDATE`
        :param total: The total value to update, defaults to `NO_UPDATE`
        :param advance: The number to advance the completed value, defaults to `NO_UPDATE`
        """
        update = progress_updates[key]
        if not isinstance(description, NoUpdate):
            update.description = description
        if not isinstance(completed, NoUpdate):
            update.completed = completed
        elif not isinstance(advance, NoUpdate):
            update.completed += advance
        if not isinstance(total, NoUpdate):
            update.total = total
        if not isinstance(status, NoUpdate):
            update.status = status
        if not isinstance(message, NoUpdate):
            update.message = message
        if not isinstance(stacktrace, NoUpdate):
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
    """
    print(Panel.fit(title, style="bold magenta", border_style="bold magenta"), "")


def print_header(header: str) -> None:
    """Print a styled header to the console using a fitted box.

    :param header: The header text to render
    """
    print(Panel.fit(header, style="bold cyan", border_style="bold cyan"), "")


def print_subheader(header: str) -> None:
    """Print a styled header to the console using a fitted box.

    :param header: The header text to render
    """
    print(Panel.fit(header), "")


def print_error(error_msg: str, stacktrace: str | None = None) -> None:
    """Print a styled error message with optional stacktrace.

    :param error_msg: The error message to render
    :param stacktrace: The stacktrace to render, defaults to None
    """
    print(f":exclamation_mark: {error_msg}", style="red")
    if stacktrace:
        print("", Panel(stacktrace, title="Logs", title_align="left", style="red", border_style="bold red"))


def print_warning(warning_msg: str) -> None:
    """Print a styled warning message.

    :param warning_msg: The warning to render
    """
    print(f":warning: {warning_msg}", style="yellow")


def print_success(success_msg: str) -> None:
    """Print a styled success message.

    :param success_msg: The success message to render
    """
    print(f":white_check_mark: {success_msg}", style="green")


def print_indent(content: str, indentation: int = 1) -> None:
    """Print indented content.

    :param content: The content to render with indentation
    :param indentation: The number of characters to indent
    """
    print(Padding(content, (0, 0, 0, indentation)))


def print_panel(content: str, title: str | None = None) -> None:
    """Print a fitted panel with some content and an optional title.

    :param content: The content to render in the panel
    :param title: The title to render on the panel, defaults to None
    """
    print(Panel.fit(content, title=title, title_align="left"))


def get_error_log_panel(error_logs: str, title: str = "Error") -> Panel:
    """Return a :class:`rich.panel.Panel` containing the provided error log and title.

    :param error_logs: The error logs to render in the Panel
    :param title: The title to use on the Panel, defaults to "Error"
    :return: A Panel to be used in any rich objects for printing
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
    :param com_path: The Odoo Community repository.
    :param ent_path: The Odoo Enterprise repository.
    :param extra_addons_paths: Optional extra directories containing Odoo modules, defaults to `[]`.
    :param filter_fn: A function to filter the modules when using `all`, `community`, or `enterprise`,
        defaults to `None`.
    :return: A mapping from all valid modules to their directories.
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
        modules_text = next(iter(modules))
        match modules_text:
            case "all":
                modules_to_consider = {m for m in all_modules if not filter_fn or filter_fn(m)}
            case "community":
                modules_to_consider = {m for m in com_modules if not filter_fn or filter_fn(m)}
            case "enterprise":
                modules_to_consider = {m for m in {"base"} | ent_modules if not filter_fn or filter_fn(m)}
            case _:
                modules = modules_text.split(",")
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
    futures: dict[Future[None], Any],
) -> None:
    """Update tasks in a :class:`rich.progress.Progress` instance by methods running in multiple threads or processes.

    :param progress: The progress instance containing the tasks.
    :param progress_updates: A shared mapping to progress update information.
    :param futures: The remote threads/processes information.
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


def get_odoo_version(odoo_repo: Path) -> float | None:
    """Get the Odoo version as a float, given the repo path.

    :param odoo_repo: The path to the Odoo community repository.
    :return: The version as a float or `None` if it could not be found.
    """
    file_path = odoo_repo / "odoo" / "release.py"
    module_name = odoo_repo.stem
    attribute_name = "version_info"

    with suppress(Exception):
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None:
            return None

        module = importlib.util.module_from_spec(spec)
        if spec.loader is not None:
            spec.loader.exec_module(module)

        if hasattr(module, attribute_name):
            raw_version = getattr(module, attribute_name)
            return float(raw_version[0] + (raw_version[1] / 10))

    return None
