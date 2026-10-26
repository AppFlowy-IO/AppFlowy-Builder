import base64
import pathlib
import tempfile
import unittest
import xml.etree.ElementTree as ET
from generate_native_appcast import generate, SPARKLE

class AppcastTests(unittest.TestCase):
    def test_apple_silicon_feed_can_target_universal_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = pathlib.Path(directory) / 'app.dmg'
            installer.write_bytes(b'universal signed installer')
            signature = base64.b64encode(b'x' * 64).decode()
            root = ET.fromstring(generate('0.14.12', 'macos', 'arm64', installer,
                signature, '1793000000', package_arch='universal'))
            self.assertTrue(root.find('channel/item/enclosure').get('url')
                .endswith('/AppFlowy-0.14.12-macos-universal.dmg'))
            with self.assertRaises(ValueError):
                generate('0.14.12', 'windows', 'x86_64', installer,
                    signature, '0.14.12', package_arch='universal')

    def test_windows_feed_carries_version_signature_and_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = pathlib.Path(directory) / 'app.exe'
            installer.write_bytes(b'final Windows installer')
            signature = base64.b64encode(b'DER encoded DSA signature').decode()
            root = ET.fromstring(generate(
                '0.14.12', 'windows', 'x86_64', installer, signature, '0.14.12',
                minimum_server_version='0.19.10', critical=True,
            ))
            item = root.find('channel/item')
            self.assertEqual(item.find(f'{{{SPARKLE}}}version').text, '0.14.12')
            self.assertEqual(item.find('minServerVersion').text, '0.19.10')
            self.assertIsNotNone(item.find(f'{{{SPARKLE}}}criticalUpdate'))
            enclosure = item.find('enclosure')
            self.assertEqual(enclosure.get(f'{{{SPARKLE}}}os'), 'windows')
            self.assertEqual(enclosure.get(f'{{{SPARKLE}}}dsaSignature'), signature)
            self.assertIsNone(enclosure.get(f'{{{SPARKLE}}}edSignature'))
            self.assertEqual(enclosure.get('url'),
                'https://github.com/AppFlowy-IO/AppFlowy/releases/download/0.14.12/AppFlowy-0.14.12-windows-x86_64.exe')
            self.assertEqual(int(enclosure.get('length')), installer.stat().st_size)

    def test_final_installer_size_build_and_signature_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = pathlib.Path(directory) / 'app.dmg'
            installer.write_bytes(b'final signed installer bytes')
            signature = base64.b64encode(b'x' * 64).decode()
            root = ET.fromstring(generate('v0.14.12', 'macos', 'arm64', installer, signature, '1793000000'))
            item = root.find('channel/item')
            self.assertEqual(item.find(f'{{{SPARKLE}}}version').text, '1793000000')
            self.assertEqual(item.find(f'{{{SPARKLE}}}shortVersionString').text, '0.14.12')
            enclosure = item.find('enclosure')
            self.assertEqual(int(enclosure.get('length')), installer.stat().st_size)
            self.assertIn('/v0.14.12/AppFlowy-0.14.12-macos-arm64.dmg', enclosure.get('url'))
            self.assertEqual(enclosure.get(f'{{{SPARKLE}}}edSignature'), signature)
            with self.assertRaises(ValueError):
                generate('0.14.12', 'macos', 'arm64', installer, '', '2')
            installer.write_bytes(b'')
            with self.assertRaises(ValueError):
                generate('0.14.12', 'macos', 'arm64', installer, signature, '2')

if __name__ == '__main__': unittest.main()
