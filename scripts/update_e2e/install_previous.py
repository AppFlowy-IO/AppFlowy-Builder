#!/usr/bin/env python3
"""Install an authentic previous AppFlowy build in a disposable Actions desktop."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_update_delivery import asset_digest, gh, release_asset
from verify_native_update import validate_macos_bundle


def checked(*args):
    subprocess.run(list(map(str, args)), check=True)


def main():
    if os.getenv("GITHUB_ACTIONS") != "true" or os.getenv("RUNNER_ENVIRONMENT") != "github-hosted":
        raise SystemExit("Requires a disposable GitHub-hosted desktop")
    tag, repo, arch = (os.environ[k] for k in ("SOURCE_TAG", "REPOSITORY", "ARCH"))
    version = tag.removeprefix("v")
    release = json.loads(gh("api", f"repos/{repo}/releases/tags/{tag}"))
    if release["tag_name"] != tag or release["draft"] or release["prerelease"]:
        raise ValueError("Previous version must be a published stable release")
    mac = sys.platform == "darwin"
    if not mac and sys.platform != "win32":
        raise ValueError("Requires macOS or Windows")
    if platform.machine().lower().replace("amd64", "x86_64") != arch:
        raise ValueError(f"Expected {arch} runner, got {platform.machine()}")
    system, ext = ("macos", "dmg") if mac else ("windows", "exe")
    name = f"AppFlowy-{version}-{system}-{arch}.{ext}"
    if mac and not any(a["name"] == name for a in release["assets"]):
        name = f"AppFlowy-{version}-macos-universal.dmg"
    asset = release_asset(release, name)
    evidence = Path("evidence")
    evidence.mkdir()
    record = {"source": tag, "release_id": release["id"], "asset": name, "target": os.environ["TARGET_TAG"]}
    (evidence / "previous-release.json").write_text(json.dumps(record, indent=2))
    with tempfile.TemporaryDirectory(dir=os.environ["RUNNER_TEMP"]) as directory:
        root = Path(directory)
        package = root / name
        checked("gh", "release", "download", tag, "--repo", repo, "--pattern", name, "--output", package)
        with package.open("rb") as data:
            checksum = "sha256:" + hashlib.file_digest(data, "sha256").hexdigest()
        if checksum != asset_digest(repo, asset) or package.stat().st_size != asset["size"]:
            raise ValueError(f"Previous installer does not match GitHub's release asset {asset['id']}")
        if mac:
            app = Path("/Applications/AppFlowy.app")
            if app.exists():
                raise FileExistsError(app)
            mount = root / "mount"
            mount.mkdir()
            checked("hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen", "-mountpoint", mount, package)
            try:
                checked("ditto", mount / app.name, app)
            finally:
                checked("hdiutil", "detach", mount)
            validate_macos_bundle(app, version)
            checked("codesign", "--verify", "--strict", "--deep", app)
            checked("spctl", "--assess", "--type", "execute", app)
        else:
            powershell = ["pwsh", "-NoProfile", "-Command"]
            os.environ["SOURCE_INSTALLER"] = str(package)
            checked(*powershell, "if ((Get-AuthenticodeSignature -LiteralPath $env:SOURCE_INSTALLER).Status -ne 'Valid') { exit 1 }")
            # The production Inno setup uses its default directory on every upgrade.
            # A custom /DIR for the old version would test two separate installs.
            candidates = [Path(os.environ[k]) / "AppFlowy/AppFlowy.exe" for k in ("ProgramFiles", "ProgramFiles(x86)")]
            if any(p.exists() for p in candidates):
                raise ValueError("AppFlowy was already installed on this runner")
            checked(package, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-")
            app, = (p for p in candidates if p.is_file())
            os.environ["APP_PATH"] = str(app)
            os.environ["SOURCE_VERSION"] = version
            checked(*powershell, "if ([version](Get-Item -LiteralPath $env:APP_PATH).VersionInfo.ProductVersion -notin "
                    "([version]$env:SOURCE_VERSION, [version]($env:SOURCE_VERSION+'.0'))) { exit 1 }")
    (evidence / "previous-release.json").write_text(json.dumps({**record, "sha256": checksum, "path": str(app)}, indent=2))
    with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as env:
        print(f"APP_PATH={app}", file=env)


if __name__ == "__main__":
    main()
