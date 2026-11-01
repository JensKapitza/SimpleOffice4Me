"""Bound external package-manager calls in the Android wheel workflow."""

from __future__ import annotations

from pathlib import Path
import unittest


class AndroidNativeWheelsWorkflowTests(unittest.TestCase):
    def test_cryptography_link_step_bounds_apt_network_operations(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/android-native-wheels.yml").read_text(
            encoding="utf-8",
        )
        step = workflow.split(
            "- name: Link cryptography extension to Android libpython", 1,
        )[1].split("- name:", 1)[0]
        normalized_step = " ".join(step.replace("\\\n", " ").split())
        timeout = "timeout --signal=TERM --kill-after=10s 5m"

        self.assertIn(f"{timeout} sudo apt-get update -qq", normalized_step)
        self.assertIn(
            f"{timeout} sudo apt-get install -y --no-install-recommends patchelf",
            normalized_step,
        )


if __name__ == "__main__":
    unittest.main()
