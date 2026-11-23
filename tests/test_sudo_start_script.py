"""sudo-start.sh: escalation choice, validation and dry-run forwarding."""
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = str(ROOT / "sudo-start.sh")


def run(*args, env=None):
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(
        ["bash", SCRIPT, *args], cwd=ROOT, text=True,
        capture_output=True, check=False, env=merged,
    )


class SudoStartScriptTests(unittest.TestCase):
    def test_help_lists_options_without_starting(self):
        result = run("--help")
        self.assertEqual(0, result.returncode)
        for token in ("--port", "--proxy-port", "--open-firewall",
                      "--print-command", "sudo", "su"):
            self.assertIn(token, result.stdout)

    def test_invalid_ports_are_rejected(self):
        for args in (("--port", "70000"), ("--port", "abc"),
                     ("--proxy-port", "80"), ("--proxy-port", "xyz")):
            with self.subTest(args=args):
                result = run(*args)
                self.assertEqual(2, result.returncode)

    def test_no_escalation_without_privileged_port(self):
        result = run("--port", "8080", "--global", "--print-command",
                     env={"SUDO_START_FORCE": "none"})
        self.assertEqual(0, result.returncode)
        self.assertIn("start.sh", result.stdout)
        self.assertIn("--port 8080", result.stdout)
        self.assertIn("--global", result.stdout)
        self.assertNotIn("sudo", result.stdout)
        self.assertNotIn("su -p", result.stdout)

    def test_sudo_command_is_printed(self):
        result = run("--port", "80", "--print-command",
                     env={"SUDO_START_FORCE": "sudo"})
        self.assertEqual(0, result.returncode)
        self.assertTrue(result.stdout.startswith("sudo -- "))
        self.assertIn("start.sh", result.stdout)
        self.assertIn("--port 80", result.stdout)

    def test_su_command_is_printed(self):
        result = run("--port", "443", "--print-command",
                     env={"SUDO_START_FORCE": "su"})
        self.assertEqual(0, result.returncode)
        self.assertIn("su -p root -c", result.stdout)
        self.assertIn("--port", result.stdout)
        self.assertIn("443", result.stdout)

    def test_unknown_args_are_forwarded(self):
        result = run("--global", "--threads", "8", "--print-command",
                     env={"SUDO_START_FORCE": "none"})
        self.assertEqual(0, result.returncode)
        self.assertIn("--global", result.stdout)
        self.assertIn("--threads 8", result.stdout)

    def test_open_firewall_needs_ports(self):
        result = run("--open-firewall", "--print-command",
                     env={"SUDO_START_FORCE": "none"})
        self.assertEqual(2, result.returncode)
        self.assertIn("--open-firewall braucht", result.stderr)


if __name__ == "__main__":
    unittest.main()
