import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa

from verify_native_update import public_key_for, validate_windows_bundle


@unittest.skipUnless(sys.platform == "win32", "Windows resource loader")
class WindowsResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        program_files = Path(os.environ["ProgramFiles(x86)"])
        vswhere = program_files / "Microsoft Visual Studio/Installer/vswhere.exe"
        installation = subprocess.check_output(
            [str(vswhere), "-latest", "-products", "*", "-requires",
             "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
            text=True).strip()
        toolsets = Path(installation) / "VC/Tools/MSVC"
        cls.link = max(toolsets.glob("*/bin/Hostx64/x64/link.exe"))
        cls.rc = max((program_files / "Windows Kits/10/bin").glob("*/x64/rc.exe"))

    def resource_image(self, root, public_key):
        source = root / "fixture.rc"
        resource = root / "fixture.res"
        image = root / "fixture.dll"
        if public_key is None:
            source.write_text("1 RCDATA { 1 }\n")
        else:
            public_file = root / "public.pem"
            public_file.write_bytes(public_key)
            source.write_text(f'DSAPUB DSAPEM "{public_file.as_posix()}"\n')
        subprocess.run([str(self.rc), "/nologo", "/fo", str(resource), str(source)],
                       check=True, capture_output=True)
        subprocess.run([str(self.link), "/NOLOGO", "/DLL", "/NOENTRY", "/MACHINE:X64",
                        f"/OUT:{image}", str(resource)], check=True, capture_output=True)
        return image

    def test_compiled_resource_preserves_the_deployed_key(self):
        with tempfile.TemporaryDirectory(prefix="update resources ") as directory:
            image = self.resource_image(Path(directory), public_key_for("windows"))
            original = image.read_bytes()
            validate_windows_bundle(image)
            self.assertEqual(image.read_bytes(), original)

    def test_missing_and_changed_compiled_keys_are_rejected(self):
        changed = dsa.generate_private_key(key_size=2048).public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        for public_key in (None, changed):
            with self.subTest(missing=public_key is None), tempfile.TemporaryDirectory() as directory:
                image = self.resource_image(Path(directory), public_key)
                with self.assertRaisesRegex(ValueError, "missing|differs"):
                    validate_windows_bundle(image)


if __name__ == "__main__":
    unittest.main()
