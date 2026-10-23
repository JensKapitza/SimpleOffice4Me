"""Regression checks for release SBOM generation, validation and attachment."""

import unittest
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "project-release.yml"


class ProjectReleaseSbomTests(unittest.TestCase):
    def test_release_generates_validates_and_attaches_json_and_xml_inventories(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        generation = workflow.index("- name: Generate release SBOM")
        validation = workflow.index("- name: Validate every CycloneDX inventory collected for the release")
        checksums = workflow.index("- name: Create checksums and release archive")
        publication = workflow.index("gh release upload")

        self.assertLess(generation, validation)
        self.assertLess(validation, checksums)
        self.assertIn("--xml-output artifacts/sbom.cdx.xml", workflow[generation:validation])
        self.assertIn("SimpleOffice4Me-${{ steps.version.outputs.value }}-sbom.cdx.json", workflow)
        self.assertIn("SimpleOffice4Me-${{ steps.version.outputs.value }}-sbom.cdx.xml", workflow)
        self.assertIn("find release-downloads -type f -name '*.cdx.json'", workflow[validation:checksums])
        self.assertIn("find release-downloads -type f -name '*.cdx.xml'", workflow[validation:checksums])
        self.assertIn("--input-version v1_6 --fail-on-errors", workflow[validation:checksums])
        self.assertIn('"${RELEASE_FILES[@]}"', workflow[publication:])
        self.assertIn("release-downloads/**", workflow[publication:])


if __name__ == "__main__":
    unittest.main()
