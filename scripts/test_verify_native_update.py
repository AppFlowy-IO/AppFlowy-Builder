import base64
import hashlib
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml
from cryptography.hazmat.primitives.asymmetric import ed25519

from verify_native_update import public_key_for, validate_macos_bundle, verify_release_asset


ROOT = Path(__file__).resolve().parents[1]


def bundle_fixture(bundle, **overrides):
    info = {
        "CFBundleIdentifier": "com.appflowy.appflowy.flutter",
        "CFBundleShortVersionString": "0.14.11",
        "CFBundleVersion": "0.14.11",
        "SUPublicEDKey": public_key_for("macos").decode().strip(),
    }
    info.update(overrides)
    info = {key: value for key, value in info.items() if value is not None}
    path = bundle / "Contents/Info.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(info))
    return path


class BundleTests(unittest.TestCase):
    def test_released_01411_missing_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "AppFlowy.app"
            bundle_fixture(bundle, SUPublicEDKey=None)
            with self.assertRaisesRegex(ValueError, "SUPublicEDKey"):
                validate_macos_bundle(bundle, "0.14.11")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/verify_native_update.py"),
                 "macos-bundle", "--bundle", str(bundle), "--version", "0.14.11"],
                capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("SUPublicEDKey", result.stderr)

    def test_retaining_deployed_key_preserves_real_build_version(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "AppFlowy.app"
            path = bundle_fixture(bundle, CFBundleVersion="1793000000")
            original = path.read_bytes()
            self.assertEqual(validate_macos_bundle(bundle, "0.14.11"), "1793000000")
            self.assertEqual(path.read_bytes(), original)

    def test_invalid_and_accidentally_rotated_keys_are_rejected(self):
        other_key = base64.b64encode(ed25519.Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
        for key in ["", "!not-base64!", base64.b64encode(b"short").decode(), other_key, 123]:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                bundle = Path(directory) / "AppFlowy.app"
                bundle_fixture(bundle, SUPublicEDKey=key)
                with self.assertRaisesRegex(ValueError, "SUPublicEDKey"):
                    validate_macos_bundle(bundle, "0.14.11")

    def test_bundle_identity_and_versions_must_match(self):
        for overrides in [
            {"CFBundleIdentifier": "other.app"},
            {"CFBundleShortVersionString": "0.14.10"},
            {"CFBundleVersion": None},
            {"CFBundleVersion": "bad-build"},
        ]:
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as directory:
                bundle = Path(directory) / "AppFlowy.app"
                bundle_fixture(bundle, **overrides)
                with self.assertRaises(ValueError):
                    validate_macos_bundle(bundle, "0.14.11")

    @unittest.skipUnless(sys.platform == "darwin", "macOS packaging shell step")
    def test_actual_packaging_step_rejects_each_incompatible_architecture(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/macos.yaml").read_text())
        step = next(s for s in workflow["jobs"]["universal"]["steps"]
                    if s.get("name") == "Verify macOS update compatibility")
        with tempfile.TemporaryDirectory(prefix="update check with spaces ") as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "scripts", root / ".builder/scripts")
            release = root / "release packages"
            architectures = ("universal", "arm64", "x86_64")
            for arch in architectures:
                bundle_fixture(release / arch / "AppFlowy.app")
            environment = {**os.environ, "MACOS_APP_RELEASE_PATH": str(release), "BUILD_VERSION": "0.14.11",
                           "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}

            def check():
                return subprocess.run(["bash", "-e", "-o", "pipefail", "-c", step["run"]],
                                      cwd=root, env=environment, capture_output=True, text=True)

            result = check()
            self.assertEqual(result.returncode, 0, result.stderr)
            for arch in architectures:
                bundle = release / arch / "AppFlowy.app"
                bundle_fixture(bundle, SUPublicEDKey=None)
                result = check()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("SUPublicEDKey", result.stderr)
                bundle_fixture(bundle)


class ReleaseAssetTests(unittest.TestCase):
    def test_same_size_replacement_is_rejected_by_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = Path(directory) / "app.exe"
            installer.write_bytes(b"original")
            asset = {"name": "app.exe", "size": 8,
                     "digest": "sha256:" + hashlib.sha256(b"original").hexdigest()}
            release = {"assets": [asset]}
            verify_release_asset(release, "app.exe", installer)
            installer.write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                verify_release_asset(release, "app.exe", installer)

    def test_missing_duplicate_empty_and_wrong_sized_assets_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = Path(directory) / "app.exe"
            installer.write_bytes(b"installer")
            for assets in [[], [{"name": "app.exe", "size": 9}] * 2,
                           [{"name": "app.exe", "size": 10}], [{"name": "app.exe", "size": 0}]]:
                with self.subTest(assets=assets), self.assertRaises(ValueError):
                    verify_release_asset({"assets": assets}, "app.exe", installer)


if __name__ == "__main__":
    unittest.main()
