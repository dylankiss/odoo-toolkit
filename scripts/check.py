"""Run every check the CI runs, in the same order, and stop at the first one that fails.

Usage: `uv run scripts/check.py`
"""

import subprocess
import sys

CHECKS = [
    ["ruff", "check", "odoo_toolkit", "scripts", "tests"],
    ["ruff", "format", "--check", "odoo_toolkit", "scripts", "tests"],
    ["pyright"],
    [sys.executable, "scripts/sync_readme.py", "--check"],
    ["pytest", "-q"],
]


def main() -> int:
    """Run the checks.

    :return: The exit code of the first check that failed, or 0.
    """
    for check in CHECKS:
        print(f"$ {' '.join(check)}", flush=True)
        if returncode := subprocess.run(check, check=False).returncode:
            return returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
