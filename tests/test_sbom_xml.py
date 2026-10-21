"""CycloneDX XML regression tests for installed Python and resolved Cargo inventories."""
from pathlib import Path
from tempfile import TemporaryDirectory
import base64
import unittest
from xml.etree import ElementTree as ET
from unittest.mock import patch
from types import SimpleNamespace

from tools.generate_sbom import build_sbom, build_cargo_sbom, build_npm_sbom, write_xml_sbom


NS = {"cdx": "http://cyclonedx.org/schema/bom/1.6"}


class SbomXmlTests(unittest.TestCase):
    def test_build_sbom_deduplicates_identical_distribution_records(self):
        distribution = SimpleNamespace(metadata={"Name": "simpleoffice4me"}, version="2.0.0")
        with patch("tools.generate_sbom.importlib.metadata.distributions", return_value=[distribution, distribution]):
            document = build_sbom()
        matching = [item for item in document["components"] if item["purl"] == "pkg:pypi/simpleoffice4me@2.0.0"]
        self.assertEqual(1, len(matching))

    def test_python_dependencies_respect_markers_and_extras(self):
        alpha = SimpleNamespace(
            metadata={"Name": "Alpha_Pkg"}, version="1.0",
            requires=["beta-pkg>=2", "missing-package; python_version < '2'", "extra-only; extra == 'pdf'"],
        )
        beta = SimpleNamespace(metadata={"Name": "Beta.Pkg"}, version="2.1", requires=[])
        with patch("tools.generate_sbom.importlib.metadata.distributions", return_value=[alpha, beta]):
            document = build_sbom()
        edges = {entry["ref"]: entry["dependsOn"] for entry in document["dependencies"]}
        self.assertEqual(["pkg:pypi/beta-pkg@2.1"], edges["pkg:pypi/alpha-pkg@1.0"])
        self.assertEqual([], edges["pkg:pypi/beta-pkg@2.1"])
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "python.xml"
            write_xml_sbom(document, output)
            tree = ET.parse(output)
        self.assertIsNotNone(tree.find(
            ".//cdx:dependencies/cdx:dependency[@ref='pkg:pypi/alpha-pkg@1.0']/cdx:dependency",
            NS,
        ))

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

    def test_npm_lock_preserves_resolved_graph_hash_and_license(self):
        lock = {
            "name": "fixture-app", "version": "1.0.0", "lockfileVersion": 3,
            "packages": {
                "": {"name": "fixture-app", "version": "1.0.0", "devDependencies": {"@scope/tool": "^1"}},
                "node_modules/@scope/tool": {
                    "version": "1.2.0", "integrity": "sha512-" + base64.b64encode(b"fixture digest").decode(),
                    "license": "MIT", "dependencies": {"nested": "^2"},
                },
                "node_modules/@scope/tool/node_modules/nested": {"version": "2.1.0", "license": "Apache-2.0"},
            },
        }
        document = build_npm_sbom(lock, npm_os="linux", npm_cpu="x64")
        tool_ref = "pkg:npm/%40scope/tool@1.2.0"
        nested_ref = "pkg:npm/nested@2.1.0"
        tool = next(item for item in document["components"] if item["purl"] == tool_ref)
        self.assertEqual([{"alg": "SHA-512", "content": base64.b64decode(lock["packages"]["node_modules/@scope/tool"]["integrity"].removeprefix("sha512-")).hex()}], tool["hashes"])
        self.assertEqual([{"license": {"name": "MIT"}}], tool["licenses"])
        root_edge = next(item for item in document["dependencies"] if item["ref"] == "pkg:npm/fixture-app@1.0.0")
        self.assertEqual([tool_ref], root_edge["dependsOn"])
        tool_edge = next(item for item in document["dependencies"] if item["ref"] == tool_ref)
        self.assertEqual([nested_ref], tool_edge["dependsOn"])

        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "npm.xml"
            write_xml_sbom(document, output)
            root = ET.parse(output).getroot()
        xml_tool = root.find("cdx:components/cdx:component[cdx:name='@scope/tool']", NS)
        self.assertEqual("MIT", xml_tool.findtext("cdx:licenses/cdx:license/cdx:name", namespaces=NS))
        self.assertEqual("SHA-512", xml_tool.find("cdx:hashes/cdx:hash", NS).attrib["alg"])
        root_ref = root.find("cdx:metadata/cdx:component", NS).attrib["bom-ref"]
        self.assertEqual("pkg:npm/fixture-app@1.0.0", root_ref)

    def test_npm_lock_filters_unselected_optional_platform_packages(self):
        lock = {
            "name": "fixture-app", "version": "1.0.0", "lockfileVersion": 3,
            "packages": {
                "": {
                    "name": "fixture-app", "version": "1.0.0",
                    "optionalDependencies": {
                        "linux-tool": "1.0.0", "mac-tool": "1.0.0",
                        "gnu-cli": "1.0.0", "musl-cli": "1.0.0",
                    },
                },
                "node_modules/linux-tool": {"version": "1.0.0", "os": ["linux"], "cpu": ["x64"]},
                "node_modules/mac-tool": {"version": "1.0.0", "os": ["darwin"], "cpu": ["arm64"]},
                "node_modules/gnu-cli": {
                    "name": "@scope/cli-linux-x64-gnu", "version": "1.0.0", "optional": True,
                },
                "node_modules/musl-cli": {
                    "name": "@scope/cli-linux-x64-musl", "version": "1.0.0", "optional": True,
                },
            },
        }
        document = build_npm_sbom(lock, npm_os="linux", npm_cpu="x64", npm_libc="gnu")
        self.assertEqual(
            ["pkg:npm/%40scope/cli-linux-x64-gnu@1.0.0", "pkg:npm/linux-tool@1.0.0"],
            [item["purl"] for item in document["components"]],
        )
        self.assertEqual(
            {
                "ref": "pkg:npm/fixture-app@1.0.0",
                "dependsOn": ["pkg:npm/%40scope/cli-linux-x64-gnu@1.0.0", "pkg:npm/linux-tool@1.0.0"],
            },
            document["dependencies"][0],
        )

    def test_rejects_wrong_spec_version(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "1.6"):
                write_xml_sbom({"specVersion": "1.5"}, Path(tmp) / "sbom.xml")


if __name__ == "__main__":
    unittest.main()
