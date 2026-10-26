#!/usr/bin/env python3
"""Generate one version-pinned appcast from the final signed installer metadata."""
import argparse
import base64
import datetime
import pathlib
import re
import xml.etree.ElementTree as ET

SPARKLE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
ET.register_namespace("sparkle", SPARKLE)


def generate(tag, platform, arch, installer, signature, build_version, repository="AppFlowy-IO/AppFlowy", minimum_server_version=None, critical=False, package_arch=None):
    version = tag.removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("A stable release tag is required")
    if platform not in ("macos", "windows") or arch not in ("arm64", "x86_64"):
        raise ValueError("Unsupported native platform")
    package_arch = package_arch or arch
    if package_arch != arch and not (platform == "macos" and package_arch == "universal"):
        raise ValueError("Installer architecture does not match feed")
    if not re.fullmatch(r"\d+(?:\.\d+)*", build_version):
        raise ValueError("Installer build version is required")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError("Invalid repository")
    signature = signature.strip()
    decoded = base64.b64decode(signature, validate=True)
    if not decoded or (platform == "macos" and len(decoded) != 64):
        raise ValueError("Missing or malformed installer signature")
    size = installer.stat().st_size
    if size <= 0:
        raise ValueError("Empty installer")
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = "AppFlowy"
    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = f"Version {version}"
    ET.SubElement(item, f"{{{SPARKLE}}}version").text = build_version
    ET.SubElement(item, f"{{{SPARKLE}}}shortVersionString").text = version
    ET.SubElement(item, "pubDate").text = datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    if minimum_server_version:
        if not re.fullmatch(r"\d+\.\d+\.\d+", minimum_server_version):
            raise ValueError("Invalid minimum server version")
        ET.SubElement(item, "minServerVersion").text = minimum_server_version
    if critical:
        ET.SubElement(item, f"{{{SPARKLE}}}criticalUpdate")
    extension = "dmg" if platform == "macos" else "exe"
    url = f"https://github.com/{repository}/releases/download/{tag}/AppFlowy-{version}-{platform}-{package_arch}.{extension}"
    ET.SubElement(item, "enclosure", {
        "url": url, "type": "application/octet-stream", "length": str(size),
        f"{{{SPARKLE}}}os": platform,
        f"{{{SPARKLE}}}{'edSignature' if platform == 'macos' else 'dsaSignature'}": signature,
    })
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tag", "platform", "arch", "installer", "signature-file", "build-version", "output"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--repository", default="AppFlowy-IO/AppFlowy")
    parser.add_argument("--minimum-server-version")
    parser.add_argument("--package-arch")
    parser.add_argument("--critical", action="store_true")
    args = parser.parse_args()
    pathlib.Path(args.output).write_bytes(generate(args.tag, args.platform, args.arch, pathlib.Path(args.installer), pathlib.Path(args.signature_file).read_text(), args.build_version, args.repository, args.minimum_server_version, args.critical, args.package_arch))
