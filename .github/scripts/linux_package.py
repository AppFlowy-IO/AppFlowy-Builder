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
    needed: tuple[str, ...]


@lru_cache(maxsize=None)
def read_elf(path: Path) -> Elf | None:
    with path.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            return None
    dynamic = subprocess.check_output(["readelf", "--wide", "-d", str(path)], text=True)
    soname = re.search(r"\(SONAME\).*\[([^]]+)\]", dynamic)
    return Elf(path, soname[1] if soname else None,
               tuple(re.findall(r"\(NEEDED\).*\[([^]]+)\]", dynamic)))


def inspect_bundle(bundle: Path) -> list[Elf]:
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
    if not read_elf(bundle / "AppFlowy"):
        raise ValueError(f"Missing native AppFlowy executable: {bundle}")
    for elf in files.values():
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
    inspect_bundle(bundle)
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
    deps = subparsers.add_parser("depends")
    deps.add_argument("bundle", type=Path)
    deps.add_argument("--control", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "audit":
        elves = inspect_bundle(args.bundle)
        print(f"Validated {len(elves)} ELF files: {args.bundle}")
    else:
        dependencies = debian_dependencies(args.bundle.resolve())
        text, count = re.subn(
            r"(?m)^Depends:.*$", lambda _: "Depends: " + dependencies,
            args.control.read_text(),
        )
        if count != 1:
            raise ValueError(f"Expected exactly one Depends field in {args.control}")
        args.control.write_text(text)
        print("Depends: " + dependencies)


if __name__ == "__main__":
    main()
