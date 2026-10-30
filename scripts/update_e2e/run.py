#!/usr/bin/env python3
"""Exercise native updater download, validation, installation and process restart.

The controller survives the old host's exit. Fixtures run locally or in CI;
production installers may execute only on disposable GitHub-hosted runners.
"""

import argparse
import base64
from contextlib import contextmanager
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ed25519
import psutil

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from verify_native_update import (  # noqa: E402
    public_key_for, validate_macos_bundle, validate_windows_bundle,
    verify_installer_signature,
)

SOURCE = Path(__file__).resolve().parent
SPARKLE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
ET.register_namespace("sparkle", SPARKLE)
PLATFORM = "macos" if sys.platform == "darwin" else "windows"


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def dependencies(root, winsparkle_version):
    if PLATFORM == "macos":
        url = "https://github.com/sparkle-project/Sparkle/releases/download/2.8.1/Sparkle-2.8.1.tar.xz"
        digest = "5cddb7695674ef7704268f38eccaee80e3accbf19e61c1689efff5b6116d85be"
    else:
        url = f"https://github.com/vslavik/winsparkle/releases/download/v{winsparkle_version}/WinSparkle-{winsparkle_version}.zip"
        digest = {
            # 0.8.1's x64 DLL exactly matches the one shipped in AppFlowy 0.14.7.
            "0.8.1": "491612b0e817373ffcde03ad0d923f1e1885a231316e9e9c257e57b4e9dab4c5",
            "0.9.4": "6037df37fc263bd1650a1c4949681a9d40ffe991d01f35892a406cb5d103c976",
        }[winsparkle_version]
    archive = root / url.rsplit("/", 1)[1]
    request = urllib.request.Request(url, headers={"User-Agent": "AppFlowy-update-e2e"})
    with urllib.request.urlopen(request, timeout=120) as response, archive.open("wb") as dest:
        shutil.copyfileobj(response, dest)
    if sha256(archive) != digest:
        raise ValueError("Native updater dependency checksum mismatch")
    destination = root / "dependencies"
    destination.mkdir()
    if PLATFORM == "macos":
        run("tar", "-xf", archive, "-C", destination)
        return next(destination.rglob("Sparkle.framework")).parent
    with zipfile.ZipFile(archive) as package:
        package.extractall(destination)
    return next(destination.rglob("winsparkle.h")).parent.parent


def public_key(private):
    key = private.public_key()
    if PLATFORM == "macos":
        return base64.b64encode(key.public_bytes_raw())
    return key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def sign(private, path):
    data = path.read_bytes()
    signature = private.sign(data) if PLATFORM == "macos" else private.sign(hashlib.sha1(data).digest(), hashes.SHA1())
    return base64.b64encode(signature).decode()


def build_mac(destination, deps, version, key, identifier, feed):
    bundle = destination / "AppFlowy.app"
    contents = bundle / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Frameworks").mkdir()
    run("ditto", deps / "Sparkle.framework", contents / "Frameworks/Sparkle.framework")
    info = {
        "CFBundleExecutable": "AppFlowy", "CFBundleName": "AppFlowy Update E2E",
        "CFBundleIdentifier": identifier, "CFBundlePackageType": "APPL",
        "CFBundleVersion": version, "CFBundleShortVersionString": version,
        "LSMinimumSystemVersion": "12.0", "SUFeedURL": feed,
        "SUEnableAutomaticChecks": False, "SUAutomaticallyUpdate": False,
        "SUAllowsAutomaticUpdates": False, "SUEnableInstallerLauncherService": True,
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    }
    if key is not None:
        info["SUPublicEDKey"] = key.decode().strip()
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    run("xcrun", "clang", "-fobjc-arc", "-Wno-incompatible-pointer-types",
        f'-DFIXTURE_VERSION="{version}"',
        "-mmacosx-version-min=12.0", "-framework", "Cocoa", "-framework", "Sparkle",
        "-F", deps, "-Wl,-rpath,@executable_path/../Frameworks",
        SOURCE / "macos.m", "-o", contents / "MacOS/AppFlowy")
    # These are disposable fixtures, not distributable release signatures.
    run("codesign", "--force", "--deep", "--sign", "-", bundle)
    run("codesign", "--verify", "--deep", "--strict", bundle)
    return bundle


