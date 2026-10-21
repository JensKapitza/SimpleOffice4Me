#!/usr/bin/env python3
"""Create a CycloneDX 1.6 SBOM from Python packages or locked Cargo metadata."""
from __future__ import annotations

import argparse
import base64
import binascii
import importlib.metadata
import json
import platform
import re
import sys
import urllib.parse
import uuid
from xml.etree import ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from simpleoffice_version import build_info  # noqa: E402
from packaging.requirements import Requirement  # noqa: E402


def _python_package_key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def build_sbom(distributions=None, *, extras: tuple[str, ...] = ()) -> dict:
    """Inventory installed Python distributions, rooted at project requirements when available."""
    unique_components = {}
    installed = {}
    discover_project = distributions is None
    package_distributions = distributions if distributions is not None else importlib.metadata.distributions()
    for distribution in sorted(package_distributions, key=lambda item: item.metadata["Name"].casefold()):
        name = distribution.metadata["Name"]
        if not name:
            continue
        key = _python_package_key(name)
        purl = f"pkg:pypi/{key}@{distribution.version}"
        component = {"type": "library", "name": name, "version": distribution.version, "purl": purl}
        previous = unique_components.get(purl)
        if previous is not None and previous != component:
            raise ValueError(f"conflicting installed distribution metadata for {purl}")
        if key in installed and installed[key][0] != purl:
            raise ValueError(f"multiple installed versions of Python distribution {key}")
        unique_components[purl] = component
        installed[key] = (purl, distribution)
    project = installed.get(_python_package_key('simpleoffice4me')) if discover_project else None
    if project is None:
        included = set(unique_components)
        dependency_map = _python_edges(installed, included, extras)
        metadata_component = {"type": "application", "name": "SimpleOffice4Me",
                             "version": str(build_info(PROJECT_ROOT).get("release_version") or "2.0.0")}
    else:
        root_ref, root_distribution = project
        included, dependency_map = _python_runtime_graph(installed, root_ref, root_distribution, extras)
        del unique_components[root_ref]
        metadata_component = {
            "type": "application", "name": root_distribution.metadata["Name"],
            "version": root_distribution.version, "bom-ref": root_ref,
        }
    components = sorted((unique_components[ref] for ref in included if ref in unique_components),
                        key=lambda item: item["purl"])
    dependencies = [
        {"ref": ref, "dependsOn": sorted(dependency_map.get(ref, set()))}
        for ref in sorted(included | ({project[0]} if project else set()))
    ]
    inventory = "\n".join(sorted({ref for ref in included} | ({project[0]} if project else set())))
    serial = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/JensKapitza/SimpleOffice4Me\n" + inventory)
    return {"bomFormat": "CycloneDX", "specVersion": "1.6", "serialNumber": f"urn:uuid:{serial}", "version": 1,
            "metadata": {"timestamp": datetime.now(timezone.utc).isoformat(), "component": metadata_component}, "components": components,
            "dependencies": dependencies}


def _python_resolved_requirements(distribution, installed: dict, extra: str) -> dict[str, set[str]]:
    dependencies = {}
    for requirement_text in getattr(distribution, 'requires', None) or []:
        requirement = Requirement(requirement_text)
        if requirement.marker is not None and not requirement.marker.evaluate({"extra": extra}):
            continue
        dependency = installed.get(_python_package_key(requirement.name))
        if dependency is None:
            raise ValueError(f"unresolved installed Python dependency: {requirement.name}")
        dependencies.setdefault(dependency[0], set()).update(requirement.extras)
    return dependencies


def _python_edges(installed: dict, included: set[str], extras: tuple[str, ...]) -> dict[str, set[str]]:
    edges = {}
    for purl, distribution in installed.values():
        if purl in included:
            references = set()
            for extra in ('', *extras):
                references.update(_python_resolved_requirements(distribution, installed, extra))
            edges[purl] = references & included
    return edges


