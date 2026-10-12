#!/usr/bin/env python3
"""Prepare the Tauri client using shared project identity and existing UI assets."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from simpleoffice_version import build_info  # noqa: E402


def prepare(root):
    root = Path(root).resolve()
    if not (root / 'pyproject.toml').is_file():
        raise ValueError('Project metadata is missing')
    version = build_info(root)['release_version']
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?(?:\+[A-Za-z0-9.-]+)?', version):
        raise ValueError('Project version is invalid')
    client = root / 'desktop/tauri'
    package = json.loads((client / 'package.json').read_text(encoding='utf-8'))
    config = json.loads((client / 'src-tauri/tauri.conf.json').read_text(encoding='utf-8'))
    cargo_file = client / 'src-tauri/Cargo.toml'
    cargo = cargo_file.read_text(encoding='utf-8')
    package['version'] = version
    config['version'] = version
    cargo, count = re.subn(r'(?m)^(version\s*=\s*)"[^"]+"', lambda match: match.group(1) + json.dumps(version), cargo, count=1)
    if count != 1:
        raise ValueError('Rust package version is missing')
    lock_updates = {}
    node_lock_file = client / 'package-lock.json'
    if node_lock_file.is_file():
        node_lock = json.loads(node_lock_file.read_text(encoding='utf-8'))
        node_lock['version'] = version
        node_lock['packages']['']['version'] = version
        lock_updates[node_lock_file] = json.dumps(node_lock, indent=2) + '\n'
    rust_lock_file = client / 'src-tauri/Cargo.lock'
    if rust_lock_file.is_file():
        package_section = re.search(r'(?ms)^\[package\]\s*\n(.*?)(?=^\[|\Z)', cargo)
        name_match = re.search(r'(?m)^name\s*=\s*"([^"\n]+)"', package_section.group(1)) if package_section else None
        if not name_match:
            raise ValueError('Rust package name is missing')
        blocks = rust_lock_file.read_text(encoding='utf-8').split('[[package]]')
        changed = 0
        for index, block in enumerate(blocks[1:], 1):
            name = re.search(r'(?m)^name\s*=\s*"([^"\n]+)"', block)
            if name and name.group(1) == name_match.group(1) and not re.search(r'(?m)^source\s*=', block):
                blocks[index], count = re.subn(r'(?m)^(version\s*=\s*)"[^"]+"',
                                              lambda match: match.group(1) + json.dumps(version), block, count=1)
                changed += count
        if changed != 1:
            raise ValueError('Rust lockfile root package is missing or ambiguous')
        lock_updates[rust_lock_file] = '[[package]]'.join(blocks)
    assets = client / 'web/assets'
    assets.mkdir(parents=True, exist_ok=True)
    for source, name in (
        ('static/vendor/bootstrap-5.3.8/css/bootstrap.min.css', 'bootstrap.min.css'),
        ('static/css/app.css', 'app.css'),
        ('docs/assets/simpleoffice-documentation-logo.png', 'logo.png'),
    ):
        shutil.copyfile(root / source, assets / name)
    (client / 'package.json').write_text(json.dumps(package, indent=2) + '\n', encoding='utf-8')
    (client / 'src-tauri/tauri.conf.json').write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    cargo_file.write_text(cargo, encoding='utf-8')
    for path, content in lock_updates.items():
        path.write_text(content, encoding='utf-8')
    return {'version': version, 'assets': ['bootstrap.min.css', 'app.css', 'logo.png']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(ROOT))
    args = parser.parse_args()
    try:
        result = prepare(args.root)
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({'ok': False, 'error': 'Client metadata or shared assets are invalid'}))
        return 1
    print(json.dumps({'ok': True, **result}, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
