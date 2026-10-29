#!/usr/bin/env python3
"""Validate update compatibility before signing or advertising release installers."""

import argparse
import base64
import binascii
import hashlib
import json
from pathlib import Path
import plistlib
import re


KEYS = Path(__file__).resolve().parent / "update_keys"


def public_key_for(platform):
    filenames = {"macos": "macos-ed25519.pub", "windows": "windows-dsa.pem"}
    return (KEYS / filenames[platform]).read_bytes()


def ed25519_key(value):
    try:
        key = base64.b64decode(value.strip(), validate=True)
    except (AttributeError, TypeError, ValueError, binascii.Error) as error:
        raise ValueError("Missing or malformed SUPublicEDKey") from error
    if len(key) != 32:
        raise ValueError("SUPublicEDKey must contain a 32-byte Ed25519 public key")
    return key


def validate_macos_bundle(bundle, version, public_key=None):
    """Sparkle rejects removing its key even when archive/code signatures pass.

    The pinned key is the one shipped to existing clients. Changing it requires
    a reviewed key-rotation rollout, rather than trusting whatever CI just built.
    """
    with (bundle / "Contents/Info.plist").open("rb") as source:
        info = plistlib.load(source)
    actual_key = ed25519_key(info.get("SUPublicEDKey"))
    expected_key = ed25519_key(public_key if public_key is not None else public_key_for("macos"))
    if actual_key != expected_key:
        raise ValueError("SUPublicEDKey differs from the deployed update key; review key rotation before publishing")
    if info.get("CFBundleIdentifier") != "com.appflowy.appflowy.flutter":
        raise ValueError("Unexpected macOS application identifier")
    if info.get("CFBundleShortVersionString") != version:
        raise ValueError("macOS application version does not match the release")
    build = info.get("CFBundleVersion", "")
    if not isinstance(build, str) or not re.fullmatch(r"\d+(?:\.\d+)*", build):
        raise ValueError("macOS application has no valid build version")
    return build


def validate_windows_bundle(executable):
    """Read the compiled WinSparkle key without running the application."""
    import ctypes
    from ctypes import wintypes
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import dsa

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    kernel.LoadLibraryExW.restype = wintypes.HMODULE
    kernel.FindResourceW.argtypes = [wintypes.HMODULE, wintypes.LPCWSTR, wintypes.LPCWSTR]
    kernel.FindResourceW.restype = wintypes.HANDLE
    kernel.LoadResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
    kernel.LoadResource.restype = wintypes.HANDLE
    kernel.LockResource.argtypes = [wintypes.HANDLE]
    kernel.LockResource.restype = ctypes.c_void_p
    kernel.SizeofResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
    kernel.SizeofResource.restype = wintypes.DWORD
    kernel.FreeLibrary.argtypes = [wintypes.HMODULE]
    kernel.FreeLibrary.restype = wintypes.BOOL
    # LOAD_LIBRARY_AS_DATAFILE | LOAD_LIBRARY_AS_IMAGE_RESOURCE: no code executes.
    module = kernel.LoadLibraryExW(str(executable.resolve()), None, 0x02 | 0x20)
    if not module:
        raise OSError("Cannot read Windows executable resources")
    try:
        resource = kernel.FindResourceW(module, "DSAPUB", "DSAPEM")
        if not resource:
            raise ValueError("Windows application is missing the WinSparkle DSAPUB/DSAPEM resource")
        size = kernel.SizeofResource(module, resource)
        pointer = kernel.LockResource(kernel.LoadResource(module, resource))
        if not pointer or not 0 < size < 16384:
            raise ValueError("Invalid Windows update public key resource")
        actual = serialization.load_pem_public_key(ctypes.string_at(pointer, size))
        expected = serialization.load_pem_public_key(public_key_for("windows"))
        if not isinstance(actual, dsa.DSAPublicKey) or actual.public_numbers() != expected.public_numbers():
            raise ValueError("Windows update public key differs from the deployed key")
    finally:
        kernel.FreeLibrary(module)


def verify_installer_signature(platform, installer, signature, public_key=None):
    # Bundle-only preflight uses the standard library, before the expensive build.
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import dsa, ed25519

    if platform not in ("macos", "windows"):
        raise ValueError("Unsupported update platform")
    try:
        decoded = base64.b64decode(signature.strip(), validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("Malformed installer signature") from error
    if not decoded:
        raise ValueError("Missing installer signature")
    payload = installer.read_bytes()
    if not payload:
        raise ValueError("Empty installer")
    key = public_key if public_key is not None else public_key_for(platform)
    try:
        if platform == "macos":
            ed25519.Ed25519PublicKey.from_public_bytes(ed25519_key(key)).verify(decoded, payload)
        else:
            verifier = serialization.load_pem_public_key(key)
            if not isinstance(verifier, dsa.DSAPublicKey):
                raise ValueError("Windows legacy updates require a DSA public key")
            # Deployed WinSparkle clients verify DSA/SHA1. Switching algorithms
            # needs a separate client migration; SHA256 here would break them.
            verifier.verify(decoded, payload, hashes.SHA1())
    except InvalidSignature as error:
        raise ValueError("Installer signature does not match the final bytes and deployed public key") from error


def verify_release_asset(release, name, installer):
    matches = [asset for asset in release["assets"] if asset["name"] == name]
    if len(matches) != 1:
        raise ValueError("Expected exactly one matching release asset")
    asset = matches[0]
    if not 0 < installer.stat().st_size == asset["size"]:
        raise ValueError("Installer size differs from the release asset")
    digest = asset.get("digest")
    if digest and digest != "sha256:" + hashlib.sha256(installer.read_bytes()).hexdigest():
        raise ValueError("Installer SHA256 differs from the release asset")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    bundle = commands.add_parser("macos-bundle")
    bundle.add_argument("--bundle", type=Path, required=True)
    bundle.add_argument("--version", required=True)
    windows = commands.add_parser("windows-bundle")
    windows.add_argument("--executable", type=Path, required=True)
    signature = commands.add_parser("signature")
    signature.add_argument("--platform", choices=("macos", "windows"), required=True)
    signature.add_argument("--installer", type=Path, required=True)
    signature.add_argument("--signature-file", type=Path, required=True)
    asset = commands.add_parser("release-asset")
    asset.add_argument("--release-json", type=Path, required=True)
    asset.add_argument("--asset-name", required=True)
    asset.add_argument("--installer", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "macos-bundle":
            print(validate_macos_bundle(args.bundle, args.version))
        elif args.command == "windows-bundle":
            validate_windows_bundle(args.executable)
            print("Compiled Windows update public key matches deployed clients")
        elif args.command == "signature":
            verify_installer_signature(args.platform, args.installer, args.signature_file.read_text(encoding="utf-8"))
            print("Installer signature verified against the deployed public key")
        else:
            verify_release_asset(json.loads(args.release_json.read_text(encoding="utf-8")), args.asset_name, args.installer)
            print("Installer matches the published release asset")
    except (ValueError, OSError, plistlib.InvalidFileException) as error:
        parser.exit(1, f"Update validation failed: {error}\n")


if __name__ == "__main__":
    main()
