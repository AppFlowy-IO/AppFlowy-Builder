#!/usr/bin/env python3
"""Run the workflow's dependency step against a real native media bundle.

Run on the release build distribution. Install the resulting Debian package
on a clean newer distribution; metadata-only assertions cannot prove that the
private codecs still resolve after installation.
"""

import argparse
import os
from pathlib import Path
import shutil
import subprocess

import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    builder = Path(__file__).resolve().parents[3]
    output = args.output.resolve()
    frontend = output / "frontend"
    version = "0.0.9010"
    release = frontend / "appflowy_flutter/product" / version / "linux/Release"
    if output.exists():
        raise SystemExit(f"Use a fresh fixture directory: {output}")
    release.mkdir(parents=True)
    shutil.copytree(args.bundle, release / "AppFlowy", symlinks=True)
    shutil.copytree(
        args.frontend / "scripts/linux_distribution",
        frontend / "scripts/linux_distribution",
    )
    shutil.copytree(builder / ".github/scripts", output / ".builder/.github/scripts")
    policy = args.frontend / "appflowy_flutter/linux/host_runtime_libraries.regex"
    if policy.exists():
        target = frontend / "appflowy_flutter/linux" / policy.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(policy, target)
    workflow = yaml.safe_load((builder / ".github/workflows/linux.yaml").read_text())
    step = next(
        step for step in workflow["jobs"]["build"]["steps"]
        if step.get("name") == "Compute .deb runtime dependencies"
    )
    script = step["run"].replace("${{ inputs.build_name }}", version)
    env = {**os.environ, "GITHUB_WORKSPACE": str(output), "BUILD_VERSION": version}
    subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=frontend, env=env, check=True)
    filename = "appflowy-packaging-fixture.deb"
    subprocess.run(
        ["bash", "scripts/linux_distribution/deb/build_deb.sh",
         str(release), version, filename],
        cwd=frontend, check=True,
    )
    architecture = subprocess.check_output(
        ["dpkg", "--print-architecture"], text=True,
    ).strip()
    packaged_architecture = subprocess.check_output(
        ["dpkg-deb", "-f", str(release / filename), "Architecture"], text=True,
    ).strip()
    if packaged_architecture != architecture:
        raise AssertionError(
            f"Debian metadata has {packaged_architecture}, expected {architecture}"
        )
    shutil.copyfile(release / filename, output / filename)
    print(output / filename)


if __name__ == "__main__":
    main()
