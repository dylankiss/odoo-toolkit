import importlib.util
import re
from collections.abc import Callable, Collection, Iterable
from contextlib import suppress
from enum import Enum, auto
from fnmatch import fnmatch
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, TypeVar

from rich.console import Console
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from typer import Typer, get_app_dir

APP_DIR = Path(get_app_dir("odoo-toolkit"))
EMPTY_LIST: list[Any] = []


def _get_version() -> str:
    try:
        return package_version("odoo-toolkit")
    except PackageNotFoundError:
        return "unknown"


VERSION = _get_version()
# Identifies the toolkit in the HTTP requests it makes, so the servers' admins know which version sent them and where
# to report problems.
USER_AGENT = f"odoo-toolkit/{VERSION} (+https://github.com/dylankiss/odoo-toolkit)"
HTTP_HEADERS = {"User-Agent": USER_AGENT}
T = TypeVar("T")


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


def is_l10n_module(module: str) -> bool:
    """Determine if the given module is a localization related module."""
    return "l10n_" in module and module != "l10n_multilang"


def get_valid_modules_to_path_mapping(
    modules: Collection[str],
    com_path: Path,
    ent_path: Path,
    extra_addons_paths: Iterable[Path] = EMPTY_LIST,
    include_path: Callable[[Path], bool] = lambda _: True,
) -> dict[str, Path]:
    """Determine the valid modules and their directories.

    :param modules: The requested modules, or `all`, `community`, `enterprise`, `community-l10n`, or `enterprise-l10n`.
    :param com_path: The Odoo Community repository.
    :param ent_path: The Odoo Enterprise repository.
    :param extra_addons_paths: Optional extra directories containing Odoo modules, defaults to `[]`.
    :param include_path: A function to include modules when using `all`, `community`, or `enterprise`,
        defaults to always `True`.
    :return: A mapping from all valid modules to their directories.
    """
    base_module_path = com_path.expanduser().resolve() / "odoo" / "addons"
    com_modules_path = com_path.expanduser().resolve() / "addons"
    ent_modules_path = ent_path.expanduser().resolve()
    extra_modules_paths = [p.expanduser().resolve() for p in extra_addons_paths]

    com_modules = {f.parent.name: com_modules_path / f.parent.name for f in com_modules_path.glob("*/__manifest__.py")}
    ent_modules = {f.parent.name: ent_modules_path / f.parent.name for f in ent_modules_path.glob("*/__manifest__.py")}
    extra_modules = {f.parent.name: p / f.parent.name for p in extra_modules_paths for f in p.glob("*/__manifest__.py")}

    all_modules = {"base": base_module_path / "base"} | com_modules | ent_modules | extra_modules

    # Determine all modules to consider.
    modules_to_consider: dict[str, Path] = {}
    if "all" in modules:
        modules_to_consider.update((m, p) for m, p in all_modules.items() if include_path(p))
    if "community" in modules:
        modules_to_consider.update((m, p) for m, p in com_modules.items() if include_path(p))
    if "enterprise" in modules:
        modules_to_consider.update((m, p) for m, p in ent_modules.items() if include_path(p))
    if "community-l10n" in modules:
        modules_to_consider.update(
            (m, p) for m, p in com_modules.items() if is_l10n_module(m) and include_path(p)
        )
    if "enterprise-l10n" in modules:
        modules_to_consider.update(
            (m, p) for m, p in ent_modules.items() if is_l10n_module(m) and include_path(p)
        )
    modules_to_consider.update(
        (m, p) for m, p in all_modules.items()
        if any(fnmatch(m, mp) for mp in modules) and include_path(p)
    )

    return modules_to_consider


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
            major, minor, *_ = getattr(module, attribute_name)
            if isinstance(major, str):
                match = re.search(r"\d+", major)
                if not match:
                    return None
                major = int(match.group())
            return float(major + (minor / 10))

    return None


def normalize_list_option(option_list: Collection[str]) -> list[str]:
    """Normalize input by splitting comma-separated strings into a list."""
    if len(option_list) > 0 and any("," in options for options in option_list):
        return [option.strip() for options in option_list for option in options.split(",")]
    return list(option_list)


def filter_by_globs(items: Iterable[str], globs: Collection[str], ignore_globs: Collection[str] = ()) -> list[str]:
    """Filter a list of items by a collection of glob patterns, with optional ignore patterns.

    :param items: The items to filter.
    :param globs: The glob patterns to filter by.
    :param ignore_globs: The glob patterns to ignore.
    :return: The filtered list of items.
    """
    if not globs and not ignore_globs:
        return list(items)
    if not globs:
        return [item for item in items if not any(fnmatch(item, pattern) for pattern in set(ignore_globs))]
    return [
        item
        for item in items
        if any(fnmatch(item, pattern) for pattern in set(globs))
        and not any(fnmatch(item, pattern) for pattern in set(ignore_globs))
    ]
