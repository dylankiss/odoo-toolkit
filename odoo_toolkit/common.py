from rich.console import Console
from typer import Typer

app = Typer(no_args_is_help=True)
logger = Console(stderr=True, highlight=False)
log = logger.print
