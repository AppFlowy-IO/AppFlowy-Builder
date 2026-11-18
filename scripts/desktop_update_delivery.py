#!/usr/bin/env python3
"""Publish only the tested feeds and verify the discovery paths used by released clients."""
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
    version = lambda m: tuple(map(int, m.groups()))
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
    latest = json.loads(gh("api", f"{location}/latest")) if not args.tag or args.mode != "stage" else None
    tag = args.tag or latest["tag_name"]
    if not STABLE.fullmatch(tag):
        raise ValueError(f"Expected a stable release tag, got {tag!r}")
    release = json.loads(gh("api", f"{location}/tags/{tag}"))
    if release["tag_name"] != tag or release["prerelease"] or (release["draft"] and args.mode != "stage"):
        raise ValueError("A published stable release is required for a rollout (use testOnly for drafts)")
    if args.version and args.version.removeprefix("v") != tag.removeprefix("v"):
        raise ValueError("Advertised version must match the signed release")
    source = ""
    if args.mode != "stage":
        if release["id"] != latest["id"]:
            raise ValueError("Installed clients discover the latest release. Use testOnly to stage historical feeds.")
        if any(name.startswith("native-") for name, _, _ in selected(args.platform, args.generation)):
            if args.repository.lower() != "appflowy-io/appflowy":
                raise ValueError("The released native client uses the official AppFlowy repository. Use testOnly for a different repository.")
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
    print(f"{args.mode}: {args.repository} release {release['id']} ({tag}), previous native client {source or 'n/a'}")


def assert_identity(args, release):
    if str(release["id"]) != args.release_id or release["tag_name"] != args.tag or release["prerelease"]:
        raise ValueError("Resolved release identity changed")
    if args.mode != "stage" and release["draft"]:
        raise ValueError("Cannot roll out a draft")


def asset_digest(repo, asset):
    if asset.get("digest"):
        return asset["digest"]
    contents = gh("api", "-H", "Accept: application/octet-stream", f"repos/{repo}/releases/assets/{asset['id']}")
    return digest(contents)


def digest(payload):
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def deliver(args):
    location = f"repos/{args.repository}/releases"
    def read_release():
        release = json.loads(gh("api", f"{location}/tags/{args.tag}"))
        assert_identity(args, release)
        if args.mode != "stage" and json.loads(gh("api", f"{location}/latest"))["id"] != release["id"]:
            raise ValueError("The target is no longer the release installed clients discover")
        return release

    release = read_release()
    targets = selected(args.platform, args.generation)
    staged = {}
    if args.mode != "verify":
        files = {p.name: p for p in args.feeds.iterdir() if p.is_file()}
        if set(files) != {name for name, _, _ in targets}:
            raise ValueError(f"Expected exactly {[t[0] for t in targets]}, received {sorted(files)}")
        for name, system, arch in targets:
            payload = files[name].read_bytes()
            inspect_feed(payload, release, args.repository, system, arch)
            staged[name] = digest(payload)
        if args.mode == "stage":
            print(f"Validated {len(staged)} staged feeds for {args.tag}.")
            return
        for name, _, _ in targets:
            existing = next((a for a in release["assets"] if a["name"] == name), None)
            if existing and existing["state"] == "uploaded" and asset_digest(args.repository, existing) == staged[name]:
                continue
            flags = ["--clobber"] if existing else []
            gh("release", "upload", args.tag, files[name], "--repo", args.repository, *flags)
        release = read_release()

    base = f"https://github.com/{args.repository}/releases"
    def verify_all():
        for name, system, arch in targets:
            asset = release_asset(release, name)
            url = f"{base}/download/{args.tag}/{name}"
            expected = staged.get(name) or asset_digest(args.repository, asset)
            if asset["browser_download_url"] != url or asset_digest(args.repository, asset) != expected:
                raise ValueError(f"Release asset differs from the verified feed: {name}")
            payload = public_bytes(url)
            if digest(payload) != expected or digest(public_bytes(f"{base}/latest/download/{name}")) != expected:
                raise ValueError(f"Public bytes differ for {name}")
            download = inspect_feed(payload, release, args.repository, system, arch)
            if name.startswith("native-"):
                query = urlencode(dict(os=system, arch="aarch64" if arch == "arm64" else arch,
                                       current_version=args.source.removeprefix("v")))
                response = json.loads(public_bytes(f"{API}?{query}"))
                verify_discovery(response, args.tag, url, download)
            print(f"Verified {name} {expected}", flush=True)
    # Discovery caches release metadata for 15 minutes. Never publish again while polling.
    retry(verify_all, args.wait_seconds)
    read_release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["resolve", "deliver"])
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", default="")
    parser.add_argument("--platform", choices=["all platforms", "macos", "windows", "linux"], required=True)
    parser.add_argument("--generation", choices=["both", "native", "legacy"], required=True)
    parser.add_argument("--mode", choices=["stage", "publish", "verify"], required=True)
    parser.add_argument("--release-id")
    parser.add_argument("--source", default="")
    parser.add_argument("--version", default="")
    parser.add_argument("--feeds", type=Path, default=Path("feeds"))
    parser.add_argument("--wait-seconds", type=int, default=1200)
    args = parser.parse_args()
    if args.command == "deliver" and (not args.release_id or not STABLE.fullmatch(args.tag)):
        parser.error("delivery requires the resolved release ID and tag")
    if (args.command == "deliver" and args.mode != "stage"
            and any(t[0].startswith("native-") for t in selected(args.platform, args.generation))
            and (not STABLE.fullmatch(args.source) or previous_tag([args.source], args.tag) != args.source)):
        parser.error("live native discovery requires an earlier stable source")
    (resolve if args.command == "resolve" else deliver)(args)


if __name__ == "__main__":
    main()