def _python_runtime_graph(installed: dict, root_ref: str, root_distribution, extras: tuple[str, ...]):
    included = set()
    edges = {}
    pending = [(root_ref, root_distribution, extra) for extra in ('', *extras)]
    visited = set()
    distributions_by_ref = {purl: distribution for purl, distribution in installed.values()}
    while pending:
        parent_ref, distribution, extra = pending.pop()
        state = (parent_ref, extra)
        if state in visited:
            continue
        visited.add(state)
        references = _python_resolved_requirements(distribution, installed, extra)
        edges.setdefault(parent_ref, set()).update(references)
        for ref, child_extras in references.items():
            included.add(ref)
            child = distributions_by_ref[ref]
            pending.append((ref, child, ''))
            pending.extend((ref, child, child_extra) for child_extra in child_extras)
    return included, edges


def build_cargo_sbom(metadata: dict) -> dict:
    packages = metadata['packages']
    root_id = metadata['resolve']['root']
    root = next(package for package in packages if package['id'] == root_id)
    components = []
    for package in sorted(packages, key=lambda item: (item['name'], item['version'])):
        component = {'type': 'application' if package['id'] == root_id else 'library',
                     'name': package['name'], 'version': package['version'],
                     'purl': f"pkg:cargo/{package['name']}@{package['version']}"}
        if package.get('license'):
            component['licenses'] = [{'expression': package['license']}]
        components.append(component)
    # Resolve edges by Cargo package ID, not by name: multiple versions may coexist.
    purls_by_id = {
        package['id']: f"pkg:cargo/{package['name']}@{package['version']}"
        for package in packages
    }
    nodes = metadata['resolve']['nodes']
    if {node['id'] for node in nodes} != set(purls_by_id):
        raise ValueError("Cargo resolve nodes must cover every resolved package")
    dependencies = []
    for node in sorted(nodes, key=lambda item: purls_by_id[item['id']]):
        refs = sorted({purls_by_id[dependency] for dependency in node['dependencies']})
        dependencies.append({'ref': purls_by_id[node['id']], 'dependsOn': refs})
    inventory = "\n".join(item['purl'] for item in components)
    serial = uuid.uuid5(uuid.NAMESPACE_URL, inventory)
    return {'bomFormat': 'CycloneDX', 'specVersion': '1.6',
            'serialNumber': f'urn:uuid:{serial}', 'version': 1,
            'metadata': {'timestamp': datetime.now(timezone.utc).isoformat(),
                         'component': {'type': 'application', 'name': root['name'],
                                       'version': root['version']}},
            'components': components, 'dependencies': dependencies}



def build_gradle_sbom(resolution: dict) -> dict:
    """Build an Android JVM SBOM from Gradle's resolved debugRuntimeClasspath."""
    records = resolution["components"]
    graph = resolution["dependencies"]
    components = []
    refs = {}
    for record in records:
        group, name, version = (record[field] for field in ("group", "name", "version"))
        key = f"{group}:{name}:{version}"
        purl = f"pkg:maven/{urllib.parse.quote(group, safe='.-_')}/{urllib.parse.quote(name, safe='.-_')}@{urllib.parse.quote(version, safe='.-_')}"
        if key in refs or purl in refs.values():
            raise ValueError(f"duplicate resolved Gradle component: {key}")
        refs[key] = purl
        components.append({"type": "library", "group": group, "name": name, "version": version, "purl": purl})
    if set(graph) != set(refs):
        raise ValueError("Gradle dependency graph does not match resolved component inventory")
    dependencies = []
    for key in sorted(graph):
        if any(target not in refs for target in graph[key]):
            raise ValueError(f"unresolved Gradle dependency for {key}")
        dependencies.append({"ref": refs[key], "dependsOn": sorted({refs[target] for target in graph[key]})})
    serial = uuid.uuid5(uuid.NAMESPACE_URL, "gradle\n" + "\n".join(sorted(refs.values())))
    return {
        "bomFormat": "CycloneDX", "specVersion": "1.6", "serialNumber": f"urn:uuid:{serial}", "version": 1,
        "metadata": {"timestamp": datetime.now(timezone.utc).isoformat(),
                     "component": {"type": "application", "name": "SimpleOffice4Me-Android-Gradle"}},
        "components": components, "dependencies": dependencies,
    }


