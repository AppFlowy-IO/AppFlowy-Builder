#!/usr/bin/env python3
"""Validate AppFlowy's private library bundle and calculate external Debian deps."""

import argparse
from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


@dataclass(frozen=True)
class Elf:
    path: Path
    soname: str | None
    architecture: str
    needed: tuple[str, ...]


@lru_cache(maxsize=None)
def read_elf(path: Path) -> Elf | None:
    with path.open("rb") as stream:
        header = stream.read(20)
    if header[:4] != b"\x7fELF":
        return None
    # Both Linux targets use little-endian, 64-bit ELF. Check the payload so
    # architecture labels cannot hide a foreign executable or private library.
    if len(header) < 20 or header[4:6] != b"\x02\x01":
        raise ValueError(f"Unsupported ELF class or byte order: {path}")
    machine = int.from_bytes(header[18:20], "little")
    architecture = {62: "amd64", 183: "arm64"}.get(machine)
    if architecture is None:
        raise ValueError(f"Unsupported ELF machine {machine}: {path}")
    dynamic = subprocess.check_output(["readelf", "--wide", "-d", str(path)], text=True)
    soname = re.search(r"\(SONAME\).*\[([^]]+)\]", dynamic)
    return Elf(path, soname[1] if soname else None, architecture,
               tuple(re.findall(r"\(NEEDED\).*\[([^]]+)\]", dynamic)))


def inspect_bundle(bundle: Path, architecture: str | None = None) -> list[Elf]:
    """Check the complete staged payload, including SONAME aliases."""
    bundle = bundle.resolve()
    policy = bundle / "host-libraries.regex"
    patterns = [
        re.compile(line) for line in policy.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not patterns:
        raise ValueError(f"Empty host library policy: {policy}")
    files = {}
    names = set()
    for path in sorted(bundle.rglob("*")):
        if any(pattern.search(path.name) for pattern in patterns):
            raise ValueError(f"Host runtime library was bundled: {path}")
        if path.is_symlink() and (
            not path.exists() or not path.resolve().is_relative_to(bundle)
        ):
            raise ValueError(f"Library symlink escapes the bundle or is broken: {path}")
        if not path.is_file():
            continue
        elf = read_elf(path.resolve())
        if elf:
            files[elf.path] = elf
            names.add(path.name)
    executable = read_elf(bundle / "AppFlowy")
    if not executable:
        raise ValueError(f"Missing native AppFlowy executable: {bundle}")
    architecture = architecture or executable.architecture
    for elf in files.values():
        if elf.architecture != architecture:
            raise ValueError(
                f"ELF architecture mismatch: expected {architecture}, "
                f"found {elf.architecture}: {elf.path}"
            )
        if elf.soname and elf.soname not in names:
            raise ValueError(f"Missing SONAME alias {elf.soname} for {elf.path}")
        for needed in elf.needed:
            if needed not in names and not any(p.search(needed) for p in patterns):
                raise ValueError(f"{elf.path.name} needs unbundled non-host library {needed}")
    return list(files.values())


def copy_library(source, destination):
    # Private staging supplies dpkg's package-root context for $ORIGIN. Hard
    # links avoid recopying hundreds of MB; no staged ELF is ever modified.
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
    return destination


def debian_dependencies(bundle: Path) -> str:
    # dpkg-shlibdeps must resolve dependencies against a matching native host.
    architecture = subprocess.check_output(
        ["dpkg", "--print-architecture"], text=True,
    ).strip()
    inspect_bundle(bundle, architecture)
    with tempfile.TemporaryDirectory(prefix="appflowy-shlibs-") as directory:
        work = Path(directory)
        package = work / "debian/appflowy"
        stage = package / "usr/lib/AppFlowy"
        shutil.copytree(bundle, stage, symlinks=True, copy_function=copy_library)
        (package / "DEBIAN").mkdir()
        (work / "debian/control").write_text(
            "Source: appflowy\nMaintainer: AppFlowy\n\n"
            "Package: appflowy\nArchitecture: any\nDescription: AppFlowy\n"
        )
        elves = inspect_bundle(stage)
        shlibs = []
        private = []
        for elf in elves:
            if not elf.soname:
                continue
            match = re.fullmatch(r"(.+)\.so\.(.+)", elf.soname)
            if match is None:
                match = re.fullmatch(r"(.+)-(\d.*)\.so", elf.soname)
            if match:
                shlibs.append(f"{match[1]} {match[2]} appflowy")
            else:
                private.append(elf.path)
        local = work / "debian/shlibs.local"
        local.write_text("\n".join(sorted(set(shlibs))) + "\n")
        if private:
            # Flutter's unversioned plugin SONAMEs cannot be represented in a
            # shlibs file. Record their symbols as owned by this package too.
            subprocess.run(
                ["dpkg-gensymbols", "-q", "-pappflowy", "-v0", "-c0", f"-P{package}",
                 *[f"-e{path}" for path in private]],
                cwd=work, check=True,
            )
        command = [
            "dpkg-shlibdeps", "-O", f"-S{package}", f"-L{local}", "-xappflowy",
            *[f"-l{path}" for path in sorted({elf.path.parent for elf in elves})],
            *[str(elf.path) for elf in elves if elf.needed],
        ]
        output = subprocess.check_output(command, cwd=work, text=True)
        matches = re.findall(r"^shlibs:Depends=(.+)$", output, re.MULTILINE)
        if len(matches) != 1:
            raise ValueError(f"No unambiguous Debian dependencies generated:\n{output}")
        clauses = matches[0].split(", ")
        # dlopen and desktop integration requirements are not necessarily
        # visible in ELF NEEDED entries.
        dynamic = ("libnotify4", "libkeybinder-3.0-0", "libgles2",
                   "libgstreamer1.0-0", "libgstreamer-plugins-base1.0-0")
        present = {clause.split()[0] for clause in clauses}
        return ", ".join(sorted(clauses + [name for name in dynamic if name not in present]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("audit")
    audit.add_argument("bundle", type=Path)
    audit.add_argument("--architecture", choices=("amd64", "arm64"))
    deps = subparsers.add_parser("depends")
    deps.add_argument("bundle", type=Path)
    deps.add_argument("--control", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "audit":
        elves = inspect_bundle(args.bundle, args.architecture)
        print(f"Validated {len(elves)} ELF files: {args.bundle}")
    else:
        dependencies = debian_dependencies(args.bundle.resolve())
        architecture = read_elf(args.bundle.resolve() / "AppFlowy").architecture
        control = args.control.read_text()
        # Stage both edits before writing so malformed metadata is unchanged.
        for field, value in (("Architecture", architecture), ("Depends", dependencies)):
            control, count = re.subn(
                rf"(?m)^{field}:.*$", lambda _: f"{field}: {value}", control,
            )
            if count != 1:
                raise ValueError(f"Expected exactly one {field} field in {args.control}")
        args.control.write_text(control)
        print("Architecture: " + architecture)
        print("Depends: " + dependencies)


if __name__ == "__main__":
    main()
