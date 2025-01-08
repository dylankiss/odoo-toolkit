import re
from collections.abc import Callable
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from typer import Typer

EMPTY_LIST = []

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
    print(Panel.fit(header, style="bold"), "")


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
    modules: list[str],
    com_path: Path,
    ent_path: Path,
    extra_addons_paths: list[Path] = EMPTY_LIST,
    filter_fn: Callable[[str], bool] = lambda _m: True,
) -> dict[str, Path]:
    """Determine the valid modules and their directories.

    :param modules: The requested list of modules, or `all`, `community`, or `enterprise`.
    :type modules: list[str]
    :param com_path: The Odoo Community repository.
    :type com_path: :class:`pathlib.Path`
    :param ent_path: The Odoo Enterprise repository.
    :type ent_path: :class:`pathlib.Path`
    :param extra_addons_paths: An optional list of extra directories containing Odoo modules, defaults to `[]`.
    :type extra_addons_paths: list[:class:`pathlib.Path`], optional
    :param filter_fn: A function to filter the modules when using `all`, `community`, or `enterprise`,
        defaults to `lambda _m: True`.
    :type filter_fn: Callable[[str], bool], optional
    :return: A tuple containing the valid modules, and the mapping to their directory.
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
                modules_to_consider = {m for m in all_modules if filter_fn(m)}
            case "community":
                modules_to_consider = {m for m in {"base"} | com_modules if filter_fn(m)}
            case "enterprise":
                modules_to_consider = {m for m in ent_modules if filter_fn(m)}
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