def _npm_name(package_path: str, package: dict) -> str:
    name = package.get('name')
    if name:
        return str(name)
    marker = 'node_modules/'
    if marker not in package_path:
        raise ValueError(f"npm package has no name: {package_path}")
    return package_path.rsplit(marker, 1)[1]


def _npm_dependency_path(package_path: str, dependency: str, packages: dict) -> str:
    parent = package_path
    while True:
        candidate = f"{parent}/node_modules/{dependency}" if parent else f"node_modules/{dependency}"
        if candidate in packages:
            return candidate
        if '/node_modules/' in parent:
            parent = parent.rsplit('/node_modules/', 1)[0]
        elif parent.startswith('node_modules/'):
            parent = ''
        else:
            break
    raise ValueError(f"npm lock is missing resolved dependency {dependency} for {package_path or 'root'}")


def _npm_hashes(integrity: str) -> list[dict]:
    hashes = []
    for token in integrity.split():
        algorithm, separator, encoded = token.partition('-')
        normalized = algorithm.casefold().replace('-', '')
        if not separator or normalized not in {'sha256', 'sha384', 'sha512'}:
            continue
        try:
            digest = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError('npm lock contains an invalid integrity digest') from exc
        hashes.append({'alg': algorithm.upper().replace('SHA', 'SHA-'), 'content': digest.hex()})
    return hashes


def _npm_matches_platform(constraints: list[str] | str, current: str) -> bool:
    if isinstance(constraints, str):
        constraints = [constraints]
    if not isinstance(constraints, list) or any(not isinstance(item, str) for item in constraints):
        raise ValueError('npm lock contains invalid platform constraints')
    allowed = {item for item in constraints if not item.startswith('!')}
    denied = {item[1:] for item in constraints if item.startswith('!')}
    return current not in denied and (not allowed or current in allowed)


def _npm_host_platform() -> tuple[str, str, str | None]:
    npm_os = {'linux': 'linux', 'darwin': 'darwin', 'win32': 'win32'}.get(sys.platform)
    npm_cpu = {
        'x86_64': 'x64', 'amd64': 'x64', 'aarch64': 'arm64', 'arm64': 'arm64',
        'i386': 'ia32', 'i686': 'ia32',
    }.get(platform.machine().casefold())
    if npm_os is None or npm_cpu is None:
        raise ValueError(f"unsupported npm SBOM host platform: {sys.platform}/{platform.machine()}")
    libc_name = platform.libc_ver()[0].casefold()
    npm_libc = 'gnu' if 'glibc' in libc_name else 'musl' if 'musl' in libc_name else None
    return npm_os, npm_cpu, npm_libc


