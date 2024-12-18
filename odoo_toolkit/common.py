from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from typer import Typer

PROGRESS_COLUMNS = [
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    TimeElapsedColumn(),
]

# The main app to register all the commands on
app = Typer(no_args_is_help=True, rich_markup_mode="markdown")
# The console object to print all messages on stderr by default
console = Console(stderr=True, highlight=False)
# Override the native print method to use the custom console
print = console.print  # noqa: A001


class TransientProgress(Progress):
    """Renders auto-updating transient progress bars using opinionated styling."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003, ARG002, D107
        super().__init__(*PROGRESS_COLUMNS, console=console, transient=True)


class StickyProgress(Progress):
    """Renders auto-updating sticky progress bars using opinionated styling."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003, ARG002, D107
        super().__init__(*PROGRESS_COLUMNS, console=console)


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
        print(
            "",
            Panel(
                stacktrace,
                title="Logs",
                title_align="left",
                style="red",
                border_style="bold red",
            ),
        )


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
    return Panel(
        error_logs,
        title=title,
        title_align="left",
        style="red",
        border_style="bold red",
    )
