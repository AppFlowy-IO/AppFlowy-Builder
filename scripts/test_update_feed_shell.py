"""Exercise the actual workflow shell boundary, including macOS Bash 3.2."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


class FeedShellTests(unittest.TestCase):
    def test_feed_generation_handles_absent_and_present_optional_metadata(self):
        workflow = Path(__file__).resolve().parents[1] / ".github/workflows/auto_update.yaml"
        steps = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]["native"]["steps"]
        script = next(step["run"] for step in steps if step.get("name") == "Generate legacy and native appcasts")
        # The macOS system shell reproduces the empty-array/nounset failure;
        # Homebrew's newer bash would hide that compatibility regression.
        bash = "/bin/bash" if sys.platform == "darwin" else shutil.which("bash")
        if sys.platform == "win32":
            # Use the same Git Bash as Actions, even when WSL's bash.exe occurs
            # earlier on PATH. No WSL distribution is needed for this check.
            git = shutil.which("git")
            self.assertIsNotNone(git)
            bash = str(Path(git).resolve().parents[1] / "bin/bash.exe")
            self.assertTrue(Path(bash).is_file())
        self.assertIsNotNone(bash)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "scripts/generate_native_appcast.py").write_text(
                "import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8"
            )
            for critical, floor in [("false", ""), ("true", "0.19.10")]:
                with self.subTest(critical=critical, minimum_server_version=floor):
                    env = {**os.environ, "OS": "macos", "ARCH": "arm64", "PACKAGE_ARCH": "universal",
                           "TAG": "0.14.12", "BUILD_VERSION": "0.14.12", "CRITICAL": critical,
                           "MINIMUM_SERVER_VERSION": floor, "REPOSITORY": "AppFlowy-IO/AppFlowy",
                           "UPDATER_GENERATION": "both"}
                    result = subprocess.run([bash, "--noprofile", "--norc", "-c", script],
                                            cwd=root, env=env, text=True, capture_output=True, check=True)
                    arguments = json.loads(result.stdout)
                    self.assertEqual(arguments[arguments.index("--installer") + 1], "app.dmg")
                    self.assertEqual("--critical" in arguments, critical == "true")
                    self.assertEqual("--minimum-server-version" in arguments, bool(floor))
                    self.assertNotIn("", arguments)
                    if floor:
                        self.assertEqual(arguments[arguments.index("--minimum-server-version") + 1], floor)


if __name__ == "__main__":
    unittest.main()