def build_npm_sbom(
    lock: dict, *, npm_os: str | None = None, npm_cpu: str | None = None,
    npm_libc: str | None = None,
) -> dict:
    """Build a CycloneDX inventory from npm's resolved package-lock v2/v3 graph."""
    packages = lock['packages']
    root = packages['']
    if npm_os is None or npm_cpu is None:
        detected_os, detected_cpu, detected_libc = _npm_host_platform()
        npm_os = npm_os or detected_os
        npm_cpu = npm_cpu or detected_cpu
        npm_libc = npm_libc or detected_libc
    root_name = str(root.get('name') or lock.get('name') or 'npm-application')
    root_version = str(root.get('version') or lock.get('version') or '0.0.0')

    def purl(name: str, version: str) -> str:
        return f"pkg:npm/{urllib.parse.quote(name, safe='/-._~')}@{urllib.parse.quote(version, safe='.+-_~')}"

    purls_by_path = {}
    unique_components = {}
    for package_path, package in sorted(packages.items()):
        if not package_path or package.get('link') or not package.get('version'):
            continue
        if not _npm_matches_platform(package.get('os') or [], npm_os):
            continue
        if not _npm_matches_platform(package.get('cpu') or [], npm_cpu):
            continue
        name = _npm_name(package_path, package)
        package_libc = package.get('libc')
        if package_libc and npm_libc and not _npm_matches_platform(package_libc, npm_libc):
            continue
        if package.get('optional') and npm_os == 'linux' and npm_libc:
            if name.endswith('-musl') and npm_libc != 'musl':
                continue
            if name.endswith('-gnu') and npm_libc != 'gnu':
                continue
        package_ref = purl(name, str(package['version']))
        component = {
            'type': 'library', 'name': name, 'version': str(package['version']), 'purl': package_ref,
        }
        hashes = _npm_hashes(str(package.get('integrity') or ''))
        if hashes:
            component['hashes'] = hashes
        license_value = package.get('license')
        if isinstance(license_value, str) and license_value.strip():
            component['licenses'] = [{'license': {'name': license_value.strip()}}]
        previous = unique_components.get(package_ref)
        if previous is not None and previous != component:
            raise ValueError(f"conflicting npm lock records for {package_ref}")
        unique_components[package_ref] = component
        purls_by_path[package_path] = package_ref

    root_ref = purl(root_name, root_version)
    dependencies = []
    for package_path, package in sorted(packages.items()):
        if package_path and package_path not in purls_by_path:
            continue
        parent_ref = root_ref if not package_path else purls_by_path[package_path]
        requested = set(package.get('dependencies') or {}) | set(package.get('optionalDependencies') or {})
        requested.update(package.get('peerDependencies') or {})
        optional = set(package.get('optionalDependencies') or {})
        optional.update(
            name for name, details in (package.get('peerDependenciesMeta') or {}).items()
            if details.get('optional') is True
        )
        if not package_path:
            requested.update(package.get('devDependencies') or {})
        resolved = set()
        for dependency in requested:
            try:
                resolved_path = _npm_dependency_path(package_path, dependency, packages)
            except ValueError:
                if dependency in optional:
                    continue
                raise
            resolved_ref = purls_by_path.get(resolved_path)
            if resolved_ref is None:
                if dependency in optional:
                    continue
                raise ValueError(f"npm dependency is not an installable package: {resolved_path}")
            resolved.add(resolved_ref)
        dependencies.append({'ref': parent_ref, 'dependsOn': sorted(resolved)})

    inventory = '\n'.join(sorted(unique_components))
    serial = uuid.uuid5(uuid.NAMESPACE_URL, 'https://github.com/JensKapitza/SimpleOffice4Me/npm\n' + inventory)
    return {
        'bomFormat': 'CycloneDX', 'specVersion': '1.6', 'serialNumber': f'urn:uuid:{serial}', 'version': 1,
        'metadata': {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'component': {'type': 'application', 'name': root_name, 'version': root_version, 'bom-ref': root_ref},
        },
        'components': sorted(unique_components.values(), key=lambda item: (item['name'], item['version'], item['purl'])),
        'dependencies': dependencies,
    }



CYCLONEDX_NAMESPACE = "http://cyclonedx.org/schema/bom/1.6"
ET.register_namespace("", CYCLONEDX_NAMESPACE)


def _xml_tag(name: str) -> str:
    return f"{{{CYCLONEDX_NAMESPACE}}}{name}"


def _xml_element(parent: ET.Element, name: str, value: str | None = None, **attrs: str) -> ET.Element:
    child = ET.SubElement(parent, _xml_tag(name), attrs)
    if value is not None:
        child.text = str(value)
    return child