def build_windows(destination, deps, version, key):
    destination.mkdir(parents=True)
    (destination / "public.pem").write_bytes(key)
    (destination / "key.rc").write_text('DSAPUB DSAPEM "public.pem"\n')
    run("rc", "/nologo", "/fo", "key.res", "key.rc", cwd=destination)
    run("cl", "/nologo", "/std:c++17", "/EHsc", "/MT", "/DUNICODE", "/D_UNICODE",
        f'/DFIXTURE_VERSION="{version}"', "/I", deps / "include", SOURCE / "windows.cpp",
        "key.res", deps / "x64/Release/WinSparkle.lib", "user32.lib",
        "/Fe:AppFlowy.exe", "/link", "/SUBSYSTEM:WINDOWS", cwd=destination)
    shutil.copy2(deps / "x64/Release/WinSparkle.dll", destination / "WinSparkle.dll")
    for name in ("public.pem", "key.rc", "key.res", "windows.obj"):
        (destination / name).unlink(missing_ok=True)
    return destination / "AppFlowy.exe"


def build_host(destination, deps, version, key, identifier, feed):
    if PLATFORM == "macos":
        return build_mac(destination, deps, version, key, identifier, feed)
    return build_windows(destination, deps, version, key)


def package_fixture(case, target, version):
    if PLATFORM == "macos":
        installer = case / "update.dmg"
        run("hdiutil", "create", "-quiet", "-format", "UDZO", "-volname", "Update E2E",
            "-srcfolder", target, installer)
        return installer
    iscc = shutil.which("iscc") or str(Path(os.environ["ProgramFiles(x86)"]) / "Inno Setup 6/ISCC.exe")
    run(iscc, "/Qp", f"/DCaseId={case.name}", f"/DVersion={version}",
        f"/DInstallDir={case / 'install'}", f"/DPayloadDir={target.parent}",
        f"/DOutputDir={case}", SOURCE / "fixture.iss")
    return case / "update.exe"


def feed_xml(case, base_url, installer, signature, version, build):
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = "AppFlowy isolated update test"
    item = ET.SubElement(channel, "item")
    ET.SubElement(item, f"{{{SPARKLE}}}version").text = build
    ET.SubElement(item, f"{{{SPARKLE}}}shortVersionString").text = version
    attributes = {
        "url": f"{base_url}/{installer.name}", "length": str(installer.stat().st_size),
        "type": "application/octet-stream",
        f"{{{SPARKLE}}}version": build,
        f"{{{SPARKLE}}}shortVersionString": version,
        f"{{{SPARKLE}}}{'edSignature' if PLATFORM == 'macos' else 'dsaSignature'}": signature,
    }
    if PLATFORM == "windows":
        attributes[f"{{{SPARKLE}}}installerArguments"] = (
            f'/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR="{case / "install"}" '
            f'/LOG="{case / "installer.log"}"'
        )
    ET.SubElement(item, "enclosure", attributes)
    (case / "appcast.xml").write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


@contextmanager
def server(case):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(case), **kwargs)

        def log_message(self, format, *args):
            with (case / "http.jsonl").open("a") as output:
                output.write(json.dumps({"path": self.path, "message": format % args}) + "\n")

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}"
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


def records(path):
    if not path.exists():
        return []
    # A writer can still be appending its last line while we poll.
    lines = path.read_text(errors="replace").splitlines(keepends=True)
    return [json.loads(line) for line in lines if line.endswith("\n")]


def executable(host):
    return host / "Contents/MacOS/AppFlowy" if PLATFORM == "macos" else host


def processes_under(root):
    result = []
    for process in psutil.process_iter(["exe"]):
        try:
            path = process.info["exe"]
            if path and Path(path).resolve().is_relative_to(root.resolve()):
                result.append(process)
        except (psutil.Error, OSError):
            continue
    return result


