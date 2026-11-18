from copy import deepcopy
import json
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
                                    generation="both", mode="publish", feeds=Path(self.temp.name),
                                    evidence=Path(self.temp.name) / "installer-evidence", wait_seconds=0)
        self.uploads = []
        for suite in ("update-e2e", "update-e2e-legacy"):
            directory = self.args.evidence / "release-update-e2e-windows-x86_64" / suite / "reports/candidate"
            directory.mkdir(parents=True)
            (directory / "result.json").write_text(json.dumps(dict(scenario="candidate", platform="windows",
                status="passed", source_version="0.15.0", target_version=TAG, installer_sha256="1" * 64)))
        for name in FEEDS:
            (self.args.feeds / name).write_bytes(XML)

    def github(self, *args):
        if args[:2] == ("release", "upload"):
            path = args[3]
            self.uploads.append(path.name)
            self.release["assets"] = [a for a in self.release["assets"] if a["name"] != path.name]
            self.release["assets"].append(asset(path.name, path.stat().st_size, delivery.digest(path.read_bytes())))
            return b""
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

    def run_delivery(self, fetch=None):
        with patch.object(delivery, "gh", side_effect=self.github), patch.object(delivery, "public_bytes", side_effect=fetch or self.public):
            delivery.deliver(self.args)

    def test_requested_feeds_and_previous_stable_version(self):
        self.assertEqual(len(delivery.selected("all platforms", "both")), 7)
        self.assertEqual(len(delivery.selected("all platforms", "native")), 3)
        self.assertEqual(len(delivery.selected("linux", "both")), 1)
        with self.assertRaises(ValueError):
            delivery.selected("linux", "native")
        self.assertEqual(delivery.previous_tag(["v0.14.9", TAG, "v0.15.0", "0.16.0-beta.1", "v0.16.1"], TAG), "v0.15.0")
        with self.assertRaises(ValueError):
            delivery.previous_tag([TAG], TAG)

    def test_missing_native_artifact_blocks_all_writes(self):
        (self.args.feeds / FEEDS[1]).unlink()
        with self.assertRaisesRegex(ValueError, "Expected exactly"):
            self.run_delivery()
        self.assertEqual(self.uploads, [])

    def test_one_wrong_enclosure_blocks_all_writes(self):
        for old, new in ((WINDOWS, WINDOWS.replace("windows", "macos")), ('length="42"', 'length="41"'),
                         ("dsaSignature", "edSignature"), ('os="windows"', 'os="macos"')):
            with self.subTest(change=new):
                (self.args.feeds / FEEDS[1]).write_bytes(XML.replace(old.encode(), new.encode()))
                with self.assertRaises(ValueError):
                    self.run_delivery()
                self.assertEqual(self.uploads, [])

    def test_dry_run_validates_without_publishing_or_public_requests(self):
        self.release["draft"] = True
        self.args.mode = "stage"
        self.run_delivery(lambda _: self.fail("public request for a draft"))
        self.assertEqual(self.uploads, [])

    def test_installer_replaced_after_signing_blocks_all_uploads(self):
        self.release["assets"][0]["digest"] = "sha256:" + "2" * 64
        with self.assertRaisesRegex(ValueError, "differs from the installer"):
            self.run_delivery()
        self.assertEqual(self.uploads, [])

    def test_both_names_publish_once_even_with_identical_bytes(self):
        self.run_delivery()
        self.run_delivery()
        self.assertEqual(self.uploads, FEEDS)
        self.assertEqual(self.release["assets"][0]["name"], WINDOWS)

    def test_read_only_replay_checks_both_real_public_routes(self):
        self.run_delivery()
        self.uploads.clear()
        self.args.mode = "verify"
        def stale_latest(url):
            return b"cached older feed" if "/latest/download/" in url else self.public(url)
        with self.assertRaisesRegex(ValueError, "Public bytes differ"):
            self.run_delivery(stale_latest)
        self.assertEqual(self.uploads, [])

    def test_read_only_fails_for_artifacts_without_attached_native_feed(self):
        self.args.mode = "verify"
        self.release["assets"].append(asset(FEEDS[0], len(XML), delivery.digest(XML)))
        with self.assertRaisesRegex(ValueError, "Missing or incomplete native-appcast"):
            self.run_delivery()
        self.assertEqual(self.uploads, [])

    def test_client_fallback_is_a_live_failure_after_upload(self):
        self.run_delivery()
        self.uploads.clear()
        for field, value in (("appcast_url", None), ("auto_update_enabled", False), ("latest_version", "0.15.0"),
                             ("appcast_url", URL), ("download_url", URL.replace("x86_64", "arm64"))):
            with self.subTest(field=field):
                policy = deepcopy(self.policy)
                policy["data"][field] = value
                with self.assertRaisesRegex(ValueError, "did not authorize"):
                    self.run_delivery(lambda url: json.dumps(policy).encode() if url.startswith(delivery.API) else self.public(url))
        self.assertEqual(self.uploads, [])

    def test_identity_change_and_draft_rejected_before_upload(self):
        self.release["id"] += 1
        with self.assertRaisesRegex(ValueError, "identity"):
            self.run_delivery()
        self.release.update(id=7, draft=True)
        with self.assertRaisesRegex(ValueError, "draft"):
            self.run_delivery()
        self.assertEqual(self.uploads, [])


if __name__ == "__main__":
    unittest.main()
