#!/usr/bin/env python3
"""Exercise the Windows release gate with real PDFium and Inno Setup packages.

Fixtures contain a copy of Python.exe as AppFlowy.exe; that executable is never
launched. Only the packaged PDFium DLL runs. No real application is installed.
"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

from verify_windows_pdf import ASSET_ID


PDFIUM_URL = "https://github.com/bblanchon/pdfium-binaries/releases/download/chromium/7811/pdfium-win-x64.tgz"
PDFIUM_SHA256 = "2e7af12674ac3716cb0e20369bb9fb269ceadfa2f0b0597097a520e6834175a0"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        raise ValueError("This integration test requires Windows x64")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    payload = root / "payload"
    payload.mkdir()
    shutil.copyfile(sys.executable, payload / "AppFlowy.exe")
    with urllib.request.urlopen(PDFIUM_URL, timeout=60) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != PDFIUM_SHA256:
        raise ValueError("PDFium fixture archive digest mismatch")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
        (payload / "pdfium.dll").write_bytes(source.extractfile("bin/pdfium.dll").read())
    manifest = payload / "data/flutter_assets/NativeAssetsManifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "native-assets": {"windows_x64": {ASSET_ID: ["absolute", "pdfium.dll"]}}
    }), encoding="utf-8")
    for name in ("good", "missing", "invalid"):
        with zipfile.ZipFile(root / f"{name}.zip", "w", zipfile.ZIP_DEFLATED) as target:
            for path in payload.rglob("*"):
                if not path.is_file() or (name == "missing" and path.name == "pdfium.dll"):
                    continue
                data = path.read_bytes()
                if name == "invalid" and path.name == "pdfium.dll":
                    # A valid x64 PE file with none of PDFium's exports must not
                    # pass merely because its filename and architecture match.
                    data = (payload / "AppFlowy.exe").read_bytes()
                target.writestr("AppFlowy/" + path.relative_to(payload).as_posix(), data)

    compiler = shutil.which("iscc") or str(
        Path(os.environ["ProgramFiles(x86)"]) / "Inno Setup 6/ISCC.exe"
    )
    for name, exclude in (("good", ""), ("missing", '; Excludes: "pdfium.dll"')):
        script = root / f"{name}.iss"
        script.write_text(f'''[Setup]
AppName=AppFlowy PDF smoke fixture
AppId=AppFlowyPdfSmokeFixture
AppVersion=1.0
DefaultDirName={{tmp}}\\AppFlowyPdfSmokeFixture
PrivilegesRequired=lowest
Uninstallable=no
CreateUninstallRegKey=no
UsePreviousAppDir=no
OutputDir={root}
OutputBaseFilename={name}
[Files]
Source: "{payload}\\*"; DestDir: "{{app}}"; Flags: recursesubdirs createallsubdirs{exclude}
''', encoding="utf-8")
        result = subprocess.run([compiler, str(script)], capture_output=True, text=True, timeout=120)
        (root / f"{name}-compile.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        result.check_returncode()

    verifier = Path(__file__).resolve().with_name("verify_windows_pdf.py")
    cases = [
        ("positive", "good.zip", "good.exe", None),
        ("missing-zip-dll", "missing.zip", "good.exe", "PDFium DLL is missing"),
        ("missing-installer-dll", "good.zip", "missing.exe", "PDFium DLL is missing"),
        ("invalid-native-dll", "invalid.zip", "good.exe", "PDFium probe failed"),
    ]
    results = []
    for name, zip_name, installer_name, expected in cases:
        result = subprocess.run(
            [sys.executable, str(verifier), "release", "--zip", str(root / zip_name),
             "--installer", str(root / installer_name), "--output", str(root / name)],
            capture_output=True, text=True, timeout=240,
        )
        (root / f"{name}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        report = json.loads((root / name / "report.json").read_text())
        if expected is None:
            if result.returncode or report["status"] != "passed":
                raise AssertionError(f"Healthy packages failed: {result.stdout}\n{result.stderr}")
        elif not result.returncode or report["status"] != "failed" or expected not in report["error"]:
            raise AssertionError(f"{name} failed for the wrong reason: {result.stdout}\n{result.stderr}")
        results.append({"case": name, "exit_status": result.returncode, "expected_error": expected})
        print(f"PASS {name}: exit {result.returncode}", flush=True)
    (root / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
