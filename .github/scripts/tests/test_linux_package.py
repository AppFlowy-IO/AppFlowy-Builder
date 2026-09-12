#!/usr/bin/env python3
"""Audit a real CMake bundle, then damage private libraries and host boundaries."""

import argparse
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile

spec = importlib.util.spec_from_file_location(
    "linux_package", Path(__file__).resolve().parents[1] / "linux_package.py",
)
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    args = parser.parse_args()
    package.inspect_bundle(args.bundle)
    print("PASS complete bundle")
    for damage in ("host-runtime", "missing-soname", "missing-codec", "escaping-link"):
        with tempfile.TemporaryDirectory(prefix="appflowy-audit-test-") as directory:
            bundle = Path(directory) / "AppFlowy"
            shutil.copytree(
                args.bundle, bundle, symlinks=True, copy_function=package.copy_library,
            )
            if damage == "host-runtime":
                # Even a renamed ELF/SONAME alias must not smuggle this host
                # runtime back into an archive's executable search directory.
                shutil.copyfile(bundle / "AppFlowy", bundle / "lib/libstdc++.so.6")
                expected = "Host runtime library was bundled"
            elif damage == "missing-soname":
                (bundle / "lib/libmpv.so.1").unlink()
                expected = "libmpv.so.1"
            elif damage == "missing-codec":
                for path in (bundle / "lib").glob("libavcodec.so*"):
                    path.unlink()
                expected = "libavcodec.so"
            else:
                (bundle / "lib/escaping.so").symlink_to("/usr/lib")
                expected = "escapes the bundle"
            try:
                package.inspect_bundle(bundle)
            except ValueError as error:
                if expected not in str(error):
                    raise AssertionError(f"{damage}: wrong failure: {error}") from error
                print(f"PASS {damage}: {error}")
            else:
                raise AssertionError(f"Damaged bundle passed audit: {damage}")


if __name__ == "__main__":
    main()
