#!/usr/bin/env python3
"""Render a known PDF with PDFium from the final Windows ZIP and installer.

Run only on a disposable Windows release runner. The installer is installed
silently into a temporary directory; AppFlowy itself is never launched.
"""

import argparse
import ctypes
import hashlib
import json
from pathlib import Path, PurePosixPath
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib


ASSET_ID = "package:pdfium_dart/libpdfium"


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def require_windows_x64(path):
    with path.open("rb") as source:
        if source.read(2) != b"MZ":
            raise ValueError(f"Not a Windows binary: {path}")
        source.seek(0x3C)
        offset = source.read(4)
        if len(offset) != 4:
            raise ValueError(f"Truncated Windows binary: {path}")
        source.seek(int.from_bytes(offset, "little"))
        if source.read(6) != b"PE\0\0\x64\x86":
            raise ValueError(f"Expected a Windows x64 binary: {path}")


def inspect_bundle(bundle):
    executable = bundle / "AppFlowy.exe"
    require_windows_x64(executable)
    manifest = json.loads(
        (bundle / "data/flutter_assets/NativeAssetsManifest.json").read_text(encoding="utf-8")
    )
    asset = manifest.get("native-assets", {}).get("windows_x64", {}).get(ASSET_ID)
    if asset != ["absolute", "pdfium.dll"]:
        raise ValueError(f"Windows PDFium manifest must declare a bundled pdfium.dll: {asset!r}")
    library = bundle / "pdfium.dll"
    if not library.is_file():
        raise ValueError("Packaged PDFium DLL is missing beside AppFlowy.exe")
    require_windows_x64(library)
    return library


def extract_zip(archive, destination):
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            path = PurePosixPath(member.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or any(":" in part for part in path.parts):
                raise ValueError(f"ZIP member escapes the extraction directory: {member.filename}")
            source.extract(member, destination)
    return destination / "AppFlowy"


def sample_pdf():
    """One 64-point page with a black square on a white background; no fonts."""
    content = b"0 g 16 16 32 32 re f\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 64 64] /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
    ]
    result = bytearray(b"%PDF-1.7\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(result)


def write_png(path, pixels, stride):
    rows = bytearray()
    for y in range(64):
        rows.append(0)  # PNG filter: none.
        for x in range(64):
            offset = y * stride + x * 4
            rows.extend(pixels[offset:offset + 3][::-1])

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    )


