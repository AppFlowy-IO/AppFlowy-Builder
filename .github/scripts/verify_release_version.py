#!/usr/bin/env python3
"""Reject release builds whose source version differs from the requested version."""

import argparse
import json
from pathlib import Path
import re
import sys
import tomllib


def verify_release_version(source: Path, expected: str) -> dict[str, str]:
    frontend = source / "frontend"
    pubspec = frontend / "appflowy_flutter/pubspec.yaml"
    matches = re.findall(
        r"^version:[ \t]*([^ \t#\r\n]+)[ \t]*(?:#.*)?$",
        pubspec.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one version field in {pubspec}")

    makefile = tomllib.loads((frontend / "Makefile.toml").read_text(encoding="utf-8"))
    compatibility = json.loads(
        (frontend / "rust-lib/flowy-user/resources/desktop_server_compatibility.json")
        .read_text(encoding="utf-8")
    )
    # Keep the fields aligned with Premium's just upgrade-version command.
    versions = {
        "appflowy_flutter/pubspec.yaml: version": matches[0],
        "Makefile.toml: APPFLOWY_VERSION": makefile["env"]["APPFLOWY_VERSION"],
        "desktop_server_compatibility.json: reviewed_through_client_version":
            compatibility["reviewed_through_client_version"],
    }
    mismatches = [
        f"  {field} = {actual!r}"
        for field, actual in versions.items()
        if actual != expected
    ]
    if mismatches:
        raise ValueError(
            f"Requested release version {expected!r} does not match the source:\n"
            + "\n".join(mismatches)
            + "\nBuild a commit containing the requested version bump."
        )
    return versions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        versions = verify_release_version(args.source, args.version)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Release version verification failed: {error}", file=sys.stderr)
        return 1
    for field, version in versions.items():
        print(f"Verified {field} = {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
