"""Recognize only standard EPUB font-obfuscation metadata, never font data."""
from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath
from xml.etree.ElementTree import Element

OCF_NAMESPACE = "urn:oasis:names:tc:opendocument:xmlns:container"
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
MAX_FONT_METADATA_BYTES = 64 * 1024
_MAX_OBFUSCATED_FONTS = 128
_XMLENC = "{http://www.w3.org/2001/04/xmlenc#}"
_FONT_EMBEDDING = "http://www.idpf.org/2008/embedding"
_FONT_TYPES = {".otf": "font/otf", ".ttf": "font/ttf", ".woff": "font/woff", ".woff2": "font/woff2"}
_OPF = "{http://www.idpf.org/2007/opf}"


def _font_path(value: str) -> bool:
    # Only literal, unambiguous container-relative paths in this compatibility case.
    return bool(
        value and re.fullmatch(r"[A-Za-z0-9._/-]+", value)
        and all(part not in {"", ".", ".."} for part in value.split("/"))
        and value.split("/", 1)[0].casefold() != "meta-inf"
        and PurePosixPath(value).suffix.casefold() in _FONT_TYPES
    )


def obfuscated_font_paths(root: Element) -> set[str]:
    """Accept the exact IDPF marker, not encrypted payloads, keys or transforms."""
    rejected = "Unsupported encrypted EPUB metadata"
    if (root.tag != f"{{{OCF_NAMESPACE}}}encryption" or root.attrib
            or not 1 <= len(root) <= _MAX_OBFUSCATED_FONTS
            or any((node.text or "").strip() or (node.tail or "").strip() for node in root.iter())):
        raise ValueError(rejected)

    paths: set[str] = set()
    for entry in root:
        if entry.tag != _XMLENC + "EncryptedData" or entry.attrib or len(entry) != 2:
            raise ValueError(rejected)
        method, cipher = entry
        if (method.tag != _XMLENC + "EncryptionMethod" or len(method)
                or method.attrib != {"Algorithm": _FONT_EMBEDDING}
                or cipher.tag != _XMLENC + "CipherData" or cipher.attrib or len(cipher) != 1):
            raise ValueError(rejected)
        reference = cipher[0]
        path = reference.get("URI", "")
        if (reference.tag != _XMLENC + "CipherReference" or len(reference)
                or set(reference.attrib) != {"URI"} or not _font_path(path) or path in paths):
            raise ValueError(rejected)
        paths.add(path)
    return paths


def validate_font_declarations(package: Element, paths: set[str], items: dict[str, dict[str, str]]) -> None:
    """Require a single authoritative manifest, with unambiguous font targets."""
    rejected = "Unverified EPUB font obfuscation"
    manifests = package.findall(_OPF + "manifest")
    if (package.tag != _OPF + "package" or len(manifests) != 1
            or any(XML_BASE in node.attrib for node in package.iter())):
        raise ValueError(rejected)

    declarations: dict[str, str] = {}
    canonical_paths: set[str] = set()
    declared_ids: set[str] = set()
    for node in manifests[0]:
        identifier = node.get("id", "")
        item = items.get(identifier)
        if (node.tag != _OPF + "item" or not identifier or identifier in declared_ids or item is None
                or not node.get("href", "").strip() or node.get("media-type") != item["media_type"]):
            raise ValueError(rejected)
        declared_ids.add(identifier)
        path = item["path"]
        canonical = unicodedata.normalize("NFC", path).casefold()
        if canonical in canonical_paths:
            raise ValueError(rejected)
        canonical_paths.add(canonical)
        declarations[path] = item["media_type"]
    if len(items) != len(declared_ids):
        raise ValueError(rejected)
    for path in paths:
        if declarations.get(path) != _FONT_TYPES.get(PurePosixPath(path).suffix.casefold()):
            raise ValueError(rejected)