def write_xml_sbom(document: dict, path: Path) -> None:
    """Serialize a generated CycloneDX inventory as schema-compatible 1.6 XML."""
    if document.get("specVersion") != "1.6":
        raise ValueError("CycloneDX XML requires specVersion 1.6")
    root = ET.Element(_xml_tag("bom"), {"serialNumber": document["serialNumber"], "version": str(document["version"])})
    metadata = _xml_element(root, "metadata")
    _xml_element(metadata, "timestamp", document["metadata"]["timestamp"])
    application = document["metadata"]["component"]
    component_attrs = {'type': application['type']}
    if application.get('bom-ref'):
        component_attrs['bom-ref'] = application['bom-ref']
    app_node = _xml_element(metadata, "component", **component_attrs)
    _xml_element(app_node, "name", application["name"])
    if application.get("version"):
        _xml_element(app_node, "version", application["version"])
    components = _xml_element(root, "components")
    for component in document["components"]:
        node = _xml_element(components, "component", type=component["type"], **{"bom-ref": component["purl"]})
        if component.get('group'):
            _xml_element(node, 'group', component['group'])
        _xml_element(node, "name", component["name"])
        _xml_element(node, "version", component["version"])
        if component.get('hashes'):
            hashes = _xml_element(node, 'hashes')
            for item in component['hashes']:
                _xml_element(hashes, 'hash', item['content'], alg=item['alg'])
        if component.get("licenses"):
            licenses = _xml_element(node, "licenses")
            for license_item in component["licenses"]:
                license_node = _xml_element(licenses, "license")
                if license_item.get("expression"):
                    # SPDX expressions are represented directly under <licenses>.
                    licenses.remove(license_node)
                    _xml_element(licenses, "expression", license_item["expression"])
                elif license_item.get('license', {}).get('name'):
                    licenses.remove(license_node)
                    license_node = _xml_element(licenses, 'license')
                    _xml_element(license_node, 'name', license_item['license']['name'])
        _xml_element(node, "purl", component["purl"])
    if document.get("dependencies") is not None:
        dependency_list = _xml_element(root, "dependencies")
        for dependency in document["dependencies"]:
            item = _xml_element(dependency_list, "dependency", ref=dependency["ref"])
            for reference in dependency["dependsOn"]:
                _xml_element(item, "dependency", ref=reference)
    path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='artifacts/sbom.cdx.json')
    parser.add_argument('--xml-output', type=Path, default=None,
                        help='Also write CycloneDX 1.6 XML to this path')
    parser.add_argument('--cargo-metadata', type=Path,
                        help='JSON from cargo metadata --locked --format-version 1')
    parser.add_argument('--npm-lock', type=Path, help='Resolved npm package-lock.json v2/v3')
    parser.add_argument('--gradle-resolution', type=Path, help='Resolved Gradle runtime dependency JSON')
    parser.add_argument('--npm-os', help='Target npm OS (linux, darwin, or win32)')
    parser.add_argument('--npm-cpu', help='Target npm CPU (x64, arm64, ia32, etc.)')
    parser.add_argument('--npm-libc', choices=('gnu', 'musl'), help='Linux libc for optional platform packages')
    parser.add_argument('--extra', action='append', default=[], help='Include an installed project extra (repeatable)')
    args = parser.parse_args()
    try:
        if sum(bool(value) for value in (args.cargo_metadata, args.npm_lock, args.gradle_resolution)) > 1:
            parser.error('select only one SBOM input source')
        if args.cargo_metadata:
            document = build_cargo_sbom(json.loads(args.cargo_metadata.read_text(encoding='utf-8')))
        elif args.gradle_resolution:
            document = build_gradle_sbom(json.loads(args.gradle_resolution.read_text(encoding='utf-8')))
        elif args.npm_lock:
            if bool(args.npm_os) != bool(args.npm_cpu):
                parser.error('--npm-os and --npm-cpu must be provided together')
            document = build_npm_sbom(
                json.loads(args.npm_lock.read_text(encoding='utf-8')),
                npm_os=args.npm_os, npm_cpu=args.npm_cpu, npm_libc=args.npm_libc,
            )
        else:
            document = build_sbom(extras=tuple(args.extra))
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        if args.cargo_metadata:
            parser.error('Cargo metadata must contain a resolved root package and valid package records')
        parser.error('SBOM input must contain valid resolved package records')
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
    if args.xml_output is not None:
        write_xml_sbom(document, args.xml_output)
        print(args.xml_output)
    print(path)


if __name__ == "__main__":
    main()
