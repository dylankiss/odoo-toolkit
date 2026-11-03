import subprocess

import pytest
from typer.testing import CliRunner

from odoo_toolkit.dev import clean
from odoo_toolkit.dev.clean import Leftover, find_leftovers, format_size, parse_size
from odoo_toolkit.main import app


class FakeDocker:
    """Answer the `docker ps` and `docker images` of `otk dev clean`, and remember what it removes."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> None:
        self.removed: list[list[str]] = []
        # Whether removing fails, like for an image a container still uses.
        self.fail = fail
        monkeypatch.setattr(clean, "_docker", self.query)
        monkeypatch.setattr(clean.subprocess, "run", self.run)
        monkeypatch.setattr(clean, "check_docker", lambda: None)

    def query(self, *args: str) -> str:
        if args[0] == "ps" and "name=^otk-.+-dev$" in args and "status=exited" in args:
            return "c1\totk-19.0-dev\t120MB (virtual 3.1GB)\n"
        if args[0] == "images":
            return "sha256:abc123def4567890\t3 weeks ago\t2.5GB\n"
        return ""

    def run(self, cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if self.fail:
            return subprocess.CompletedProcess(cmd, 1)
        self.removed.append(cmd[1:])
        return subprocess.CompletedProcess(cmd, 0)


@pytest.mark.parametrize(
    ("size", "expected"),
    [("2.5GB", 2_500_000_000), ("120MB", 120_000_000), ("1.2kB", 1200), ("0B", 0), ("?", 0)],
)
def test_parse_size(size: str, expected: int) -> None:
    assert parse_size(size) == expected


def test_format_size() -> None:
    assert format_size(2_500_000_000) == "2.5 GB"
    assert format_size(120) == "120.0 B"
    assert format_size(1_500_000_000_000) == "1.5 TB"


def test_docker_output_is_empty_if_it_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    returncode = 0

    def run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, returncode, stdout="c1\n")

    monkeypatch.setattr(clean.subprocess, "run", run)

    assert clean._docker("ps") == "c1\n"  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    returncode = 1
    assert clean._docker("ps") == ""  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


def test_find_leftovers_lists_containers_before_images(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDocker(monkeypatch)

    leftovers = find_leftovers()

    # Containers come first: an image can only go once no container uses it.
    assert leftovers == [
        Leftover("Dev container", "c1", "otk-19.0-dev", 120_000_000),
        Leftover("Earlier Odoo image", "sha256:abc123def4567890", "abc123def456 (3 weeks ago)", 2_500_000_000),
    ]


def test_cli_clean_removes_what_it_found(monkeypatch: pytest.MonkeyPatch) -> None:
    docker = FakeDocker(monkeypatch)

    result = CliRunner().invoke(app, ["dev", "clean", "--yes"])

    assert result.exit_code == 0, result.output
    assert docker.removed == [["rm", "c1"], ["image", "rm", "sha256:abc123def4567890"]]
    assert "Freed about 2.6 GB" in result.output


def test_cli_clean_without_leftovers(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDocker(monkeypatch)

    def nothing(*_: str) -> str:
        return ""

    monkeypatch.setattr(clean, "_docker", nothing)

    result = CliRunner().invoke(app, ["dev", "clean"])

    assert result.exit_code == 0
    assert "Nothing to clean up" in result.output


def test_cli_clean_asks_first(monkeypatch: pytest.MonkeyPatch) -> None:
    docker = FakeDocker(monkeypatch)

    result = CliRunner().invoke(app, ["dev", "clean"], input="n\n")

    assert result.exit_code == 1
    assert docker.removed == []


def test_cli_clean_says_what_it_couldnt_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDocker(monkeypatch, fail=True)

    result = CliRunner().invoke(app, ["dev", "clean", "--yes"])

    assert result.exit_code == 1
    output = " ".join(result.output.split())
    assert "Removing otk-19.0-dev, abc123def456 (3 weeks ago) failed" in output
