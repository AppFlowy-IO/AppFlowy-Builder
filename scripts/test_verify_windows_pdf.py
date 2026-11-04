import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

import yaml

import verify_windows_pdf as pdf


ROOT = Path(__file__).resolve().parents[1]


def bundle_fixture(bundle):
    bundle.mkdir(parents=True)
    header = bytearray(128)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, 64)
    header[64:70] = b"PE\0\0\x64\x86"
    for name in ("AppFlowy.exe", "pdfium.dll"):
        (bundle / name).write_bytes(header)
    manifest = bundle / "data/flutter_assets/NativeAssetsManifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "native-assets": {"windows_x64": {pdf.ASSET_ID: ["absolute", "pdfium.dll"]}}
    }), encoding="utf-8")
    return manifest


def archive_fixture(bundle, archive):
    with zipfile.ZipFile(archive, "w") as output:
        for path in bundle.rglob("*"):
            if path.is_file():
                output.write(path, "AppFlowy/" + path.relative_to(bundle).as_posix())


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "AppFlowy"
        self.manifest = bundle_fixture(self.bundle)

    def test_declared_dll_is_resolved_only_beside_the_app(self):
        self.assertEqual(pdf.inspect_bundle(self.bundle), self.bundle / "pdfium.dll")
        (self.bundle / "pdfium.dll").rename(self.root / "pdfium.dll")
        with self.assertRaisesRegex(ValueError, "PDFium DLL is missing beside AppFlowy.exe"):
            pdf.inspect_bundle(self.bundle)

    def test_missing_or_development_manifest_is_rejected(self):
        for entry in [None, ["absolute", "C:/build/pdfium.dll"], ["process"]]:
            with self.subTest(entry=entry):
                self.manifest.write_text(json.dumps({
                    "native-assets": {"windows_x64": {pdf.ASSET_ID: entry}}
                }), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "manifest must declare a bundled pdfium.dll"):
                    pdf.inspect_bundle(self.bundle)

    def test_missing_manifest_fails(self):
        self.manifest.unlink()
        with self.assertRaises(FileNotFoundError):
            pdf.inspect_bundle(self.bundle)

    def test_damaged_and_wrong_architecture_binaries_are_rejected(self):
        for name in ("AppFlowy.exe", "pdfium.dll"):
            path = self.bundle / name
            original = path.read_bytes()
            for data in (b"", b"MZ", original[:68] + b"\xaa\x64" + original[70:]):
                with self.subTest(name=name, data=data[:2]):
                    path.write_bytes(data)
                    with self.assertRaises(ValueError):
                        pdf.inspect_bundle(self.bundle)
            path.write_bytes(original)

    def test_archive_is_audited_after_extraction(self):
        archive = self.root / "app.zip"
        archive_fixture(self.bundle, archive)
        extracted = pdf.extract_zip(archive, self.root / "extracted")
        self.assertEqual(pdf.inspect_bundle(extracted).read_bytes(), (self.bundle / "pdfium.dll").read_bytes())

    def test_archive_cannot_escape_the_temporary_directory(self):
        archive = self.root / "bad.zip"
        for name in ("../pdfium.dll", "AppFlowy/../../pdfium.dll", "C:/pdfium.dll"):
            with self.subTest(name=name):
                with zipfile.ZipFile(archive, "w") as output:
                    output.writestr(name, b"bad")
                with self.assertRaisesRegex(ValueError, "escapes the extraction directory"):
                    pdf.extract_zip(archive, self.root / "extracted")

    def test_native_probe_failure_is_not_accepted_as_a_pass(self):
        completed = subprocess.CompletedProcess([], 7, "", "loader failed")
        with mock.patch.object(pdf.subprocess, "run", return_value=completed) as run:
            with self.assertRaisesRegex(ValueError, "probe failed .*exit 7.*loader failed"):
                pdf.probe_bundle(self.bundle, self.root, "zip")
        self.assertEqual(run.call_args.kwargs["cwd"], self.bundle)
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_native_hang_fails_and_preserves_failed_release_report(self):
        archive = self.root / "app.zip"
        archive_fixture(self.bundle, archive)
        installer = self.root / "installer.exe"
        installer.write_bytes(b"installer fixture; never executed")
        output = self.root / "evidence"
        with mock.patch.object(pdf.sys, "platform", "win32"), mock.patch.object(
            pdf.subprocess, "run", side_effect=subprocess.TimeoutExpired("probe", 30)
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                pdf.verify_packages(archive, installer, output)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["packages"]["zip"]["sha256"], pdf.sha256(archive))


class WorkflowTests(unittest.TestCase):
    def test_final_packages_are_checked_before_either_package_upload(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/windows.yaml").read_text())
        steps = workflow["jobs"]["build"]["steps"]
        indices = {step.get("name"): index for index, step in enumerate(steps)}
        gate = indices["Verify packaged Windows PDFs"]
        self.assertLess(indices["Build installer executable"], gate)
        for name in ("Upload EXE", "Upload EXE to Release", "Upload ZIP", "Upload ZIP to Release"):
            self.assertLess(gate, indices[name])
        self.assertFalse(steps[gate].get("continue-on-error", False))
        self.assertIn("verify_windows_pdf.py release", steps[gate]["run"])
        self.assertIn("if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }", steps[gate]["run"])


if __name__ == "__main__":
    unittest.main()
