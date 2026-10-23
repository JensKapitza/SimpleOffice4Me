"""Tests for Android resolved Gradle SBOM license metadata validation."""

import unittest

from tools.check_gradle_sbom import validate_gradle_sbom


def _document(*, licenses=True, spec_version="1.6") -> dict:
    component = {
        "type": "library",
        "name": "androidx.appcompat",
        "purl": "pkg:maven/androidx.appcompat/appcompat@1.7.1",
    }
    if licenses:
        component["licenses"] = [{"license": {"name": "The Apache Software License, Version 2.0"}}]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": spec_version,
        "components": [component],
        "dependencies": [{"ref": component["purl"], "dependsOn": []}],
    }


class GradleSbomTests(unittest.TestCase):
    def test_accepts_resolved_maven_sbom_with_license_evidence(self):
        validate_gradle_sbom(_document())

    def test_rejects_missing_license_metadata(self):
        with self.assertRaisesRegex(ValueError, "no resolved Maven license metadata"):
            validate_gradle_sbom(_document(licenses=False))

    def test_rejects_wrong_cyclonedx_version(self):
        with self.assertRaisesRegex(ValueError, "CycloneDX 1.6"):
            validate_gradle_sbom(_document(spec_version="1.7"))


if __name__ == "__main__":
    unittest.main()
