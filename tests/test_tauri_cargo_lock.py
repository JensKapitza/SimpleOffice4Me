"""Security-sensitive Tauri Rust dependency lock assertions."""

from __future__ import annotations

from pathlib import Path
import tomllib
import unittest


class TauriCargoLockTests(unittest.TestCase):
    def test_tokio_lock_uses_rustsec_fixed_release(self):
        root = Path(__file__).resolve().parents[1]
        lock = tomllib.loads(
            (root / "desktop/tauri/src-tauri/Cargo.lock").read_text(encoding="utf-8"),
        )
        tokio_packages = [
            package for package in lock["package"] if package["name"] == "tokio"
        ]
        self.assertEqual(1, len(tokio_packages))
        self.assertEqual("1.53.3", tokio_packages[0]["version"])
        self.assertEqual(
            "ce3335fa71841cda333a58d7615b03901380ecf09d59b3296d21f8bbac0dde4e",
            tokio_packages[0]["checksum"],
        )


if __name__ == "__main__":
    unittest.main()
