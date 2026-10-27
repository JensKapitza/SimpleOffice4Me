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
        artifact_upload = workflow.index("name: simpleoffice4me-project-release-")
        publish_job = workflow.index("\n  publish:\n")
        publication = workflow.index("gh release upload")

        self.assertLess(generation, validation)
        self.assertLess(validation, checksums)
        self.assertLess(checksums, artifact_upload)
        self.assertLess(artifact_upload, publish_job)
        self.assertLess(publish_job, publication)
        self.assertIn("--xml-output artifacts/sbom.cdx.xml", workflow[generation:validation])
        self.assertIn("SimpleOffice4Me-${{ steps.version.outputs.value }}-sbom.cdx.json", workflow)
        self.assertIn("SimpleOffice4Me-${{ steps.version.outputs.value }}-sbom.cdx.xml", workflow)
        self.assertIn("find release-downloads -type f -name '*.cdx.json'", workflow[validation:checksums])
        self.assertIn("find release-downloads -type f -name '*.cdx.xml'", workflow[validation:checksums])
        self.assertIn("--input-version v1_6 --fail-on-errors", workflow[validation:checksums])
        self.assertIn("release-downloads/**", workflow[artifact_upload:publish_job])
        self.assertIn('gh run download "$GITHUB_RUN_ID"', workflow[publish_job:publication])
        self.assertIn('"${RELEASE_FILES[@]}"', workflow[publication:])
        self.assertIn("release-payload/release-downloads", workflow[publish_job:publication])

    def test_release_token_permissions_are_scoped_to_required_jobs(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        jobs = workflow.split("jobs:\n", maxsplit=1)[1]
        dispatch, remainder = jobs.split("  build:\n", maxsplit=1)
        build, publish = remainder.split("  publish:\n", maxsplit=1)

        self.assertNotIn("actions: write", workflow.split("jobs:\n", maxsplit=1)[0])
        self.assertNotIn("contents: write", workflow.split("jobs:\n", maxsplit=1)[0])
        self.assertIn("actions: write", dispatch)
        self.assertIn("contents: read", dispatch)
        self.assertIn("BUILD_REF: ${{ github.ref_name }}", dispatch)
        self.assertNotIn("BUILD_REF: ${{ github.ref }}", dispatch)
        self.assertIn("actions: read", build)
        self.assertIn("contents: read", build)
        self.assertIn("actions: read", publish)
        self.assertIn("contents: write", publish)


if __name__ == "__main__":
    unittest.main()
