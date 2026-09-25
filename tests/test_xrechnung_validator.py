import hashlib
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from app import xrechnung_validation
from tools import install_xrechnung_validator as installer


def _prepared_upstream_fixture(path: Path) -> bytes:
    """Apply the same XRechnung spec-id filtering as the upstream Ant build."""
    payload = path.read_bytes()
    marker = b"@xrechnung.spec.id@"
    if marker not in payload:
        raise AssertionError(f"upstream fixture no longer contains expected marker: {path.name}")
    prepared = payload.replace(marker, installer.XRECHNUNG_SPEC_ID.encode("utf-8"))
    if marker in prepared:
        raise AssertionError(f"XRechnung fixture marker replacement failed: {path.name}")
    return prepared


def _config_zip(*, unsafe_name: str = "") -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(unsafe_name or "scenarios.xml", "<scenarios />")
        archive.writestr("resources/rules.xsl", "<xsl:stylesheet xmlns:xsl='http://www.w3.org/1999/XSL/Transform' version='1.0' />")
    return target.getvalue()


class XRechnungInstallerTests(unittest.TestCase):
    def _paths(self, root: Path):
        runtime = root / ".runtime-tools"
        return runtime, runtime / "validator.jar", runtime / "config"

    def test_pinned_install_verifies_jar_and_every_configuration_file(self):
        jar = b"synthetic validator jar"
        config = _config_zip()
        with tempfile.TemporaryDirectory() as temp:
            runtime, jar_target, config_dir = self._paths(Path(temp))
            with mock.patch.object(installer, "RUNTIME_DIR", runtime), \
                 mock.patch.object(installer, "JAR_TARGET", jar_target), \
                 mock.patch.object(installer, "CONFIG_DIR", config_dir), \
                 mock.patch.object(installer, "JAR_SHA256", hashlib.sha256(jar).hexdigest()), \
                 mock.patch.object(installer, "CONFIG_SHA256", hashlib.sha256(config).hexdigest()), \
                 mock.patch.object(installer, "_download", side_effect=[jar, config]), \
                 mock.patch.object(installer.shutil, "which", return_value="/usr/bin/java"):
                installed = installer.install()
                verified = installer.verify_installation()

            self.assertEqual(verified, installed)
            self.assertEqual(jar, jar_target.read_bytes())
            self.assertTrue((config_dir / "scenarios.xml").is_file())
            self.assertTrue((config_dir / "resources" / "rules.xsl").is_file())

    def test_configuration_tampering_fails_closed(self):
        jar = b"synthetic validator jar"
        config = _config_zip()
        with tempfile.TemporaryDirectory() as temp:
            runtime, jar_target, config_dir = self._paths(Path(temp))
            patches = (
                mock.patch.object(installer, "RUNTIME_DIR", runtime),
                mock.patch.object(installer, "JAR_TARGET", jar_target),
                mock.patch.object(installer, "CONFIG_DIR", config_dir),
                mock.patch.object(installer, "JAR_SHA256", hashlib.sha256(jar).hexdigest()),
                mock.patch.object(installer, "CONFIG_SHA256", hashlib.sha256(config).hexdigest()),
                mock.patch.object(installer, "_download", side_effect=[jar, config]),
                mock.patch.object(installer.shutil, "which", return_value="/usr/bin/java"),
            )
            for patcher in patches:
                patcher.start()
                self.addCleanup(patcher.stop)
            installer.install()
            (config_dir / "scenarios.xml").write_text("<tampered />", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "checksum changed"):
                installer.verify_installation()

    def test_archive_path_traversal_is_rejected(self):
        payload = _config_zip(unsafe_name="../outside.xml")
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "config"
            destination.mkdir()
            with self.assertRaisesRegex(RuntimeError, "unsafe path"):
                installer._extract_configuration(payload, destination)
            self.assertFalse((Path(temp) / "outside.xml").exists())

    def test_download_rejects_unpinned_url_before_network(self):
        with mock.patch.object(installer._OPENER, "open") as opener:
            with self.assertRaisesRegex(RuntimeError, "not allowed"):
                installer._download(
                    "https://github.com/itplr-kosit/validator/releases/latest/download/validator.jar",
                    max_bytes=1024,
                )
            opener.assert_not_called()


