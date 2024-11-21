from rich.console import Console
from rich.progress import BarColumn, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from typer import Typer

PROGRESS_COLUMNS = [
    SpinnerColumn(),
    TextColumn("[progress.description]{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    TimeElapsedColumn(),
]

app = Typer(no_args_is_help=True)
logger = Console(stderr=True, highlight=False)
log = logger.print
