"""Windows Defender scanner engine, selection, and security-page wiring.

The Defender provider drives documented cmdlets (Start-MpScan,
Get-MpThreatDetection, Get-MpComputerStatus, Update-MpSignature) through
fixed stdin-fed PowerShell. Every test uses a fake process runner; no real
scan, shell, or elevation is ever attempted here.
"""
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.attachment_security import (
    AttachmentSecurity,
    ClamAV,
    ScanResult,
    WindowsDefender,
    _powershell_executable,
    default_scanner,
)
from app.document_store import DocumentStore


class _Completed:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _defender_env(**overrides):
    env = {"SIMPLEOFFICE_SCANNER": "", "SIMPLEOFFICE_CLAMAV_SCANNER": "",
           "SystemRoot": "", "ProgramFiles": "", "ProgramFiles(x86)": ""}
    env.update(overrides)
    return patch.dict(os.environ, env, clear=False)


class PowershellResolutionTests(unittest.TestCase):
    def test_system_root_candidate_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"fake")
            with patch.dict(os.environ, {"SystemRoot": temp}, clear=False), \
                 patch("app.attachment_security.shutil.which", return_value=None):
                self.assertEqual(str(binary), _powershell_executable())

    def test_path_lookup_is_fallback(self):
        with patch.dict(os.environ, {"SystemRoot": ""}, clear=False), \
             patch("app.attachment_security.shutil.which",
                   side_effect=lambda name: "/usr/bin/pwsh" if name == "powershell.exe" else None):
            self.assertEqual("/usr/bin/pwsh", _powershell_executable())

    def test_missing_shell_reports_not_in_path(self):
        with patch.dict(os.environ, {"SystemRoot": ""}, clear=False), \
             patch("app.attachment_security.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "not in PATH"):
                _powershell_executable()


class StdinTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "sample.bin"
        self.path.write_bytes(b"sample")
        self.scanner = WindowsDefender(timeout=60)

    def test_scan_passes_path_as_stdin_data_to_fixed_argv(self):
        stdout = "\n".join([f"PATH:{self.path}", "STATUS:STARTED", "RESULT:CLEAN"])
        with patch.object(self.scanner, "executable", return_value="powershell.exe"), \
             patch("app.attachment_security.subprocess.run") as run:
            run.return_value = _Completed(0, stdout=stdout)
            result = self.scanner.scan(self.path)
        command, options = run.call_args
        self.assertEqual(5, len(command[0]))
        self.assertEqual("powershell.exe", command[0][0])
        self.assertEqual(["-NoProfile", "-NonInteractive", "-Command"], command[0][1:4])
        self.assertIn("Start-MpScan", command[0][4])
        self.assertIn("-ScanType CustomScan", command[0][4])
        self.assertIn("Get-MpThreatDetection", command[0][4])
        self.assertNotIn(str(self.path), command[0][4])
        self.assertEqual(str(self.path), options["input"])
        self.assertFalse(options["check"])
        self.assertEqual(60, options["timeout"])
        self.assertNotIn("shell", options)
        self.assertEqual("clean", result.verdict)
        self.assertEqual("Start-MpScan", result.engine)

    def test_path_transport_mismatch_fails_closed(self):
        stdout = "\n".join(["PATH:C:\\elsewhere\\other.bin", "STATUS:STARTED", "RESULT:CLEAN"])
        with patch.object(self.scanner, "executable", return_value="powershell.exe"), \
             patch("app.attachment_security.subprocess.run", return_value=_Completed(0, stdout=stdout)):
            with self.assertRaisesRegex(RuntimeError, "transport mismatch"):
                self.scanner.scan(self.path)

    def test_relative_path_is_rejected_without_subprocess(self):
        with patch("app.attachment_security.subprocess.run") as run:
            with self.assertRaisesRegex(RuntimeError, "absolute path"):
                self.scanner.scan(Path("relative.bin"))
        run.assert_not_called()

    def test_unparseable_output_fails_closed(self):
        with patch.object(self.scanner, "executable", return_value="powershell.exe"), \
             patch("app.attachment_security.subprocess.run", return_value=_Completed(0, stdout="garbage\n")):
            with self.assertRaises(RuntimeError):
                self.scanner.scan(self.path)

    def test_timeout_maps_to_timeout_message(self):
        with patch.object(self.scanner, "executable", return_value="powershell.exe"), \
             patch("app.attachment_security.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("powershell.exe", 60)):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                self.scanner.scan(self.path)

    def test_timeout_is_clamped(self):
        self.assertEqual(120, WindowsDefender().timeout)
        self.assertEqual(5, WindowsDefender(timeout=1).timeout)
        self.assertEqual(900, WindowsDefender(timeout=99999).timeout)


class ScanOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "sample.bin"
        self.path.write_bytes(b"sample")
        self.scanner = WindowsDefender()

    def _run_scan(self, stdout, returncode=0):
        with patch.object(self.scanner, "executable", return_value="powershell.exe"), \
             patch("app.attachment_security.subprocess.run",
                   return_value=_Completed(returncode, stdout=stdout)):
            return self.scanner.scan(self.path)

    def test_detection_match_is_infected(self):
        stdout = "\n".join([f"PATH:{self.path}", "STATUS:STARTED", "RESULT:INFECTED",
                            "THREAT:Trojan:Script/Wacatac"])
        result = self._run_scan(stdout)
        self.assertEqual("infected", result.verdict)
        self.assertIn("Wacatac", result.detail)

    def test_permission_failure_is_distinguished(self):
        stdout = "\n".join(["STATUS:ERROR", "CATEGORY:PermissionDenied",
                            "FQID:UnauthorizedAccess", "MSG:Access denied"])
        with self.assertRaisesRegex(RuntimeError, "permission denied.*no elevation"):
            self._run_scan(stdout)

    def test_missing_cmdlets_are_unavailable(self):
        stdout = "\n".join(["STATUS:ERROR", "CATEGORY:ObjectNotFound",
                            "FQID:CommandNotFoundException,Microsoft.PowerShell", "MSG:Start-MpScan"])
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            self._run_scan(stdout)

    def test_started_query_failure_is_unknown_never_clean(self):
        stdout = "\n".join([f"PATH:{self.path}", "STATUS:STARTED", "STATUS:ERROR",
                            "CATEGORY:InvalidOperation", "FQID:x", "MSG:query broke"])
        with self.assertRaisesRegex(RuntimeError, "started but the result is unknown"):
            self._run_scan(stdout)

    def test_nonzero_exit_fails_closed_with_redacted_path(self):
        with self.assertRaises(RuntimeError) as failure:
            self._run_scan(f"boom on {self.path}", returncode=1)
        self.assertIn("exit 1", str(failure.exception))
        self.assertNotIn(str(self.path), str(failure.exception))
        self.assertIn(self.path.name, str(failure.exception))

    def test_vanished_file_is_error_not_clean(self):
        self.path.unlink()
        stdout = "\n".join([f"PATH:{self.path}", "STATUS:STARTED", "RESULT:CLEAN"])
        with self.assertRaisesRegex(RuntimeError, "file changed during scan"):
            self._run_scan(stdout)


class DefenderStatusTests(unittest.TestCase):
    def test_available_reports_engine_and_signatures(self):
        scanner = WindowsDefender()
        stdout = "\n".join(["STATUS:OK", "AV:True", "SIG:1.2.3"])
        with patch("app.attachment_security.subprocess.run",
                   return_value=_Completed(0, stdout=stdout)), \
             patch("app.attachment_security.shutil.which", return_value="powershell.exe"):
            status = scanner.status()
        self.assertEqual("available", status["state"])
        self.assertEqual("Start-MpScan", status["engine"])
        self.assertIn("1.2.3", status["version"])

    def test_disabled_antivirus_is_error(self):
        scanner = WindowsDefender()
        stdout = "\n".join(["STATUS:OK", "AV:False", "SIG:1.2.3"])
        with patch("app.attachment_security.subprocess.run",
                   return_value=_Completed(0, stdout=stdout)), \
             patch("app.attachment_security.shutil.which", return_value="powershell.exe"):
            status = scanner.status()
        self.assertEqual("error", status["state"])
        self.assertIn("disabled", status["version"])

    def test_missing_cmdlets_are_unavailable(self):
        scanner = WindowsDefender()
        stdout = "\n".join(["STATUS:ERROR", "CATEGORY:ObjectNotFound",
                            "FQID:CommandNotFoundException,x", "MSG:Get-MpComputerStatus"])
        with patch("app.attachment_security.subprocess.run",
                   return_value=_Completed(0, stdout=stdout)), \
             patch("app.attachment_security.shutil.which", return_value="powershell.exe"):
            status = scanner.status()
        self.assertEqual("unavailable", status["state"])

    def test_denied_status_is_error(self):
        scanner = WindowsDefender()
        stdout = "\n".join(["STATUS:ERROR", "CATEGORY:PermissionDenied", "FQID:x", "MSG:denied"])
        with patch("app.attachment_security.subprocess.run",
                   return_value=_Completed(0, stdout=stdout)), \
             patch("app.attachment_security.shutil.which", return_value="powershell.exe"):
            status = scanner.status()
        self.assertEqual("error", status["state"])
        self.assertIn("permission denied", status["version"])

    def test_spawn_failure_is_unavailable(self):
        scanner = WindowsDefender()
        with patch("app.attachment_security.subprocess.run", side_effect=OSError("noexec")), \
             patch("app.attachment_security.shutil.which", return_value="powershell.exe"):
            self.assertEqual("unavailable", scanner.status()["state"])


class DefenderUpdateTests(unittest.TestCase):
    def test_update_uses_fixed_script_without_stdin(self):
        scanner = WindowsDefender()
        with patch.object(scanner, "executable", return_value="powershell.exe"), \
             patch("app.attachment_security.subprocess.run") as run:
            run.return_value = _Completed(0, stdout="STATUS:OK")
            output = scanner.update()
        command, options = run.call_args
        self.assertEqual("powershell.exe", command[0][0])
        self.assertEqual(["-NoProfile", "-NonInteractive", "-Command"], command[0][1:4])
        self.assertIn("Update-MpSignature", command[0][4])
        self.assertIsNone(options["input"])
        self.assertEqual(300, options["timeout"])
        self.assertIn("aktuell", output)

    def test_update_permission_failure(self):
        scanner = WindowsDefender()
        stdout = "\n".join(["STATUS:ERROR", "CATEGORY:PermissionDenied", "FQID:x", "MSG:denied"])
        with patch.object(scanner, "executable", return_value="powershell.exe"), \
             patch("app.attachment_security.subprocess.run",
                   return_value=_Completed(0, stdout=stdout)):
            with self.assertRaisesRegex(RuntimeError, "permission denied.*no elevation"):
                scanner.update()

    def test_update_nonzero_exit_fails(self):
        scanner = WindowsDefender()
        with patch.object(scanner, "executable", return_value="powershell.exe"), \
             patch("app.attachment_security.subprocess.run",
                   return_value=_Completed(1, stderr="broken")):
            with self.assertRaisesRegex(RuntimeError, "exit 1"):
                scanner.update()


class InRootGuardTests(unittest.TestCase):
    def test_in_root_paths_pass_and_escapes_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "documents"
            root.mkdir()
            security = AttachmentSecurity(root, scanner=object())
            security._require_in_root(root / "validated_pdf" / "a.pdf")
            outside = Path(temp) / "elsewhere" / "b.pdf"
            with self.assertRaisesRegex(RuntimeError, "escapes the document root"):
                security._require_in_root(outside)
            with self.assertRaisesRegex(RuntimeError, "escapes the document root"):
                security._require_in_root(root / ".." / "elsewhere" / "b.pdf")


class ErrorCodeTests(unittest.TestCase):
    def test_defender_states_map_to_distinct_codes(self):
        codes = AttachmentSecurity.safe_error_code
        self.assertEqual("scanner_unavailable", codes(RuntimeError("x is not installed or not in PATH")))
        self.assertEqual("scanner_timeout", codes(RuntimeError("Windows Defender scan timed out")))
        self.assertEqual("scanner_permission", codes(RuntimeError("scan failed: permission denied (x)")))
        self.assertEqual("scanner_unknown", codes(RuntimeError("scan started but the result is unknown")))
        self.assertEqual("scanner_error", codes(RuntimeError("ClamAV scan failed (exit 3)")))
        self.assertEqual("scanner_error", codes(RuntimeError("SIMPLEOFFICE_SCANNER must be one of: auto")))


class DefaultScannerTests(unittest.TestCase):
    def test_explicit_choice_wins(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_SCANNER": "clamav"}, clear=False):
            self.assertIsInstance(default_scanner(timeout=9), ClamAV)
        with patch.dict(os.environ, {"SIMPLEOFFICE_SCANNER": "defender"}, clear=False):
            scanner = default_scanner(timeout=9)
            self.assertIsInstance(scanner, WindowsDefender)
            self.assertEqual(9, scanner.timeout)

    def test_invalid_choice_fails_closed_without_raising(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_SCANNER": "nonsense"}, clear=False):
            scanner = default_scanner()
        self.assertEqual("unavailable", scanner.status()["state"])
        self.assertIn("SIMPLEOFFICE_SCANNER", scanner.status()["version"])
        with self.assertRaisesRegex(RuntimeError, "SIMPLEOFFICE_SCANNER"):
            scanner.scan(Path("sample.bin"))

    def test_auto_keeps_explicit_clamav_configuration(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_SCANNER": "", "SIMPLEOFFICE_CLAMAV_SCANNER": "/usr/bin/clamscan"}, clear=False), \
             patch("os.name", "nt"):
            self.assertIsInstance(default_scanner(), ClamAV)

    def test_auto_prefers_installed_clamav(self):
        with _defender_env(), patch("os.name", "nt"), \
             patch("app.attachment_security.shutil.which",
                   side_effect=lambda name: f"/usr/bin/{name}" if name == "clamdscan" else None):
            self.assertIsInstance(default_scanner(), ClamAV)

    def test_auto_selects_defender_on_windows_without_clamav(self):
        with _defender_env(), patch("os.name", "nt"), \
             patch("app.attachment_security.shutil.which", return_value=None):
            scanner = default_scanner(timeout=30)
            self.assertIsInstance(scanner, WindowsDefender)
            self.assertEqual(30, scanner.timeout)

    def test_auto_keeps_clamav_unavailable_off_windows(self):
        with _defender_env(), patch("os.name", "posix"), \
             patch("app.attachment_security.shutil.which", return_value=None):
            scanner = default_scanner()
            self.assertIsInstance(scanner, ClamAV)
            with self.assertRaisesRegex(RuntimeError, "not in PATH"):
                scanner.executable()

    def test_attachment_security_defaults_to_defender_on_windows(self):
        with tempfile.TemporaryDirectory() as temp, _defender_env(), patch("os.name", "nt"), \
             patch("app.attachment_security.shutil.which", return_value=None):
            security = AttachmentSecurity(Path(temp))
        self.assertIsInstance(security.scanner, WindowsDefender)
        self.assertEqual(120, security.scanner.timeout)


class SecurityPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(
            TESTING=True, DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) VALUES (?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("owner", generate_password_hash("owner-password")),
            )
            db.commit()
        self.addCleanup(app.config.update, self.saved)
        self.client = app.test_client()
        self.client.post("/auth/login", data={"username": "owner", "password": "owner-password"})

    def test_security_center_shows_defender_name_and_host_note(self):
        status = {"state": "available", "engine": "Start-MpScan", "version": "Signaturen 1.2.3"}
        with patch.dict(os.environ, {"SIMPLEOFFICE_SCANNER": "defender"}, clear=False), \
             patch("app.attachment_security.WindowsDefender.status", return_value=status):
            page = self.client.get("/documents/security")
        self.assertEqual(200, page.status_code)
        body = page.get_data(as_text=True)
        self.assertIn("Dateisicherheit / Windows Defender", body)
        self.assertIn("Windows Defender-Ereignisse", body)
        self.assertNotIn("ClamAV-Ereignisse", body)
        self.assertIn("Die Prüfung läuft auf dem Anwendungsserver.", body)

    def test_security_center_keeps_clamav_name_by_default(self):
        status = {"state": "unavailable", "engine": "", "version": "missing"}
        with patch.dict(os.environ, {"SIMPLEOFFICE_SCANNER": "", "SIMPLEOFFICE_CLAMAV_SCANNER": ""}, clear=False), \
             patch("app.attachment_security.ClamAV.status", return_value=status), \
             patch("app.attachment_security.shutil.which", return_value="/usr/bin/clamscan"):
            page = self.client.get("/documents/security")
        self.assertEqual(200, page.status_code)
        body = page.get_data(as_text=True)
        self.assertIn("Dateisicherheit / ClamAV", body)
        self.assertIn("ClamAV-Ereignisse", body)

    def test_scan_now_button_needs_runnable_provider(self):
        env = {"SIMPLEOFFICE_SCANNER": "defender", "SIMPLEOFFICE_SECURITY_ADMINS": "owner"}
        available = {"state": "available", "engine": "Start-MpScan", "version": "x"}
        with patch.dict(os.environ, env, clear=False), \
             patch("app.attachment_security.WindowsDefender.status", return_value=available):
            page = self.client.get("/documents/security")
        self.assertIn("Verwaltete Dateien jetzt scannen", page.get_data(as_text=True))
        missing = {"state": "unavailable", "engine": "", "version": "gone"}
        with patch.dict(os.environ, env, clear=False), \
             patch("app.attachment_security.WindowsDefender.status", return_value=missing):
            page = self.client.get("/documents/security")
        self.assertNotIn("Verwaltete Dateien jetzt scannen", page.get_data(as_text=True))

    def test_signature_update_uses_active_scanner(self):
        env = {"SIMPLEOFFICE_SCANNER": "defender", "SIMPLEOFFICE_SECURITY_ADMINS": "owner"}
        with patch.dict(os.environ, env, clear=False), \
             patch("app.attachment_security.WindowsDefender.update", return_value="aktuell") as update:
            response = self.client.post("/documents/security/update", follow_redirects=True)
        update.assert_called_once_with()
        self.assertEqual(200, response.status_code)
        self.assertIn("Windows Defender-Signaturen wurden aktualisiert.", response.get_data(as_text=True))

    def test_signature_update_stays_admin_gated(self):
        env = {"SIMPLEOFFICE_SCANNER": "defender", "SIMPLEOFFICE_SECURITY_ADMINS": ""}
        with patch.dict(os.environ, env, clear=False), \
             patch("app.attachment_security.WindowsDefender.update") as update:
            response = self.client.post("/documents/security/update")
        self.assertEqual(403, response.status_code)
        update.assert_not_called()

    def test_document_scan_flash_uses_engine_name(self):
        store = DocumentStore(Path(self.temp.name) / "documents")
        document = store.import_upload(io.BytesIO(b"note"), "note.txt", "owner")
        with patch.dict(os.environ, {"SIMPLEOFFICE_SCANNER": "defender"}, clear=False), \
             patch("app.attachment_security.WindowsDefender.scan",
                   return_value=ScanResult("clean", "OK", "Start-MpScan")):
            response = self.client.post(
                f"/documents/{document['document_id']}/security/scan", follow_redirects=True
            )
        self.assertEqual(200, response.status_code)
        self.assertIn("Windows Defender: Das Dokument ist unauffällig.", response.get_data(as_text=True))

    def test_detail_scan_button_needs_resolvable_provider(self):
        store = DocumentStore(Path(self.temp.name) / "documents")
        document = store.import_upload(io.BytesIO(b"note"), "note.txt", "owner")
        url = f"/documents/{document['document_id']}"
        with patch.dict(os.environ, {"SIMPLEOFFICE_SCANNER": "defender"}, clear=False), \
             patch("app.attachment_security.WindowsDefender.executable", return_value="powershell.exe"):
            page = self.client.get(url)
        self.assertIn("Mit Windows Defender prüfen", page.get_data(as_text=True))
        with patch.dict(os.environ, {"SIMPLEOFFICE_SCANNER": "defender"}, clear=False), \
             patch("app.attachment_security.WindowsDefender.executable",
                   side_effect=RuntimeError("gone")):
            page = self.client.get(url)
        self.assertNotIn("Mit Windows Defender prüfen", page.get_data(as_text=True))
        self.assertNotIn("/security/scan", page.get_data(as_text=True))
