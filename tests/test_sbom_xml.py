"""CycloneDX XML regression tests for installed Python and resolved Cargo inventories."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from xml.etree import ElementTree as ET
from unittest.mock import patch
from types import SimpleNamespace

from tools.generate_sbom import build_sbom, build_cargo_sbom, write_xml_sbom


NS = {"cdx": "http://cyclonedx.org/schema/bom/1.6"}


class SbomXmlTests(unittest.TestCase):
    def test_build_sbom_deduplicates_identical_distribution_records(self):
        distribution = SimpleNamespace(metadata={"Name": "simpleoffice4me"}, version="2.0.0")
        with patch("tools.generate_sbom.importlib.metadata.distributions", return_value=[distribution, distribution]):
            document = build_sbom()
        matching = [item for item in document["components"] if item["purl"] == "pkg:pypi/simpleoffice4me@2.0.0"]
        self.assertEqual(1, len(matching))

    def test_xml_preserves_inventory_and_spdx_license(self):
        document = {
            "bomFormat": "CycloneDX", "specVersion": "1.6",
            "serialNumber": "urn:uuid:00000000-0000-0000-0000-000000000001",
            "version": 1,
            "metadata": {
                "timestamp": "2026-10-21T00:00:00+00:00",
                "component": {"type": "application", "name": "SimpleOffice4Me", "version": "2.0"},
            },
            "components": [{
                "type": "library", "name": "sample", "version": "1.2",
                "purl": "pkg:cargo/sample@1.2",
                "licenses": [{"expression": "MIT OR Apache-2.0"}],
            }],
        }
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "sbom.xml"
            write_xml_sbom(document, output)
            root = ET.parse(output).getroot()
        self.assertEqual("1", root.attrib["version"])
        self.assertEqual("urn:uuid:00000000-0000-0000-0000-000000000001", root.attrib["serialNumber"])
        component = root.find("cdx:components/cdx:component", NS)
        self.assertEqual("pkg:cargo/sample@1.2", component.attrib["bom-ref"])
        self.assertEqual("sample", component.findtext("cdx:name", namespaces=NS))
        self.assertEqual("MIT OR Apache-2.0", component.findtext("cdx:licenses/cdx:expression", namespaces=NS))
        child_names = [child.tag.rsplit("}", 1)[-1] for child in component]
        self.assertLess(child_names.index("licenses"), child_names.index("purl"))

    def test_cargo_dependency_graph_preserves_distinct_versions(self):
        metadata = {
            "packages": [
                {"id": "root", "name": "app", "version": "1.0"},
                {"id": "old", "name": "common", "version": "1.0"},
                {"id": "new", "name": "common", "version": "2.0"},
            ],
            "resolve": {"root": "root", "nodes": [
                {"id": "root", "dependencies": ["old", "new"]},
                {"id": "old", "dependencies": []},
                {"id": "new", "dependencies": []},
            ]},
        }
        document = build_cargo_sbom(metadata)
        root_edge = next(edge for edge in document["dependencies"] if edge["ref"] == "pkg:cargo/app@1.0")
        self.assertEqual(["pkg:cargo/common@1.0", "pkg:cargo/common@2.0"], root_edge["dependsOn"])
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "cargo.xml"
            write_xml_sbom(document, output)
            tree = ET.parse(output)
        edges = tree.findall(".//cdx:dependencies/cdx:dependency[@ref='pkg:cargo/app@1.0']/cdx:dependency", NS)
        self.assertEqual({"pkg:cargo/common@1.0", "pkg:cargo/common@2.0"}, {edge.attrib["ref"] for edge in edges})

    def test_rejects_wrong_spec_version(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "1.6"):
                write_xml_sbom({"specVersion": "1.5"}, Path(tmp) / "sbom.xml")


if __name__ == "__main__":
    unittest.main()