def render_pdf(library, image):
    if sys.platform == "win32":
        # Resolve the exact packaged DLL and dependencies only from its directory
        # or System32, so a build checkout/PATH cannot hide missing runtime files.
        dll = ctypes.WinDLL(str(library.resolve()), winmode=0x100 | 0x800)
    else:
        # Allows the renderer itself to be checked with native Linux PDFium.
        # The release command below still requires a Windows x64 host.
        dll = ctypes.CDLL(str(library.resolve()))
    pointer = ctypes.c_void_p
    integer = ctypes.c_int
    signatures = {
        "FPDF_InitLibrary": ([], None),
        "FPDF_DestroyLibrary": ([], None),
        "FPDF_LoadMemDocument64": ([pointer, ctypes.c_size_t, ctypes.c_char_p], pointer),
        "FPDF_CloseDocument": ([pointer], None),
        "FPDF_GetPageCount": ([pointer], integer),
        "FPDF_LoadPage": ([pointer, integer], pointer),
        "FPDF_ClosePage": ([pointer], None),
        "FPDFBitmap_Create": ([integer, integer, integer], pointer),
        "FPDFBitmap_Destroy": ([pointer], None),
        "FPDFBitmap_FillRect": ([pointer, integer, integer, integer, integer, ctypes.c_ulong], None),
        "FPDF_RenderPageBitmap": ([pointer, pointer] + [integer] * 6, None),
        "FPDFBitmap_GetBuffer": ([pointer], pointer),
        "FPDFBitmap_GetStride": ([pointer], integer),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(dll, name)
        function.argtypes = arguments
        function.restype = result
    document = page = bitmap = None
    data = sample_pdf()
    buffer = ctypes.create_string_buffer(data)
    dll.FPDF_InitLibrary()
    try:
        document = dll.FPDF_LoadMemDocument64(buffer, len(data), None)
        if not document or dll.FPDF_GetPageCount(document) != 1:
            raise ValueError("Packaged PDFium could not open the known one-page PDF")
        page = dll.FPDF_LoadPage(document, 0)
        bitmap = dll.FPDFBitmap_Create(64, 64, 1)
        if not page or not bitmap:
            raise ValueError("Packaged PDFium could not allocate the page/bitmap")
        dll.FPDFBitmap_FillRect(bitmap, 0, 0, 64, 64, 0xFFFFFFFF)
        dll.FPDF_RenderPageBitmap(bitmap, page, 0, 0, 64, 64, 0, 0)
        stride = dll.FPDFBitmap_GetStride(bitmap)
        address = dll.FPDFBitmap_GetBuffer(bitmap)
        if not address or not 256 <= stride <= 4096:
            raise ValueError("Packaged PDFium returned an invalid bitmap")
        pixels = ctypes.string_at(address, stride * 64)
        write_png(image, pixels, stride)
        for x, y, expected in [(4, 4, b"\xff" * 3), (60, 60, b"\xff" * 3),
                               (24, 24, b"\x00" * 3), (40, 40, b"\x00" * 3)]:
            offset = y * stride + x * 4
            if pixels[offset:offset + 3] != expected:
                raise ValueError(f"Packaged PDFium rendered incorrect pixels at ({x}, {y})")
        return {"page_count": 1, "rendered_size": [64, 64], "image": str(image),
                "pdf_sha256": hashlib.sha256(data).hexdigest()}
    finally:
        if bitmap:
            dll.FPDFBitmap_Destroy(bitmap)
        if page:
            dll.FPDF_ClosePage(page)
        if document:
            dll.FPDF_CloseDocument(document)
        dll.FPDF_DestroyLibrary()


def probe_bundle(bundle, output, label):
    library = inspect_bundle(bundle)
    command = [sys.executable, str(Path(__file__).resolve()), "probe",
               "--library", str(library), "--image", str(output / f"{label}.png")]
    # The native DLL runs in a separate process; a crash/hang must fail the gate.
    result = subprocess.run(command, cwd=bundle, capture_output=True, text=True, timeout=30)
    (output / f"{label}-probe.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise ValueError(f"Packaged PDFium probe failed ({label}, exit {result.returncode}): {result.stderr}")
    return {"pdfium_sha256": sha256(library), "app_sha256": sha256(bundle / "AppFlowy.exe"),
            **json.loads(result.stdout)}


def verify_packages(archive, installer, output):
    output.mkdir(parents=True, exist_ok=False)
    report = {"status": "failed", "packages": {}}
    try:
        if sys.platform != "win32" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise ValueError("Release PDF validation requires a Windows x64 Python host")
        report["packages"] = {
            "zip": {"path": str(archive), "sha256": sha256(archive)},
            "installer": {"path": str(installer), "sha256": sha256(installer)},
        }
        with tempfile.TemporaryDirectory(prefix="appflowy-pdf-release-") as directory:
            root = Path(directory)
            bundle = extract_zip(archive, root / "zip")
            report["packages"]["zip"].update(probe_bundle(bundle, output, "zip"))
            installed = root / "installed"
            subprocess.run(
                [str(installer.resolve()), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                 "/SP-", "/NOICONS", f"/DIR={installed}", f"/LOG={output / 'installer.log'}"],
                check=True, timeout=180,
            )
            report["packages"]["installer"].update(probe_bundle(installed, output, "installer"))
            for key in ("pdfium_sha256", "app_sha256"):
                if report["packages"]["zip"][key] != report["packages"]["installer"][key]:
                    raise ValueError(f"ZIP and installed EXE contain different {key}")
        report["status"] = "passed"
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    release = commands.add_parser("release")
    release.add_argument("--zip", type=Path, required=True)
    release.add_argument("--installer", type=Path, required=True)
    release.add_argument("--output", type=Path, required=True)
    probe = commands.add_parser("probe")
    probe.add_argument("--library", type=Path, required=True)
    probe.add_argument("--image", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "probe":
        result = render_pdf(args.library, args.image)
    else:
        result = verify_packages(args.zip.resolve(), args.installer.resolve(), args.output.resolve())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
