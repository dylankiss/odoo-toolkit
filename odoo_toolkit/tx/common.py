from configparser import ConfigParser, DuplicateSectionError
from contextlib import suppress
from pathlib import Path
from typing import Literal, TextIO


class TxConfigError(Exception):
    """Custom exception for TxConfig errors."""

    def __init__(self, file_path: Path, error_type: Literal["load", "save"]) -> None:
        """Initialize a TxConfigError for loading `file_path`."""
        match error_type:
            case "load":
                super().__init__(f"The configuration file '{file_path}' could not be loaded.")
            case "save":
                super().__init__(f"The configuration file '{file_path}' could not be saved.")


class TxConfig:
    """Represents a Transifex config file."""

    def __init__(self, file_path: Path) -> None:
        """Initialize a TxConfig object.

        :param file_path: The file to load into the object or to save the new object to.
        :raises TxConfigError: If the given file could not be loaded.
        """
        self.file_path = file_path
        self.config = ConfigParser()

        if not self.file_path.exists():
            self.config.add_section("main")
            self.config["main"]["host"] = "https://www.transifex.com"

        if self.file_path.is_file():
            self.config.read(self.file_path)

    def add_module(self, module_path: Path, project: str, organization: str = "odoo") -> None:
        """Add a module configuration to the Transifex config file.

        :param module_path: The path to the module to add.
        :param project: The Transifex project name.
        :param organization: The Transifex organization name, defaults to "odoo".
        """
        module_name = module_path.name
        relative_module_path = module_path.relative_to(self.file_path.parent.parent)
        section_name = f"o:{organization}:p:{project}:r:{module_name}"
        with suppress(DuplicateSectionError, ValueError):
            self.config.add_section(section_name)
        self.config[section_name].update({
            "file_filter": f"{relative_module_path}/i18n/<lang>.po",
            "source_file": f"{relative_module_path}/i18n/{module_name}.pot",
            "type": "PO",
            "minimum_perc": "0",
            "resource_name": module_name,
            "replace_edited_strings": "false",
            "keep_translations": "false",
        })

    def save(self) -> None:
        """Save the Transifex config to file.

        :raises TxConfigError: If the save failed.
        """
        try:
            with self.file_path.open("w") as f:
                if "main" in self.config:
                    f.write("[main]\n")
                    self._write_section(f, "main")

                for section in sorted(s for s in self.config.sections() if s != "main"):
                    f.write(f"\n[{section}]\n")
                    self._write_section(f, section)
        except OSError as e:
            raise TxConfigError(self.file_path, "save") from e

    def _write_section(self, file: TextIO, section: str) -> None:
        max_key_length = max(len(key) for key in self.config[section])

        for key, value in self.config[section].items():
            aligned_key = key.ljust(max_key_length)
            file.write(f"{aligned_key} = {value}\n")
