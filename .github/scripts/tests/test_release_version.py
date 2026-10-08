#!/usr/bin/env python3
"""Exercise release version validation through the same CLI used by Builder."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


VERIFIER = Path(__file__).resolve().parents[1] / "verify_release_version.py"


class ReleaseVersionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="release-version-test-")
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name)
        self.files = {
            "pubspec": self.source / "frontend/appflowy_flutter/pubspec.yaml",
            "makefile": self.source / "frontend/Makefile.toml",
            "compatibility": self.source
            / "frontend/rust-lib/flowy-user/resources/desktop_server_compatibility.json",
        }
        for path in self.files.values():
            path.parent.mkdir(parents=True, exist_ok=True)
        self.write_versions()

    def write_versions(self, pubspec="0.14.8", makefile="0.14.8", compatibility="0.14.8"):
        self.files["pubspec"].write_text(f"name: appflowy\nversion: {pubspec}\n")
        self.files["makefile"].write_text(f'[env]\nAPPFLOWY_VERSION = "{makefile}"\n')
        self.files["compatibility"].write_text(json.dumps({
            "reviewed_through_client_version": compatibility,
        }))

    def verify(self):
        before = {key: path.read_bytes() for key, path in self.files.items() if path.exists()}
        result = subprocess.run(
            [sys.executable, str(VERIFIER), "--source", str(self.source), "--version", "0.14.8"],
            capture_output=True,
            text=True,
        )
        after = {key: path.read_bytes() for key, path in self.files.items() if path.exists()}
        self.assertEqual(before, after, "Verification must not rewrite the checkout")
        return result

    def test_matching_versions_pass(self):
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_old_release_branch_fails_for_all_three_fields(self):
        self.write_versions("0.14.7", "0.14.7", "0.14.7")
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        for field in ("pubspec.yaml", "APPFLOWY_VERSION", "reviewed_through_client_version"):
            self.assertIn(field, result.stderr)
        self.assertIn("0.14.7", result.stderr)
        self.assertIn("0.14.8", result.stderr)

    def test_each_partial_version_update_fails(self):
        for field in self.files:
            with self.subTest(field=field):
                self.write_versions(**{field: "0.14.7"})
                result = self.verify()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("does not match the source", result.stderr)

    def test_missing_version_files_fail(self):
        for field, path in self.files.items():
            with self.subTest(field=field):
                self.write_versions()
                path.unlink()
                result = self.verify()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(str(path), result.stderr)

    def test_duplicate_pubspec_versions_fail(self):
        self.files["pubspec"].write_text("version: 0.14.8\nversion: 0.14.7\n")
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly one version", result.stderr)

    def test_malformed_metadata_fails(self):
        for field in ("makefile", "compatibility"):
            with self.subTest(field=field):
                self.write_versions()
                self.files[field].write_text("invalid contents")
                result = self.verify()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Release version verification failed", result.stderr)

    def test_pubspec_comment_is_allowed(self):
        self.files["pubspec"].write_text("version: 0.14.8 # release version\n")
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
