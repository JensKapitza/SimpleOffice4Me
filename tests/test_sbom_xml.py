"""CycloneDX XML regression tests for installed Python and resolved Cargo inventories."""
from pathlib import Path
from tempfile import TemporaryDirectory
import base64
import json
import unittest
from xml.etree import ElementTree as ET
from unittest.mock import patch
from types import SimpleNamespace
from packaging.markers import default_environment

from tools.generate_sbom import (
    build_sbom,
    build_cargo_sbom,
    build_npm_sbom,
    build_gradle_sbom,
    build_python_environment_sbom,
    main as generate_sbom_main,
    PYTHON_MARKER_ENVIRONMENT_KEYS,
    write_xml_sbom,
)


NS = {"cdx": "http://cyclonedx.org/schema/bom/1.6"}


class SbomXmlTests(unittest.TestCase):
    def test_python_environment_inventories_resolved_project_and_selected_extra(self):
        with TemporaryDirectory() as tmp:
            site_packages = Path(tmp) / "lib" / "python3.12" / "site-packages"
            site_packages.mkdir(parents=True)
            records = {
                "simpleoffice4me-2.0.0.dist-info": (
                    "Name: SimpleOffice4Me\nVersion: 2.0.0\n"
                    "Requires-Dist: Flask>=3.0\n"
                    "Requires-Dist: python-312-only; python_version < '3.13'\n"
                    "Requires-Dist: ocr-engine; extra == 'ocr'\n"
                    "Provides-Extra: ocr\n"
                ),
                "flask-3.1.3.dist-info": (
                    "Name: Flask\nVersion: 3.1.3\nLicense-Expression: BSD-3-Clause\n"
                ),
                "python_312_only-1.0.dist-info": "Name: python-312-only\nVersion: 1.0\n",
                "ocr_engine-1.0.dist-info": "Name: ocr-engine\nVersion: 1.0\n",
                "unrelated-9.0.dist-info": "Name: unrelated\nVersion: 9.0\n",
            }
            for directory, metadata in records.items():
                dist_info = site_packages / directory
                dist_info.mkdir()
                (dist_info / "METADATA").write_text(
                    "Metadata-Version: 2.1\n" + metadata,
                    encoding="utf-8",
                )
                if directory == "flask-3.1.3.dist-info":
                    (dist_info / "direct_url.json").write_text(json.dumps({
                        "url": "https://example.invalid/flask-3.1.3.whl",
                        "archive_info": {"hashes": {"sha256": "a" * 64}},
                    }), encoding="utf-8")

            marker_environment = default_environment()
            marker_environment.update(python_version="3.12", python_full_version="3.12.8")
            document = build_python_environment_sbom(
                Path(tmp), extras=("ocr",), marker_environment=marker_environment,
            )
            marker_file = Path(tmp) / "markers.json"
            marker_file.write_text(json.dumps(marker_environment), encoding="utf-8")
            json_output = Path(tmp) / "container.cdx.json"
            xml_output = Path(tmp) / "container.cdx.xml"
            with patch("sys.argv", [
                "generate_sbom.py", "--python-environment", tmp,
                "--marker-environment", str(marker_file), "--extra", "ocr",
                "--output", str(json_output), "--xml-output", str(xml_output),
            ]):
                generate_sbom_main()
            cli_document = json.loads(json_output.read_text(encoding="utf-8"))
            self.assertEqual(
                {item["purl"] for item in document["components"]},
                {item["purl"] for item in cli_document["components"]},
            )
            ET.parse(xml_output)

        root_ref = "pkg:pypi/simpleoffice4me@2.0.0"
        references = {component["purl"] for component in document["components"]}
        flask = next(item for item in document["components"] if item["purl"] == "pkg:pypi/flask@3.1.3")
        self.assertEqual([{"alg": "SHA-256", "content": "a" * 64}], flask["hashes"])
        self.assertEqual([{"expression": "BSD-3-Clause"}], flask["licenses"])
        self.assertEqual(
            {
                "pkg:pypi/flask@3.1.3",
                "pkg:pypi/ocr-engine@1.0",
                "pkg:pypi/python-312-only@1.0",
            },
            references,
        )
        self.assertNotIn("pkg:pypi/unrelated@9.0", references)
        self.assertEqual(root_ref, document["metadata"]["component"]["bom-ref"])
        root_edge = next(item for item in document["dependencies"] if item["ref"] == root_ref)
        self.assertEqual(sorted(references), root_edge["dependsOn"])

    def test_python_environment_requires_the_project_distribution(self):
        with TemporaryDirectory() as tmp:
            site_packages = Path(tmp) / "site-packages"
            site_packages.mkdir()
            with self.assertRaisesRegex(ValueError, "does not contain the SimpleOffice4Me distribution"):
                build_python_environment_sbom(site_packages)

    def test_python_environment_rejects_incomplete_marker_environment(self):
        with TemporaryDirectory() as tmp:
            site_packages = Path(tmp) / "site-packages"
            site_packages.mkdir()
            project = site_packages / "simpleoffice4me-2.0.0.dist-info"
            project.mkdir()
            (project / "METADATA").write_text(
                "Metadata-Version: 2.1\nName: SimpleOffice4Me\nVersion: 2.0.0\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Python marker environment is missing"):
                build_python_environment_sbom(site_packages, marker_environment={})

    def test_docker_sbom_captures_every_python_marker_from_the_target_image(self):
        workflow = Path(".github/workflows/docker-build.yml").read_text(encoding="utf-8")
        for key in PYTHON_MARKER_ENVIRONMENT_KEYS:
            self.assertIn(f'"{key}"', workflow)

    def test_python_sbom_rejects_malformed_direct_url_metadata(self):
        distribution = SimpleNamespace(
            metadata={"Name": "sample"}, version="1.0", read_text=lambda name: "[]",
        )
        with self.assertRaisesRegex(ValueError, "invalid direct_url.json"):
            build_sbom(distributions=[distribution])

    def test_build_sbom_deduplicates_project_distribution_as_application_root(self):
        distribution = SimpleNamespace(metadata={"Name": "simpleoffice4me"}, version="2.0.0")
        with patch("tools.generate_sbom.importlib.metadata.distributions", return_value=[distribution, distribution]):
            document = build_sbom()
        matching = [item for item in document["components"] if item["purl"] == "pkg:pypi/simpleoffice4me@2.0.0"]
        self.assertEqual([], matching)
        root_ref = document["metadata"]["component"]["bom-ref"]
        self.assertEqual("pkg:pypi/simpleoffice4me@2.0.0", root_ref)
        self.assertEqual([{"ref": root_ref, "dependsOn": []}], document["dependencies"])

    def test_python_dependencies_respect_markers_and_extras(self):
        root = SimpleNamespace(
            metadata={"Name": "simpleoffice4me"}, version="2.0",
            requires=["alpha-pkg>=1", "extra-only; extra == 'pdf'"],
        )
        alpha = SimpleNamespace(
            metadata={"Name": "Alpha_Pkg"}, version="1.0",
            requires=["beta-pkg>=2", "missing-package; python_version < '2'"],
        )
        beta = SimpleNamespace(metadata={"Name": "Beta.Pkg"}, version="2.1", requires=[])
        extra_only = SimpleNamespace(metadata={"Name": "Extra_Only"}, version="1.0", requires=[])
        with patch("tools.generate_sbom.importlib.metadata.distributions", return_value=[root, alpha, beta, extra_only]):
            document = build_sbom()
        edges = {entry["ref"]: entry["dependsOn"] for entry in document["dependencies"]}
        self.assertEqual(["pkg:pypi/beta-pkg@2.1"], edges["pkg:pypi/alpha-pkg@1.0"])
        self.assertEqual([], edges["pkg:pypi/beta-pkg@2.1"])
        self.assertEqual(["pkg:pypi/alpha-pkg@1.0"], edges["pkg:pypi/simpleoffice4me@2.0"])
        self.assertNotIn("pkg:pypi/extra-only@1.0", {item["purl"] for item in document["components"]})
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "python.xml"
            write_xml_sbom(document, output)
            tree = ET.parse(output)
        self.assertIsNotNone(tree.find(
            ".//cdx:dependencies/cdx:dependency[@ref='pkg:pypi/alpha-pkg@1.0']/cdx:dependency",
            NS,
        ))
        with patch("tools.generate_sbom.importlib.metadata.distributions", return_value=[root, alpha, beta, extra_only]):
            with_extra = build_sbom(extras=("pdf",))
        extra_edges = {entry["ref"]: entry["dependsOn"] for entry in with_extra["dependencies"]}
        self.assertIn("pkg:pypi/extra-only@1.0", extra_edges["pkg:pypi/simpleoffice4me@2.0"])

    def test_python_sbom_fails_closed_for_missing_active_dependency(self):
        root = SimpleNamespace(
            metadata={"Name": "simpleoffice4me"}, version="2.0", requires=["missing-package>=1"],
        )
        with patch("tools.generate_sbom.importlib.metadata.distributions", return_value=[root]):
            with self.assertRaisesRegex(ValueError, "unresolved installed Python dependency: missing-package"):
                build_sbom()

    def test_gradle_resolved_dependency_graph(self):
        resolution = {
            "application": {"name": "SimpleOffice4Me Android", "version": "1.0", "bomRef": "urn:simpleoffice4me:android:arm64"},
            "rootDependencies": ["org.example:app:1.0"],
            "components": [
                {"group": "org.example", "name": "app", "version": "1.0"},
                {"group": "org.example", "name": "lib", "version": "2.0"},
            ],
            "dependencies": {
                "org.example:app:1.0": ["org.example:lib:2.0"],
                "org.example:lib:2.0": [],
            },
        }
        document = build_gradle_sbom(resolution)
        root_edge = document["dependencies"][0]
        self.assertEqual("urn:simpleoffice4me:android:arm64", root_edge["ref"])
        self.assertEqual(["pkg:maven/org.example/app@1.0"], root_edge["dependsOn"])
        self.assertEqual("pkg:maven/org.example/lib@2.0", document["dependencies"][1]["dependsOn"][0])
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "gradle.xml"
            write_xml_sbom(document, output)
            root = ET.parse(output).getroot()
        component = root.find("cdx:components/cdx:component", NS)
        self.assertEqual("org.example", component.findtext("cdx:group", namespaces=NS))
        self.assertIsNotNone(root.find("cdx:dependencies", NS))

    def test_gradle_rejects_missing_resolved_dependencies(self):
        with self.assertRaisesRegex(ValueError, "unresolved Gradle dependency"):
            build_gradle_sbom({
                "application": {"name": "Android", "version": "1", "bomRef": "android"},
                "rootDependencies": ["a:b:1"],
                "components": [{"group": "a", "name": "b", "version": "1"}],
                "dependencies": {"a:b:1": ["missing:lib:1"]},
            })
        with self.assertRaisesRegex(ValueError, "unresolved Gradle application dependency"):
            build_gradle_sbom({
                "application": {"name": "Android", "version": "1", "bomRef": "android"},
                "rootDependencies": ["missing:lib:1"],
                "components": [{"group": "a", "name": "b", "version": "1"}],
                "dependencies": {"a:b:1": []},
            })

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
                "": {
                    "name": "fixture-app", "version": "1.0.0",
                    "devDependencies": {"@scope/tool": "^1", "nested": "^2"},
                },
                "node_modules/@scope/tool": {
                    "version": "1.2.0", "integrity": "sha512-" + base64.b64encode(b"fixture digest").decode(),
                    "license": "MIT", "dependencies": {"nested": "^2"},
                },
                "node_modules/@scope/tool/node_modules/nested": {"version": "2.1.0", "license": "Apache-2.0"},
                "node_modules/nested": {"version": "2.1.0", "license": "Apache-2.0"},
            },
        }
        document = build_npm_sbom(lock, npm_os="linux", npm_cpu="x64")
        tool_ref = "pkg:npm/%40scope/tool@1.2.0"
        nested_ref = "pkg:npm/nested@2.1.0"
        tool = next(item for item in document["components"] if item["purl"] == tool_ref)
        self.assertEqual([{"alg": "SHA-512", "content": base64.b64decode(lock["packages"]["node_modules/@scope/tool"]["integrity"].removeprefix("sha512-")).hex()}], tool["hashes"])
        self.assertEqual([{"license": {"name": "MIT"}}], tool["licenses"])
        root_edge = next(item for item in document["dependencies"] if item["ref"] == "pkg:npm/fixture-app@1.0.0")
        self.assertEqual([tool_ref, nested_ref], root_edge["dependsOn"])
        tool_edge = next(item for item in document["dependencies"] if item["ref"] == tool_ref)
        self.assertEqual([nested_ref], tool_edge["dependsOn"])
        dependency_refs = [item["ref"] for item in document["dependencies"]]
        self.assertEqual(len(dependency_refs), len(set(dependency_refs)))

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
        root_edge = next(item for item in document["dependencies"] if item["ref"] == "pkg:npm/fixture-app@1.0.0")
        self.assertEqual(
            {
                "ref": "pkg:npm/fixture-app@1.0.0",
                "dependsOn": ["pkg:npm/%40scope/cli-linux-x64-gnu@1.0.0", "pkg:npm/linux-tool@1.0.0"],
            },
            root_edge,
        )

    def test_rejects_wrong_spec_version(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "1.6"):
                write_xml_sbom({"specVersion": "1.5"}, Path(tmp) / "sbom.xml")


if __name__ == "__main__":
    unittest.main()