class XRechnungValidationTests(unittest.TestCase):
    XML = b"<?xml version='1.0'?><Invoice xmlns='urn:synthetic:test' />"

    def _available_status(self, root: Path):
        jar = root / "validator.jar"
        scenario = root / "config" / "scenarios.xml"
        jar.write_bytes(b"jar")
        scenario.parent.mkdir()
        scenario.write_text("<scenarios />", encoding="utf-8")
        return jar, scenario

    def test_validation_uses_local_repository_without_shell_or_output_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jar, scenario = self._available_status(root)
            completed = mock.Mock(returncode=0)
            with mock.patch.object(xrechnung_validation.shutil, "which", return_value="/usr/bin/java"), \
                 mock.patch.object(xrechnung_validation, "verify_installation", return_value=(jar, scenario)), \
                 mock.patch.object(xrechnung_validation, "CONFIG_DIR", scenario.parent), \
                 mock.patch.object(xrechnung_validation.subprocess, "run", return_value=completed) as run:
                result = xrechnung_validation.validate_xrechnung(self.XML)

        self.assertTrue(result["validated"])
        self.assertTrue(result["acceptable"])
        command = run.call_args.args[0]
        self.assertEqual("/usr/bin/java", command[0])
        self.assertIn("-s", command)
        self.assertIn("-r", command)
        self.assertEqual(str(scenario.parent), command[command.index("-r") + 1])
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertIs(xrechnung_validation.subprocess.DEVNULL, run.call_args.kwargs["stdout"])
        self.assertIs(xrechnung_validation.subprocess.DEVNULL, run.call_args.kwargs["stderr"])

    def test_nonzero_validator_result_is_rejected_but_still_a_completed_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jar, scenario = self._available_status(root)
            with mock.patch.object(xrechnung_validation.shutil, "which", return_value="/usr/bin/java"), \
                 mock.patch.object(xrechnung_validation, "verify_installation", return_value=(jar, scenario)), \
                 mock.patch.object(xrechnung_validation, "CONFIG_DIR", scenario.parent), \
                 mock.patch.object(xrechnung_validation.subprocess, "run", return_value=mock.Mock(returncode=2)):
                result = xrechnung_validation.validate_xrechnung(self.XML)

        self.assertTrue(result["validated"])
        self.assertFalse(result["acceptable"])
        self.assertEqual("rejected", result["reason"])
        self.assertEqual(2, result["exit_code"])

    def test_external_entity_xml_is_rejected_before_java(self):
        xml = b"<!DOCTYPE x [<!ENTITY ext SYSTEM 'https://example.invalid/secret'>]><x>&ext;</x>"
        with mock.patch.object(xrechnung_validation.subprocess, "run") as run:
            result = xrechnung_validation.validate_xrechnung(xml)
        self.assertFalse(result["validated"])
        self.assertEqual("unsafe_xml", result["reason"])
        run.assert_not_called()

    def test_missing_runtime_is_reported_without_invoice_payload(self):
        with mock.patch.object(xrechnung_validation.shutil, "which", return_value=None):
            result = xrechnung_validation.validate_xrechnung(self.XML)
        self.assertFalse(result["validated"])
        self.assertEqual("java_unavailable", result["reason"])
        self.assertNotIn("Invoice", str(result))


@unittest.skipUnless(
    os.environ.get("SIMPLEOFFICE_RUN_XRECHNUNG_INTEGRATION") == "1",
    "pinned KoSIT integration runtime is installed only in the standards CI job",
)
class XRechnungOfficialFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        installed = installer.install()
        if installed is None:
            raise unittest.SkipTest("Java runtime unavailable")
        cls.fixtures = Path(__file__).resolve().parent / "fixtures" / "xrechnung"

    def test_official_kosit_positive_and_negative_instances(self):
        accepted = xrechnung_validation.validate_xrechnung(
            _prepared_upstream_fixture(self.fixtures / "ubl001-valid.xml"),
            timeout_seconds=120,
        )
        rejected = xrechnung_validation.validate_xrechnung(
            _prepared_upstream_fixture(self.fixtures / "ubl002-rejected.xml"),
            timeout_seconds=120,
        )

        self.assertTrue(accepted["validated"], accepted)
        self.assertTrue(accepted["acceptable"], accepted)
        self.assertTrue(rejected["validated"], rejected)
        self.assertFalse(rejected["acceptable"], rejected)
        self.assertEqual("rejected", rejected["reason"])


if __name__ == "__main__":
    unittest.main()
