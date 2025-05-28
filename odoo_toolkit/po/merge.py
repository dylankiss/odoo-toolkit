from pathlib import Path
from typing import Annotated

from polib import POFile, pofile
from typer import Argument, Exit, Option, Typer

from odoo_toolkit.common import TransientProgress, print, print_command_title, print_error, print_success

app = Typer()


@app.command()
def merge(
    po_files: Annotated[list[Path], Argument(help="Merge these `.po` files together.")],
    output_file: Annotated[Path, Option("--output-file", "-o", help="Specify the output `.po` file.")] = Path(
        "merged.po",
    ),
    overwrite: Annotated[bool, Option("--overwrite", help="Overwrite existing translations.")] = False,
) -> None:
    """Merge multiple translation files (`.po`) into one.

    The order of the files determines which translation takes priority. Empty translations in earlier files will be
    completed with translations from later files, taking the first one in the order they occur.\n
    \n
    If the option `--overwrite` is active, existing translations in earlier files will always be overwritten by
    translations in later files. In that case the last file takes precedence.\n
    \n
    The `.po` metadata is taken from the first file by default, or the last if `--overwrite` is active.
    """
    print_command_title(":shuffle_tracks_button: Odoo PO Merge")

    if len(po_files) < 2:  # noqa: PLR2004
        print_error("You need at least two .po files to merge them.")
        raise Exit

    for po_file in po_files:
        if not po_file.is_file():
            print_error(f"The file [b]{po_file}[/b] does not exist.")
            raise Exit

    print(
        f"Merging files [b]{' ← '.join(str(po_file) for po_file in po_files)}[/b]"
        f"{', overwriting translations.' if overwrite else '.'}\n",
    )

    merged_po = POFile()
    try:
        with TransientProgress() as progress:
            progress_task = progress.add_task("Merging .po files", total=len(po_files))
            for po_file in po_files:
                po = pofile(po_file)
                if po.metadata and (not merged_po.metadata or overwrite):
                    merged_po.metadata = po.metadata
                merged_po = _merge_second_po_into_first(merged_po, po)
                progress.advance(progress_task, 1)

        merged_po.sort(key=lambda entry: (entry.msgid, entry.msgctxt or ""))
        merged_po.save(str(output_file))
    except (OSError, ValueError) as e:
        print_error("Merging .po files failed.", str(e))
        raise Exit from e

    print_success(
        f"The files were successfully merged into [b]{output_file}[/b] ({merged_po.percent_translated()}% translated)",
    )


def _merge_second_po_into_first(first_po: POFile, second_po: POFile, overwrite: bool = False) -> POFile:
    """Merge the second .po file into the first, without considering order.

    :param first_po: The first .po file, that will be modified by the second.
    :param second_po: The second .po file, that will be merged into the first.
    :param overwrite: Whether to overwrite translations in the first file by ones in the second, defaults to False.
    :return: The merged .po file.
    """
    for entry in second_po:
        if entry.obsolete or entry.fuzzy:
            # Don't merge obsolete or fuzzy entries.
            continue
        existing_entry = first_po.find(entry.msgid, msgctxt=entry.msgctxt)
        if existing_entry:
            if entry.msgstr and (not existing_entry.msgstr or overwrite):
                existing_entry.msgstr = entry.msgstr
            if entry.msgstr_plural and (not existing_entry.msgstr_plural or overwrite):
                existing_entry.msgstr_plural = entry.msgstr_plural
        else:
            first_po.append(entry)
    return first_po
