#!/usr/bin/env python3
"""Record tested feed bytes before publication and verify their public delivery afterwards."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from generate_native_appcast import SPARKLE

S = "{" + SPARKLE + "}"
NATIVE = (("macos", "arm64"), ("macos", "x86_64"), ("windows", "x86_64"))
RUNNERS = {("macos", "arm64"): "macos-15", ("macos", "x86_64"): "macos-15-intel",
           ("windows", "x86_64"): "windows-2025"}
API = "https://beta.appflowy.cloud/api/desktop-update"
MAX_FEED = 256 * 1024  # the released Flutter client's limit
STABLE = re.compile(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


def gh(*arguments):
    return subprocess.check_output(["gh", *map(str, arguments)])


def public_bytes(url):
    # Public delivery must work for an installed client without GitHub credentials.
    with urlopen(Request(url, headers={"User-Agent": "AppFlowy-update-check"}), timeout=15) as response:
        if response.status != 200:
            raise ValueError(f"Unexpected HTTP {response.status} at {url}")
        body = response.read(MAX_FEED + 1)
    if not body or len(body) > MAX_FEED:
        raise ValueError(f"Empty or oversized response at {url}")
    return body


def selected(platform, generation):
    targets = []
    for system, arch in (*NATIVE, ("linux", "x86_64")):
        if platform not in ("all platforms", system):
            continue
        for kind in ("legacy", "native"):
            if generation not in ("both", kind) or (kind == "native" and system == "linux"):
                continue
            prefix = "native-" if kind == "native" else ""
            targets.append((f"{prefix}appcast-{system}-{arch}.xml", system, arch))
    if not targets:
        raise ValueError(f"{platform} has no {generation} update feed")
    return targets


def previous_tag(tags, target):
    def version(match):
        return tuple(map(int, match.groups()))
    limit = version(STABLE.fullmatch(target))
    prior = [(version(m), tag) for tag in tags if (m := STABLE.fullmatch(tag)) and version(m) < limit]
    if not prior:
        raise ValueError(f"No previous published stable release for {target}")
    return max(prior)[1]


def release_asset(release, name):
    matches = [a for a in release["assets"] if a["name"] == name and a["state"] == "uploaded"]
    if len(matches) != 1 or matches[0]["size"] <= 0:
        raise ValueError(f"Missing or incomplete {name} on {release['tag_name']}")
    return matches[0]


def installer_name(version, system, arch):
    package = "universal" if (system, arch) == ("macos", "arm64") else arch
    extension = "dmg" if system == "macos" else "exe"
    return f"AppFlowy-{version}-{system}-{package}.{extension}"


def inspect_feed(payload, release, repo, system, arch):
    if len(payload) > MAX_FEED:
        raise ValueError("Appcast exceeds the client size limit")
    tag = release["tag_name"]
    version = tag.removeprefix("v")
    root = ET.fromstring(payload)
    items = root.findall(".//item")
    if root.tag != "rss" or len(items) != 1:
        raise ValueError("Expected a single RSS release item")
    item = items[0]
    enclosures = item.findall("enclosure")
    if (item.findtext(S + "shortVersionString") != version or not item.findtext(S + "version")
            or len(enclosures) != 1 or enclosures[0].get(S + "os") != system):
        raise ValueError(f"{system}/{arch} appcast does not advertise {version}")
    if system == "linux":
        return None  # Legacy Linux clients use a notification-only feed.
    name = installer_name(version, system, arch)
    asset = release_asset(release, name)
    url = f"https://github.com/{repo}/releases/download/{tag}/{name}"
    enclosure = enclosures[0]
    signature = "edSignature" if system == "macos" else "dsaSignature"
    if (asset["browser_download_url"] != url or enclosure.get("url") != url
            or enclosure.get("length") != str(asset["size"]) or not enclosure.get(S + signature)):
        raise ValueError(f"{system}/{arch} enclosure does not match the released signed installer {name}")
    return url


def verify_discovery(response, tag, feed_url, download_url):
    data = response.get("data") or {}
    if (response.get("code") != 0 or data.get("auto_update_enabled") is not True
            or data.get("latest_version") != tag.removeprefix("v")
            or data.get("appcast_url") != feed_url or data.get("download_url") != download_url):
        raise ValueError(f"Client update API did not authorize {feed_url}: {response}")


def retry(check, seconds):
    deadline = time.monotonic() + seconds
    while True:
        try:
            return check()
        except (ValueError, ET.ParseError, URLError, TimeoutError) as error:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            print(f"{error}; retrying public read", flush=True)
            time.sleep(min(20, remaining))


def resolve(args):
    location = f"repos/{args.repository}/releases"
    selected(args.platform, args.generation)
    tag = args.tag or json.loads(gh("api", f"{location}/latest"))["tag_name"]
    if not STABLE.fullmatch(tag):
        raise ValueError(f"Expected a stable release tag, got {tag!r}")
    release = json.loads(gh("api", f"{location}/tags/{tag}"))
    if (release["tag_name"] != tag or release["prerelease"]
            or (release["draft"] and os.getenv("ALLOW_DRAFT") != "true")):
        raise ValueError("A stable release is required (drafts require testOnly)")
    if args.version and args.version.removeprefix("v") != tag.removeprefix("v"):
        raise ValueError("Advertised version must match the signed release")
    source = ""
    if args.platform != "linux":
        tags = gh("api", "--paginate", f"{location}?per_page=100", "--jq",
                  ".[] | select(.draft == false and .prerelease == false) | .tag_name")
        source = previous_tag(tags.decode().splitlines(), tag)
    matrix = {"include": [
        {"os": system, "arch": arch, "runner": RUNNERS[system, arch],
         "package_arch": "universal" if (system, arch) == ("macos", "arm64") else arch}
        for system, arch in NATIVE if args.platform in ("all platforms", system)
    ]}
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        print(f"tag={tag}\nrelease_id={release['id']}\nsource={source}", file=output)
        print(f"matrix={json.dumps(matrix)}", file=output)
    print(f"Staging {args.repository} release {release['id']} ({tag}), previous client {source or 'n/a'}")


def read_release(repo, tag, release_id):
    release = json.loads(gh("api", f"repos/{repo}/releases/tags/{tag}"))
    if (str(release["id"]) != str(release_id) or release["tag_name"] != tag or release["prerelease"]):
        raise ValueError("Resolved release identity changed")
    return release


def asset_digest(repo, asset):
    if asset.get("digest"):
        return asset["digest"]
    contents = gh("api", "-H", "Accept: application/octet-stream", f"repos/{repo}/releases/assets/{asset['id']}")
    return digest(contents)


def digest(payload):
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def check_tested_installer(evidence, release, repo, system, arch, source):
    version = release["tag_name"].removeprefix("v")
    name = installer_name(version, system, arch)
    asset = release_asset(release, name)
    directory = evidence / f"release-update-e2e-{system}-{arch}"
    suites = ["update-e2e", "update-e2e-legacy"] if system == "windows" else ["update-e2e"]
    expected = asset_digest(repo, asset)
    for suite in suites:
        report = json.loads((directory / suite / "reports/candidate/result.json").read_bytes())
        if (report["scenario"] != "candidate" or report["status"] != "passed" or report["platform"] != system
                or report["target_version"] != version or "sha256:" + report["installer_sha256"] != expected):
            raise ValueError(f"{name} differs from the installer that passed {suite}")
        if suite == "update-e2e" and (not source or report.get("source_version") != source.removeprefix("v")):
            raise ValueError(f"{name} has no passing upgrade from {source}")
    return expected


def stage(args):
    release = read_release(args.repository, args.tag, args.release_id)
    targets = selected(args.platform, args.generation)
    files = {p.name: p for p in args.feeds.iterdir() if p.is_file()}
    if set(files) != {name for name, _, _ in targets}:
        raise ValueError(f"Expected exactly {[t[0] for t in targets]}, received {sorted(files)}")
    hashes = {}
    for name, system, arch in targets:
        payload = files[name].read_bytes()
        inspect_feed(payload, release, args.repository, system, arch)
        hashes[name] = digest(payload)
    installers = {}
    for system, arch in sorted({(s, a) for _, s, a in targets if s != "linux"}):
        name = installer_name(args.tag.removeprefix("v"), system, arch)
        installers[name] = check_tested_installer(args.evidence, release, args.repository, system, arch, args.source)
        legacy = f"appcast-{system}-{arch}.xml"
        native = f"native-{legacy}"
        if legacy in hashes and native in hashes and hashes[legacy] != hashes[native]:
            raise ValueError(f"The two {system}/{arch} feeds differ from the payload exercised by the updater")
    receipt = dict(repository=args.repository, tag=args.tag, release_id=release["id"], source=args.source,
                   platform=args.platform, generation=args.generation, feeds=hashes, installers=installers,
                   run_id=os.environ["GITHUB_RUN_ID"], attempt=os.environ["GITHUB_RUN_ATTEMPT"])
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Validated {len(hashes)} staged feeds for {args.tag}.")
    return receipt


def verify_published(receipt):
    repo, tag = receipt["repository"], receipt["tag"]
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or not STABLE.fullmatch(tag):
        raise ValueError("Invalid repository or tag in staging receipt")
    release = read_release(repo, tag, receipt["release_id"])
    if release["draft"]:
        raise ValueError("The staged release is still a draft")
    latest = json.loads(gh("api", f"repos/{repo}/releases/latest"))
    if latest["id"] != release["id"]:
        raise ValueError(f"Clients now discover {latest['tag_name']}, not staged release {tag}")
    targets = selected(receipt["platform"], receipt["generation"])
    hashes = receipt["feeds"]
    if set(hashes) != {name for name, _, _ in targets}:
        raise ValueError("Staging receipt is missing selected feeds or contains extra files")
    for system, arch in {(s, a) for _, s, a in targets if s != "linux"}:
        name = installer_name(tag.removeprefix("v"), system, arch)
        if asset_digest(repo, release_asset(release, name)) != receipt["installers"].get(name):
            raise ValueError(f"Published installer differs from the staged upgrade: {name}")
    base = f"https://github.com/{repo}/releases"
    for name, system, arch in targets:
        expected = hashes[name]
        asset = release_asset(release, name)
        url = f"{base}/download/{tag}/{name}"
        if asset["browser_download_url"] != url or asset_digest(repo, asset) != expected:
            raise ValueError(f"Release asset differs from staged feed {name}")
        payload = public_bytes(url)
        if digest(payload) != expected or digest(public_bytes(f"{base}/latest/download/{name}")) != expected:
            raise ValueError(f"Public bytes differ for {name}")
        installer = inspect_feed(payload, release, repo, system, arch)
        if name.startswith("native-"):
            source = receipt["source"]
            if not STABLE.fullmatch(source) or previous_tag([source], tag) != source:
                raise ValueError("Missing earlier stable client version")
            query = urlencode(dict(os=system, arch="aarch64" if arch == "arm64" else arch,
                                   current_version=source.removeprefix("v")))
            response = json.loads(public_bytes(f"{API}?{query}"))
            verify_discovery(response, tag, url, installer)
        print(f"Verified {name} {expected}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    resolve_cmd = commands.add_parser("resolve")
    resolve_cmd.add_argument("--version", default="")
    stage_cmd = commands.add_parser("stage")
    for command in (resolve_cmd, stage_cmd):
        command.add_argument("--repository", required=True)
        command.add_argument("--tag", default="", required=command is stage_cmd)
        command.add_argument("--platform", choices=["all platforms", "macos", "windows", "linux"], required=True)
        command.add_argument("--generation", choices=["both", "native", "legacy"], required=True)
    stage_cmd.add_argument("--release-id", required=True)
    stage_cmd.add_argument("--source", required=True)
    stage_cmd.add_argument("--feeds", type=Path, default=Path("feeds"))
    stage_cmd.add_argument("--evidence", type=Path, default=Path("installer-evidence"))
    verify_cmd = commands.add_parser("verify-published")
    verify_cmd.add_argument("--stage-run-id", required=True)
    verify_cmd.add_argument("--attempt", required=True)
    verify_cmd.add_argument("--wait-seconds", type=int, default=1200)
    for command in (stage_cmd, verify_cmd):
        command.add_argument("--receipt", type=Path, default=Path("staged-desktop-update.json"))
    args = parser.parse_args()
    if args.command == "resolve":
        resolve(args)
    elif args.command == "stage":
        stage(args)
    else:
        receipt = json.loads(args.receipt.read_bytes())
        if (receipt["run_id"], receipt["attempt"]) != (args.stage_run_id, args.attempt):
            raise ValueError("Receipt belongs to a different staging run or attempt")
        # Release metadata is cached by the deployment for 15 minutes.
        retry(lambda: verify_published(receipt), args.wait_seconds)


if __name__ == "__main__":
    main()