def wait_for(description, condition, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for {description}")


def assert_candidate(case, host, args):
    if PLATFORM == "macos":
        validate_macos_bundle(host, args.version)
        if not (host / "Contents/Frameworks/Sparkle.framework").is_dir():
            raise AssertionError("Installed candidate has no Sparkle framework for the following update")
        run("codesign", "--verify", "--strict", "--deep", host)
        run("spctl", "--assess", "--type", "execute", "--verbose=2", host)
    else:
        wait_for("Inno Setup completion", lambda: (case / "installer.log").exists()
                 and "Installation process succeeded" in (case / "installer.log").read_text(errors="replace"))
        validate_windows_bundle(host)
        if not (host.parent / "WinSparkle.dll").is_file():
            raise AssertionError("Installed candidate has no WinSparkle.dll for the following update")
        script = '(Get-Item -LiteralPath $env:E2E_INSTALLED_EXE).VersionInfo.ProductVersion'
        version = run("powershell", "-NoProfile", "-Command", script, capture_output=True,
                      text=True, env={**os.environ, "E2E_INSTALLED_EXE": str(host)}).stdout.strip()
        if version.removesuffix(".0") != args.version and version != args.version:
            raise AssertionError(f"Installed Windows version {version!r} differs from {args.version}")
    # Sparkle relaunches the app. AppFlowy's Windows Inno script has no [Run]
    # entry, so the controller explicitly launches the installed Windows app.
    if PLATFORM == "windows":
        subprocess.Popen([str(host)], cwd=host.parent)
    launched = wait_for("installed AppFlowy process", lambda: [
        p for p in processes_under(case / "install") if Path(p.exe()).resolve() == executable(host).resolve()
    ])
    pid = launched[0].pid
    time.sleep(15)
    if not any(p.pid == pid for p in processes_under(case / "install")):
        raise AssertionError("The installed AppFlowy process exited during its startup smoke test")
    return pid


def exercise(root, deps, scenario, args):
    case = root / scenario
    case.mkdir()
    candidate = args.installer is not None
    private = ed25519.Ed25519PrivateKey.generate() if PLATFORM == "macos" else dsa.generate_private_key(1024)
    key = public_key_for(PLATFORM) if candidate else public_key(private)
    identifier = "com.appflowy.appflowy.flutter" if candidate else f"io.appflowy.update-e2e.{uuid.uuid4().hex}"
    version = args.version if candidate else "2.0.0"
    marker = uuid.uuid4().hex
    (case / "data.txt").write_text(marker)
    (case / "target-version.txt").write_text(version)
    (case / "config.json").write_text(json.dumps({"version": version, "scenario": scenario}))
    summary = {"scenario": scenario, "platform": PLATFORM, "target_version": version,
               "engine": "Sparkle 2.8.1" if PLATFORM == "macos" else f"WinSparkle {args.winsparkle_version}"}
    try:
        with server(case) as base:
            feed = f"{base}/appcast.xml"
            (case / "feed-url.txt").write_text(feed)
            host = build_host(case / "install", deps, "0.0.1" if candidate else "1.0.0", key, identifier, feed)
            before = sha256(executable(host))
            if candidate:
                installer = case / ("update.dmg" if PLATFORM == "macos" else "update.exe")
                shutil.copy2(args.installer, installer)
                signature = args.signature_file.read_text().strip()
                verify_installer_signature(PLATFORM, installer, signature)
            else:
                target = build_host(case / "payload", deps, version,
                                    None if scenario == "missing-key" else key, identifier, feed)
                installer = package_fixture(case, target, version)
                signature = sign(private, installer)
            summary["installer_sha256"] = sha256(installer)
            if scenario == "bad-signature":
                wrong = ed25519.Ed25519PrivateKey.generate() if PLATFORM == "macos" else dsa.generate_private_key(1024)
                signature = sign(wrong, installer)
            elif scenario == "single-hash":
                signature = base64.b64encode(private.sign(installer.read_bytes(), hashes.SHA1())).decode()
            feed_xml(case, base, installer, signature, version, args.build_version or version)
            with (case / "console.log").open("wb") as console:
                process = subprocess.Popen([str(executable(host))], stdout=console, stderr=subprocess.STDOUT)
                expected = "skipped" if scenario == "skip" else "error" if scenario in ("bad-signature", "missing-key", "single-hash") else "terminated" if candidate else "relaunched"

                def completed():
                    events = records(case / "events.jsonl")
                    if expected != "error" and any(e["event"] in ("error", "no-update") for e in events):
                        raise AssertionError(f"Unexpected updater failure: {events}")
                    return events if any(e["event"] == expected for e in events) else None

                events = wait_for(expected, completed)
                process.wait(timeout=30)
                if process.returncode != 0:
                    raise AssertionError(f"Old updater host exited {process.returncode}")
            requests = records(case / "http.jsonl")
            if not any(r["path"].startswith("/appcast.xml") for r in requests):
                raise AssertionError("The native updater never requested the test feed")
            if scenario == "skip":
                if any(r["path"] == f"/{installer.name}" for r in requests):
                    raise AssertionError("Skipping the update downloaded its installer")
            elif not any(r["path"] == f"/{installer.name}" for r in requests):
                raise AssertionError("The native updater never downloaded the installer")
            if expected in ("error", "skipped"):
                if sha256(executable(host)) != before or any(e["event"] == "installing" for e in events):
                    raise AssertionError("Rejected update changed the installed executable")
                if expected == "error" and PLATFORM == "macos":
                    codes = {error["code"] for e in events for error in e.get("errors", []) if error["domain"] == "SUSparkleErrorDomain"}
                    if not codes.intersection({3001, 3002}):
                        raise AssertionError(f"Expected signature/validation rejection, got {events}")
            elif candidate:
                wait_for("installed candidate replacing the old host", lambda: executable(host).exists() and sha256(executable(host)) != before)
                summary["installed_pid"] = assert_candidate(case, host, args)
            else:
                if sha256(executable(host)) != sha256(executable(target)):
                    raise AssertionError("Installed executable differs from the target payload")
                old_pid = next(e["pid"] for e in events if e["event"] == "launched")
                new_pid = next(e["pid"] for e in events if e["event"] == "relaunched")
                if old_pid == new_pid or not any(e["event"] == "installing" for e in events):
                    raise AssertionError("No verified installation and process replacement occurred")
                data = next(e["data"] for e in events if e["event"] == "relaunched") if PLATFORM == "macos" else (case / "relaunch-data.txt").read_text()
                if data != marker:
                    raise AssertionError("Relaunched application could not read pre-update test data")
            if (case / "data.txt").read_text() != marker:
                raise AssertionError("Update changed the external data sentinel")
        summary["status"] = "passed"
    except Exception:
        summary.update(status="failed", error=traceback.format_exc())
        print(summary["error"], flush=True)
    finally:
        for process in processes_under(case):
            try:
                process.terminate()
                process.wait(timeout=5)
            except psutil.TimeoutExpired:
                process.kill()
            except psutil.Error:
                pass
        report = root.parent / "reports" / scenario
        report.mkdir(parents=True)
        for name in ("events.jsonl", "http.jsonl", "console.log", "appcast.xml", "installer.log"):
            if (case / name).exists():
                shutil.copy2(case / name, report / name)
        (report / "result.json").write_text(json.dumps(summary, indent=2))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New, empty directory for this run")
    parser.add_argument("--scenario", choices=("success", "bad-signature", "missing-key", "skip", "single-hash"))
    parser.add_argument("--installer", type=Path, help="Final signed release installer (CI only)")
    parser.add_argument("--signature-file", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--build-version")
    parser.add_argument("--winsparkle-version", choices=("0.8.1", "0.9.4"), default="0.9.4")
    args = parser.parse_args()
    if sys.platform not in ("darwin", "win32"):
        parser.error("Run on macOS or Windows")
    if args.installer:
        if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
            parser.error("Production installer tests require a disposable GitHub-hosted runner")
        if not args.signature_file or not args.version or args.scenario:
            parser.error("--installer requires --signature-file and --version, without --scenario")
    elif args.signature_file or args.version or args.build_version:
        parser.error("Release metadata requires --installer")
    if args.scenario in ("missing-key", "skip") and PLATFORM != "macos":
        parser.error("This scenario is macOS-only")
    if args.scenario == "single-hash" and PLATFORM != "windows":
        parser.error("This scenario is Windows-only")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    work = output / "work"
    work.mkdir()
    deps = dependencies(work, args.winsparkle_version)
    scenarios = ["candidate"] if args.installer else [args.scenario] if args.scenario else ["success", "bad-signature"] + (["missing-key", "skip"] if PLATFORM == "macos" else ["single-hash"])
    results = []
    for scenario in scenarios:
        print(f"Running {PLATFORM}: {scenario}", flush=True)
        results.append(exercise(work, deps, scenario, args))
        print(f"{scenario}: {results[-1]['status']}", flush=True)
    (output / "reports/results.json").write_text(json.dumps(results, indent=2))
    return 0 if all(result["status"] == "passed" for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
