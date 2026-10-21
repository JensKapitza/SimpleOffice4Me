"""Black-box checks for APK Chaquopy wheel inventory extraction."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from tools.android_apk_python_sbom import packaged_distributions, enforce_android_pillow_policy
from tools.generate_sbom import build_sbom


def make_apk(path: Path, metadata: bytes | None, archive_name="assets/chaquopy/requirements-common.imy"):
    nested = BytesIO()
    with zipfile.ZipFile(nested, "w") as requirements:
        if metadata is not None:
            requirements.writestr("Pillow-11.0.0.dist-info/METADATA", metadata)
    with zipfile.ZipFile(path, "w") as apk:
        apk.writestr(archive_name, nested.getvalue())


class AndroidApkPythonSbomTests(unittest.TestCase):
    def test_security_gate_rejects_bundled_pillow_11(self):
        with TemporaryDirectory() as tmp:
            apk = Path(tmp) / "app.apk"
            make_apk(apk, b"Metadata-Version: 2.1\nName: Pillow\nVersion: 11.0.0\n")
            with self.assertRaisesRegex(ValueError, "below the 12.2.0 security baseline"):
                enforce_android_pillow_policy(packaged_distributions(apk))

    def test_security_gate_accepts_updated_pillow(self):
        with TemporaryDirectory() as tmp:
            apk = Path(tmp) / "app.apk"
            make_apk(apk, b"Metadata-Version: 2.1\nName: Pillow\nVersion: 13.0.0\n")
            enforce_android_pillow_policy(packaged_distributions(apk))

    def test_security_gate_rejects_missing_pillow(self):
        from types import SimpleNamespace
        package = SimpleNamespace(metadata={"Name": "Flask"}, version="3.1.3")
        with self.assertRaisesRegex(ValueError, "missing Pillow metadata"):
            enforce_android_pillow_policy([package])

    def test_security_gate_rejects_old_pillow_even_with_new_chaquopy(self):
        from types import SimpleNamespace
        package = SimpleNamespace(metadata={"Name": "Pillow"}, version="11.0.0")
        with self.assertRaisesRegex(ValueError, "below the 12.2.0"):
            enforce_android_pillow_policy([package])

    def test_extracts_actual_bundled_version(self):
        with TemporaryDirectory() as tmp:
            apk = Path(tmp) / "app.apk"
            make_apk(apk, b"Metadata-Version: 2.1\nName: Pillow\nVersion: 11.0.0\nRequires-Dist: typing-extensions\n")
            packages = packaged_distributions(apk)
        self.assertEqual([("Pillow", "11.0.0")], [(p.metadata["Name"], p.version) for p in packages])
        self.assertEqual(["typing-extensions"], packages[0].requires)

    def test_sbom_uses_only_distributions_extracted_from_the_apk(self):
        with TemporaryDirectory() as tmp:
            apk = Path(tmp) / "app.apk"
            make_apk(apk, b"Metadata-Version: 2.1\nName: Pillow\nVersion: 11.0.0\n")
            document = build_sbom(packaged_distributions(apk))
        self.assertEqual(["pkg:pypi/pillow@11.0.0"], [item["purl"] for item in document["components"]])

    def test_sbom_fails_when_bundled_wheel_dependency_is_missing(self):
        with TemporaryDirectory() as tmp:
            apk = Path(tmp) / "app.apk"
            make_apk(apk, b"Metadata-Version: 2.1\nName: Pillow\nVersion: 11.0.0\nRequires-Dist: missing-wheel\n")
            with self.assertRaisesRegex(ValueError, "unresolved installed Python dependency: missing-wheel"):
                build_sbom(packaged_distributions(apk))

    def test_missing_wheel_metadata_is_not_claimed_complete(self):
        with TemporaryDirectory() as tmp:
            apk = Path(tmp) / "app.apk"
            make_apk(apk, None)
            with self.assertRaisesRegex(ValueError, "no dist-info"):
                packaged_distributions(apk)

    def test_no_chaquopy_requirements_archive_fails(self):
        with TemporaryDirectory() as tmp:
            apk = Path(tmp) / "app.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("assets/example.txt", "example")
            with self.assertRaisesRegex(ValueError, "no Chaquopy"):
                packaged_distributions(apk)


if __name__ == "__main__":
    unittest.main()
