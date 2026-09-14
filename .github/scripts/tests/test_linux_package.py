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
    elves = package.inspect_bundle(args.bundle)
    mpv = next(elf for elf in elves if elf.soname and elf.soname.startswith("libmpv.so."))
    print("PASS complete bundle")
    architecture = package.read_elf(args.bundle.resolve() / "AppFlowy").architecture
    foreign_architecture = "arm64" if architecture == "amd64" else "amd64"
    try:
        package.inspect_bundle(args.bundle, foreign_architecture)
    except ValueError as error:
        if "ELF architecture mismatch" not in str(error):
            raise AssertionError(f"Wrong architecture failed for another reason: {error}") from error
        print("PASS executable rejected for wrong target")
    else:
        raise AssertionError("Bundle passed audit for the wrong target architecture")
    for damage in ("host-runtime", "missing-soname", "missing-codec", "escaping-link", "foreign-elf"):
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
                (bundle / "lib" / mpv.soname).unlink()
                # The unversioned loader alias may point through this SONAME.
                expected = "libmpv.so"
            elif damage == "missing-codec":
                for path in (bundle / "lib").glob("libavcodec.so*"):
                    path.unlink()
                expected = "libavcodec.so"
            elif damage == "foreign-elf":
                # Do not hardlink this copy: changing its header must leave the
                # original application bundle used for packaging untouched.
                foreign = bundle / "lib/foreign.so"
                shutil.copyfile(bundle / "AppFlowy", foreign)
                machine = 183 if architecture == "amd64" else 62
                with foreign.open("r+b") as stream:
                    stream.seek(18)
                    stream.write(machine.to_bytes(2, "little"))
                expected = "ELF architecture mismatch"
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
