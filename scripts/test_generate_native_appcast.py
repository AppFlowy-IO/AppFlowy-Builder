import base64
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ed25519

from generate_native_appcast import generate, write_feeds, SPARKLE


class AppcastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = {"macos": ed25519.Ed25519PrivateKey.generate(),
                    "windows": dsa.generate_private_key(key_size=2048)}

    def signed_installer(self, root, platform):
        installer = root / "installer"
        payload = b"final signed installer"
        installer.write_bytes(payload)
        key = self.keys[platform]
        if platform == "macos":
            signature = key.sign(payload)
            public = base64.b64encode(key.public_key().public_bytes_raw())
        else:
            signature = key.sign(payload, hashes.SHA1())
            public = key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        return installer, base64.b64encode(signature).decode(), public

    def test_rejects_a_well_formed_but_invalid_installer_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = Path(directory) / "app.dmg"
            installer.write_bytes(b"installer replaced after signing")
            with self.assertRaisesRegex(ValueError, "signature"):
                generate("0.14.11", "macos", "arm64", installer,
                         base64.b64encode(b"x" * 64).decode(), "0.14.11")

    def test_both_platforms_reject_bytes_changed_after_signing(self):
        for platform in self.keys:
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as directory:
                installer, signature, public = self.signed_installer(Path(directory), platform)
                installer.write_bytes(b"installer changed after signing")
                with self.assertRaisesRegex(ValueError, "signature"):
                    generate("0.14.12", platform, "x86_64", installer, signature,
                             "0.14.12", public_key=public)

    def test_both_platforms_reject_a_different_signing_key(self):
        for platform in self.keys:
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as directory:
                installer, signature, _ = self.signed_installer(Path(directory), platform)
                with self.assertRaisesRegex(ValueError, "signature"):
                    generate("0.14.12", platform, "x86_64", installer, signature, "0.14.12")

    def test_each_generation_preserves_legacy_names_and_signed_metadata(self):
        for platform, arch in [("macos", "arm64"), ("macos", "x86_64"), ("windows", "x86_64")]:
            for generation in ("legacy", "native", "both"):
                with self.subTest(platform=platform, arch=arch, generation=generation), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    installer, signature, public = self.signed_installer(root, platform)
                    payload = generate("0.14.12", platform, arch, installer,
                                       signature, "1793000000", minimum_server_version="0.19.10",
                                       critical=True, public_key=public)
                    files = write_feeds(root / "feeds", platform, arch, payload, generation)
                    expected = []
                    if generation != "native":
                        expected.append(f"appcast-{platform}-{arch}.xml")
                    if generation != "legacy":
                        expected.append(f"native-appcast-{platform}-{arch}.xml")
                    self.assertEqual([p.name for p in files], expected)
                    for path in files:
                        self.assertEqual(path.read_bytes(), payload)
                        item = ET.fromstring(path.read_bytes()).find("channel/item")
                        self.assertEqual(item.find(f"{{{SPARKLE}}}shortVersionString").text, "0.14.12")
                        self.assertEqual(item.find(f"{{{SPARKLE}}}criticalUpdate").text, "true")
                        self.assertEqual(item.find("minServerVersion").text, "0.19.10")

    def test_apple_silicon_feed_can_target_universal_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            installer, signature, public = self.signed_installer(Path(directory), "macos")
            root = ET.fromstring(generate("0.14.12", "macos", "arm64", installer,
                                        signature, "1793000000", package_arch="universal", public_key=public))
            self.assertTrue(root.find("channel/item/enclosure").get("url")
                            .endswith("/AppFlowy-0.14.12-macos-universal.dmg"))
            with self.assertRaises(ValueError):
                generate("0.14.12", "windows", "x86_64", installer,
                         signature, "0.14.12", package_arch="universal", public_key=public)

    def test_windows_feed_carries_version_signature_and_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            installer, signature, public = self.signed_installer(Path(directory), "windows")
            root = ET.fromstring(generate(
                "0.14.12", "windows", "x86_64", installer, signature, "0.14.12",
                minimum_server_version="0.19.10", critical=True, public_key=public))
            item = root.find("channel/item")
            self.assertEqual(item.find(f"{{{SPARKLE}}}version").text, "0.14.12")
            self.assertEqual(item.find("minServerVersion").text, "0.19.10")
            self.assertIsNotNone(item.find(f"{{{SPARKLE}}}criticalUpdate"))
            enclosure = item.find("enclosure")
            self.assertEqual(enclosure.get(f"{{{SPARKLE}}}os"), "windows")
            self.assertEqual(enclosure.get(f"{{{SPARKLE}}}dsaSignature"), signature)
            self.assertIsNone(enclosure.get(f"{{{SPARKLE}}}edSignature"))
            self.assertEqual(enclosure.get("url"),
                             "https://github.com/AppFlowy-IO/AppFlowy/releases/download/0.14.12/AppFlowy-0.14.12-windows-x86_64.exe")
            self.assertEqual(int(enclosure.get("length")), installer.stat().st_size)

    def test_final_installer_size_build_and_signature_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            installer, signature, public = self.signed_installer(Path(directory), "macos")
            root = ET.fromstring(generate("v0.14.12", "macos", "arm64", installer,
                                        signature, "1793000000", public_key=public))
            item = root.find("channel/item")
            self.assertEqual(item.find(f"{{{SPARKLE}}}version").text, "1793000000")
            self.assertEqual(item.find(f"{{{SPARKLE}}}shortVersionString").text, "0.14.12")
            enclosure = item.find("enclosure")
            self.assertEqual(int(enclosure.get("length")), installer.stat().st_size)
            self.assertIn("/v0.14.12/AppFlowy-0.14.12-macos-arm64.dmg", enclosure.get("url"))
            self.assertEqual(enclosure.get(f"{{{SPARKLE}}}edSignature"), signature)
            with self.assertRaises(ValueError):
                generate("0.14.12", "macos", "arm64", installer, "", "2", public_key=public)
            installer.write_bytes(b"")
            with self.assertRaises(ValueError):
                generate("0.14.12", "macos", "arm64", installer, signature, "2", public_key=public)


if __name__ == "__main__":
    unittest.main()
