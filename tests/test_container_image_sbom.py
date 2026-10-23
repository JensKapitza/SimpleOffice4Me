"""Tests for complete container image SBOM composition checks."""

import unittest

from tools.check_container_image_sbom import validate_combined_sbom


def _sbom(*purls: str) -> dict:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "components": [{"purl": purl} for purl in purls],
    }


class ContainerImageSbomTests(unittest.TestCase):
    def test_accepts_image_inventory_with_python_and_debian_components(self):
        python_sbom = _sbom("pkg:pypi/flask@3.1.0", "pkg:pypi/simpleoffice4me@2.0.0")
        image_sbom = _sbom(
            "pkg:pypi/flask@3.1.0", "pkg:pypi/simpleoffice4me@2.0.0",
            "pkg:deb/debian/libc6@2.36-9",
        )
        image_sbom["components"].append({"name": "unidentified image artifact"})
        validate_combined_sbom(python_sbom, image_sbom)

    def test_rejects_omitted_python_component(self):
        with self.assertRaisesRegex(ValueError, "omits Python components"):
            validate_combined_sbom(_sbom("pkg:pypi/flask@3.1.0"), _sbom("pkg:deb/debian/libc6@2.36"))

    def test_rejects_python_inventory_component_without_purl(self):
        python_sbom = _sbom()
        python_sbom["components"].append({"name": "missing purl"})
        image_sbom = _sbom("pkg:pypi/flask@3.1.0", "pkg:deb/debian/libc6@2.36")
        with self.assertRaisesRegex(ValueError, "without a package URL"):
            validate_combined_sbom(python_sbom, image_sbom)

    def test_rejects_missing_os_inventory(self):
        with self.assertRaisesRegex(ValueError, "no Debian OS packages"):
            validate_combined_sbom(_sbom("pkg:pypi/flask@3.1.0"), _sbom("pkg:pypi/flask@3.1.0"))

    def test_rejects_missing_python_inventory(self):
        with self.assertRaisesRegex(ValueError, "no Python packages"):
            validate_combined_sbom(_sbom(), _sbom("pkg:deb/debian/libc6@2.36"))

    def test_rejects_wrong_cyclonedx_version(self):
        invalid = _sbom("pkg:pypi/flask@3.1.0")
        invalid["specVersion"] = "1.5"
        with self.assertRaisesRegex(ValueError, "CycloneDX 1.6"):
            validate_combined_sbom(invalid, _sbom("pkg:pypi/flask@3.1.0", "pkg:deb/debian/libc6@2.36"))


if __name__ == "__main__":
    unittest.main()
