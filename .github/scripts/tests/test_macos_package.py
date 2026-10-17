#!/usr/bin/env python3
"""Exercise macOS verification, thinning, and ZIP steps with real Mach-O files."""

import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

import yaml


ROOT = Path(__file__).resolve().parents[3]
THIN = ROOT / ".github/scripts/thin_app.sh"
ARCHITECTURES = ("universal", "arm64", "x86_64")


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def architectures(binary):
    return set(run("xcrun", "lipo", str(binary), "-archs").stdout.split())


def main():
    require(sys.platform == "darwin", "Run this check on macOS with Xcode tools installed")
    workflow = yaml.safe_load((ROOT / ".github/workflows/macos.yaml").read_text())
    steps = {step["name"]: step for step in workflow["jobs"]["universal"]["steps"]}

    with tempfile.TemporaryDirectory(prefix="appflowy-macos-package-") as directory:
        root = Path(directory)
        release = root / "Release with spaces"
        app = release / "AppFlowy.app"
        executable = Path("Contents/MacOS/AppFlowy")
        library = Path("Contents/Frameworks/Fixture.framework/Versions/A/Fixture")
        framework_link = Path("Contents/Frameworks/Fixture.framework/Fixture")
        resource = Path("Contents/Resources/read me.txt")
        for relative in (executable, library, resource):
            (app / relative).parent.mkdir(parents=True, exist_ok=True)

        source = root / "fixture.c"
        source.write_text("int main(void) { return 0; }\n")
        run("xcrun", "clang", "-arch", "arm64", "-arch", "x86_64",
            str(source), "-o", str(app / executable))
        run("xcrun", "clang", "-dynamiclib", "-arch", "arm64", "-arch", "x86_64",
            str(source), "-o", str(app / library))
        # The deleted helper missed extensionless framework binaries without +x.
        (app / library).chmod(0o644)
        (app / framework_link.parent / "Versions/Current").symlink_to("A")
        (app / framework_link).symlink_to("Versions/Current/Fixture")
        (app / resource).write_text("Keep non-binary resources unchanged.\n")
        original_binary = (app / executable).read_bytes()
        original_library = (app / library).read_bytes()

        tools = root / ".builder/.github/scripts"
        tools.mkdir(parents=True)
        shutil.copy2(THIN, tools / THIN.name)
        environment = {**os.environ, "MACOS_APP_RELEASE_PATH": str(release),
                       "MACOS_ASSET_PREFIX": "AppFlowy-0.14.9-macos"}
        verification = steps["Verify universal app"]["run"]
        run("bash", "-e", "-o", "pipefail", "-c", verification, cwd=root, env=environment)
        run("bash", "-e", "-o", "pipefail", "-c",
            steps["Prepare architecture-specific apps"]["run"], cwd=root, env=environment)
        run("bash", "-e", "-o", "pipefail", "-c",
            steps["Archive macOS apps"]["run"], cwd=release, env=environment)

        for arch in ARCHITECTURES:
            package_app = release / arch / "AppFlowy.app"
            expected = {"arm64", "x86_64"} if arch == "universal" else {arch}
            for relative in (executable, library, framework_link):
                require(architectures(package_app / relative) == expected,
                        f"Incorrect {arch} architecture: {relative}")
            require(stat.S_IMODE((package_app / library).stat().st_mode) == 0o644,
                    f"{arch}: framework mode changed")
            require((package_app / executable).stat().st_mode & stat.S_IXUSR,
                    f"{arch}: main executable lost its executable bit")
            require((package_app / framework_link).is_symlink(), f"{arch}: symlink was lost")
            require((package_app / resource).read_text() == "Keep non-binary resources unchanged.\n",
                    f"{arch}: resource changed")
            with zipfile.ZipFile(release / f"AppFlowy-0.14.9-macos-{arch}.zip") as archive:
                require(all(name.startswith("AppFlowy.app/") for name in archive.namelist()),
                        f"{arch}: ZIP must install as AppFlowy.app")
                link = archive.getinfo(f"AppFlowy.app/{framework_link}")
                require(stat.S_ISLNK(link.external_attr >> 16), f"{arch}: ZIP lost its symlink")
                require(archive.read(link) == b"Versions/Current/Fixture", "Wrong archived link")
                extracted = root / f"archived-{arch}"
                extracted.write_bytes(archive.read(f"AppFlowy.app/{executable}"))
                require(architectures(extracted) == expected, f"{arch}: wrong archived binary")
            if arch != "universal":
                result = subprocess.run(
                    ["bash", "-e", "-o", "pipefail", "-c", verification], cwd=root,
                    env={**environment, "MACOS_APP_RELEASE_PATH": str(release / arch)},
                    capture_output=True, text=True, check=False,
                )
                require(result.returncode != 0, f"Universal verification accepted a {arch}-only app")
        universal = release / "universal/AppFlowy.app"
        require((universal / executable).read_bytes() == original_binary,
                "Thinning modified the universal executable")
        require((universal / library).read_bytes() == original_library,
                "Thinning modified the universal framework")

        # A bundle with an Intel-only nested library must not pass as ARM64.
        broken = root / "broken.app"
        shutil.copytree(universal, broken, symlinks=True)
        shutil.copy2(release / "x86_64/AppFlowy.app" / library, broken / library)
        result = subprocess.run(
            ["bash", str(THIN), str(broken), "arm64", str(root / "rejected.app")],
            capture_output=True, text=True, check=False,
        )
        require(result.returncode != 0 and "Missing arm64 slice" in result.stderr,
                "Thinning accepted an incompatible nested binary")

        # Already-thin compatible binaries are valid; existing outputs are protected.
        run("bash", str(THIN), str(release / "arm64/AppFlowy.app"), "arm64",
            str(root / "already-thin.app"))
        result = subprocess.run(
            ["bash", str(THIN), str(universal), "arm64", str(universal)],
            capture_output=True, text=True, check=False,
        )
        require(result.returncode != 0 and "Output already exists" in result.stderr,
                "Thinning overwrote an existing app")

    print("PASS macOS packages: universal verification, real ARM64/Intel Mach-O files, framework symlinks,")
    print("     permissions, ZIP contents, unchanged source, and incompatible-slice rejection")


if __name__ == "__main__":
    main()
