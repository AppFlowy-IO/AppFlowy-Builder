#!/usr/bin/env python3
"""Exercise the smoke gate with real GTK/X11 surfaces, including abort(3)."""

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    args = parser.parse_args()
    runner = Path(__file__).resolve().parents[1] / "linux_smoke.py"
    with tempfile.TemporaryDirectory(prefix="appflowy-smoke-test-") as directory:
        outcomes = {
            "render": "rendered a window and stayed alive",
            "abort": "exited before smoke check completed: -6",
            "exit": "exited before smoke check completed: 0",
            "blank": "No stable, rendered AppFlowy window",
            "hidden": "No stable, rendered AppFlowy window",
        }
        for mode, expected in outcomes.items():
            result = subprocess.run(
                ["xvfb-run", "-a", sys.executable, str(runner),
                 "--startup-timeout", "4", "--observe", "2",
                 "--log", str(Path(directory) / f"{mode}.log"),
                 str(args.fixture.resolve()), mode],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if ((result.returncode == 0) != (mode == "render")
                    or expected not in result.stdout):
                raise AssertionError(f"{mode}: {result.stdout}\n{result.stderr}")
            print(f"PASS {mode}: {result.stdout.splitlines()[0]}")


if __name__ == "__main__":
    main()
