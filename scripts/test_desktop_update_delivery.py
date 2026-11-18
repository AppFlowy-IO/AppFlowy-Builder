from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import desktop_update_delivery as delivery


REPO = "AppFlowy-IO/AppFlowy"
TAG = "0.15.1"
BASE = f"https://github.com/{REPO}/releases"
WINDOWS = "AppFlowy-0.15.1-windows-x86_64.exe"
URL = f"{BASE}/download/{TAG}/{WINDOWS}"
FEEDS = ["appcast-windows-x86_64.xml", "native-appcast-windows-x86_64.xml"]
XML = f"""<rss xmlns:sparkle="{delivery.SPARKLE}"><channel><item>
<sparkle:version>{TAG}</sparkle:version><sparkle:shortVersionString>{TAG}</sparkle:shortVersionString>
<enclosure url="{URL}" length="42" sparkle:os="windows" sparkle:dsaSignature="signed"/>
</item></channel></rss>""".encode()


def asset(name, size, checksum):
    return dict(id=1, name=name, size=size, digest=checksum, state="uploaded",
                browser_download_url=f"{BASE}/download/{TAG}/{name}")


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.release = dict(id=7, tag_name=TAG, draft=False, prerelease=False,
                            assets=[asset(WINDOWS, 42, "sha256:" + "1" * 64)])
        self.policy = dict(code=0, data=dict(auto_update_enabled=True, latest_version=TAG, download_url=URL,
                                            appcast_url=f"{BASE}/download/{TAG}/{FEEDS[1]}"))
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.args = SimpleNamespace(repository=REPO, tag=TAG, source="0.15.0", release_id="7", platform="windows",
                                    generation="both", feeds=Path(self.temp.name) / "feeds",
                                    evidence=Path(self.temp.name) / "installer-evidence", receipt=Path(self.temp.name) / "receipt.json")
        self.args.feeds.mkdir()

        for suite in ("update-e2e", "update-e2e-legacy"):
            directory = self.args.evidence / "release-update-e2e-windows-x86_64" / suite / "reports/candidate"
            directory.mkdir(parents=True)
            (directory / "result.json").write_text(json.dumps(dict(scenario="candidate", platform="windows",
                status="passed", source_version="0.15.0", target_version=TAG, installer_sha256="1" * 64)))
        for name in FEEDS:
            (self.args.feeds / name).write_bytes(XML)

    def github(self, *args):
        if args[0] == "api" and (args[1].endswith("/latest") or args[1].endswith("/tags/" + TAG)):
            return json.dumps(self.release).encode()
        self.fail(f"Unexpected GitHub operation {args}")

    def public(self, url):
        if url.startswith(delivery.API + "?"):
            self.assertIn("current_version=0.15.0", url)
            self.assertIn("arch=x86_64", url)
            return json.dumps(self.policy).encode()
        self.assertIn(url, [f"{BASE}/{route}/{name}" for route in ("latest/download", f"download/{TAG}") for name in FEEDS])
        return XML

    def stage(self):
        with patch.object(delivery, "gh", side_effect=self.github), patch.object(delivery, "public_bytes", side_effect=AssertionError("staging contacted public discovery")), patch.dict(os.environ, {"GITHUB_RUN_ID": "99", "GITHUB_RUN_ATTEMPT": "2"}):
            return delivery.stage(self.args)

    def copy_xml(self, *names):
        for name in names:
            payload = (self.args.feeds / name).read_bytes()
            self.release["assets"].append(asset(name, len(payload), delivery.digest(payload)))

    def verify(self, receipt, fetch=None):
        with patch.object(delivery, "gh", side_effect=self.github), patch.object(delivery, "public_bytes", side_effect=fetch or self.public):
            delivery.verify_published(receipt)

    def test_requested_feeds_and_previous_stable_version(self):
        self.assertEqual(len(delivery.selected("all platforms", "both")), 7)
        self.assertEqual(len(delivery.selected("all platforms", "native")), 3)
        self.assertEqual(len(delivery.selected("linux", "both")), 1)
        with self.assertRaises(ValueError):
            delivery.selected("linux", "native")
        self.assertEqual(delivery.previous_tag(["v0.14.9", TAG, "v0.15.0", "0.16.0-beta.1", "v0.16.1"], TAG), "v0.15.0")
        with self.assertRaises(ValueError):
            delivery.previous_tag([TAG], TAG)

    def test_staging_passes_with_no_public_xml_or_discovery(self):
        receipt = self.stage()
        self.assertEqual(self.release["assets"], [asset(WINDOWS, 42, "sha256:" + "1" * 64)])
        self.assertEqual(receipt["feeds"], dict.fromkeys(FEEDS, delivery.digest(XML)))
        self.assertEqual(receipt["source"], "0.15.0")
        self.assertEqual(receipt["run_id"], "99")
        self.assertEqual(receipt, json.loads(self.args.receipt.read_bytes()))

    def test_missing_native_artifact_blocks_staging(self):
        (self.args.feeds / FEEDS[1]).unlink()
        with self.assertRaisesRegex(ValueError, "Expected exactly"):
            self.stage()
        self.assertFalse(self.args.receipt.exists())

    def test_wrong_enclosure_rejected(self):
        for old, new in ((WINDOWS, WINDOWS.replace("windows", "macos")), ('length="42"', 'length="41"'),
                         ("dsaSignature", "edSignature"), ('os="windows"', 'os="macos"')):
            with self.subTest(change=new):
                (self.args.feeds / FEEDS[1]).write_bytes(XML.replace(old.encode(), new.encode()))
                with self.assertRaises(ValueError):
                    self.stage()

    def test_draft_stages_but_cannot_pass_a_live_check(self):
        self.release["draft"] = True
        receipt = self.stage()
        with self.assertRaisesRegex(ValueError, "draft"):
            self.verify(receipt)

    def test_installer_replaced_after_signing_blocks_staging(self):
        self.release["assets"][0]["digest"] = "sha256:" + "2" * 64
        with self.assertRaisesRegex(ValueError, "differs from the installer"):
            self.stage()

    def test_fixture_only_cannot_satisfy_the_previous_version_gate(self):
        self.args.source = "0.14.11"
        with self.assertRaisesRegex(ValueError, "no passing upgrade"):
            self.stage()

    def test_live_checks_exact_bytes_of_both_names(self):
        receipt = self.stage()
        self.copy_xml(*FEEDS)
        self.verify(receipt)
        def stale_latest(url):
            return b"cached older feed" if "/latest/download/" in url else self.public(url)
        with self.assertRaisesRegex(ValueError, "Public bytes differ"):
            self.verify(receipt, stale_latest)
        self.release["assets"][-1]["digest"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(ValueError, "differs from staged feed"):
            self.verify(receipt)

    def test_legacy_only_copy_fails_live_discovery(self):
        receipt = self.stage()
        self.copy_xml(FEEDS[0])
        with self.assertRaisesRegex(ValueError, "Missing or incomplete native-appcast"):
            self.verify(receipt)

    def test_replacing_the_installer_after_publication_fails_live_verification(self):
        receipt = self.stage()
        self.copy_xml(*FEEDS)
        self.release["assets"][0]["digest"] = "sha256:" + "3" * 64
        with self.assertRaisesRegex(ValueError, "Published installer differs"):
            self.verify(receipt)

    def test_client_fallback_fails_live_verification(self):
        receipt = self.stage()
        self.copy_xml(*FEEDS)
        for field, value in (("appcast_url", None), ("auto_update_enabled", False), ("latest_version", "0.15.0"),
                             ("appcast_url", URL), ("download_url", URL.replace("x86_64", "arm64"))):
            with self.subTest(field=field):
                policy = deepcopy(self.policy)
                policy["data"][field] = value
                with self.assertRaisesRegex(ValueError, "did not authorize"):
                    self.verify(receipt, lambda url: json.dumps(policy).encode() if url.startswith(delivery.API) else self.public(url))

    def test_replaced_release_does_not_verify(self):
        receipt = self.stage()
        self.release["id"] += 1
        with self.assertRaisesRegex(ValueError, "identity"):
            self.verify(receipt)


if __name__ == "__main__":
    unittest.main()
