"""Validate the hand-maintained SBOM vulnerability follow-up register."""

import json
import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRACKER_PATH = PROJECT_ROOT / "docs" / "sbom-vulnerability-findings.json"
REQUIRED_FINDING_FIELDS = {
    "id", "aliases", "component", "severity", "severity_source",
    "affected_versions", "fix_version", "advisory", "platforms",
}
REQUIRED_PLATFORM_FIELDS = {
    "name", "observed_version", "status", "blocker", "follow_up_issue",
    "follow_up_pr", "build",
}
TRACKED_STATUSES = {"open", "remediated_pending_merge", "resolved"}
PILLOW_AUDIT_FINDINGS = {
    "CVE-2026-112895", "CVE-2026-112400", "CVE-2026-112397", "CVE-2026-112896",
    "CVE-2026-112897", "CVE-2026-112395", "CVE-2026-112404", "CVE-2026-112899",
}
PILLOW_FINDING_TARGETS = {
    identifier: {"Ubuntu 22.04 Python 3.10 runtime", "Android ARM64 installable APK",
                 "Termux ARM64 native Python runtime"}
    for identifier in PILLOW_AUDIT_FINDINGS - {"CVE-2026-112899"}
}
PILLOW_FINDING_TARGETS["CVE-2026-112899"] = {
    "Ubuntu 22.04 Python 3.10 runtime", "Termux ARM64 native Python runtime",
}


class SbomFindingRegisterTests(unittest.TestCase):
    def test_findings_have_complete_traceable_follow_up_records(self):
        document = json.loads(TRACKER_PATH.read_text(encoding="utf-8"))
        self.assertEqual(document["schema_version"], 1)
        self.assertRegex(document["last_reviewed_commit"], r"^[0-9a-f]{40}$")
        self.assertTrue(document["findings"])

        identifiers = set()
        for finding in document["findings"]:
            self.assertTrue(REQUIRED_FINDING_FIELDS <= finding.keys())
            self.assertRegex(finding["id"], r"^(CVE-\d{4}-\d+|GHSA-[a-z0-9-]+)$")
            self.assertNotIn(finding["id"], identifiers)
            identifiers.add(finding["id"])
            self.assertTrue(finding["component"].startswith("pkg:"))
            self.assertTrue(finding["platforms"])
            self.assertTrue(self._is_url(finding["advisory"]))
            self.assertTrue(self._is_url(finding["severity_source"]))
            self.assertEqual(
                PILLOW_FINDING_TARGETS[finding["id"]],
                {target["name"] for target in finding["platforms"]},
            )

            for platform in finding["platforms"]:
                self.assertTrue(REQUIRED_PLATFORM_FIELDS <= platform.keys())
                self.assertIn(platform["status"], TRACKED_STATUSES)
                for field in ("follow_up_issue", "follow_up_pr", "build"):
                    self.assertTrue(self._is_url(platform[field]), field)

        self.assertTrue(PILLOW_AUDIT_FINDINGS <= identifiers)

    @staticmethod
    def _is_url(value: str) -> bool:
        return bool(re.fullmatch(r"https://[^\s]+", value))


if __name__ == "__main__":
    unittest.main()
