"""Bounded, extraction-free EPUB reader using only local archive members."""
from __future__ import annotations

import html
import posixpath
import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree
from pathlib import PurePosixPath
from typing import Any

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

MAX_EPUB_BYTES = 100 * 1024 * 1024
MAX_ENTRIES = 5_000
MAX_ENTRY_BYTES = 20 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 200 * 1024 * 1024
MAX_CHAPTERS = 1_000
MAX_CHAPTER_TEXT = 2 * 1024 * 1024
MAX_COVER_BYTES = 10 * 1024 * 1024
SAFE_COVER_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def _safe_name(value: str) -> str:
    raw = str(value or "").replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or raw.startswith("/") or ".." in path.parts or any(part in {"", "."} for part in path.parts):
        raise ValueError("EPUB contains an unsafe archive path")
    if ":" in path.parts[0]:
        raise ValueError("EPUB contains an unsafe archive path")
    return path.as_posix()


def _join(base: str, relative: str) -> str:
    target = posixpath.normpath(posixpath.join(posixpath.dirname(base), relative.split("#", 1)[0]))
    return _safe_name(target)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _text(root: Any) -> str:
    chunks: list[str] = []
    size = 0

    def visit(node: Any) -> None:
        nonlocal size
        if _local(str(node.tag)) in {"script", "style", "noscript", "template"}:
            return
        if node.text:
            clean = " ".join(str(node.text).split())
            if clean:
                chunks.append(clean)
                size += len(clean)
        for child in list(node):
            visit(child)
            if child.tail:
                clean = " ".join(str(child.tail).split())
                if clean:
                    chunks.append(clean)
                    size += len(clean)
            if size > MAX_CHAPTER_TEXT:
                raise ValueError("EPUB chapter text exceeds the supported limit")

    visit(root)
    return "\n\n".join(chunks)


class EpubBook:
    def __init__(self, raw: bytes):
        if len(raw) > MAX_EPUB_BYTES:
            raise ValueError("EPUB exceeds the supported size")
        self.raw = raw
        self.entries: dict[str, zipfile.ZipInfo] = {}
        self.manifest: dict[str, dict[str, str]] = {}
        self.spine: list[str] = []
        self.title = ""
        self.author = ""
        self.cover_item = ""
        self._load()

    def _read(self, archive: zipfile.ZipFile, name: str) -> bytes:
        safe = _safe_name(name)
        info = self.entries.get(safe)
        if info is None:
            raise ValueError("EPUB references a missing archive member")
        if info.file_size > MAX_ENTRY_BYTES:
            raise ValueError("EPUB member exceeds the supported size")
        data = archive.read(info)
        if len(data) != info.file_size:
            raise ValueError("EPUB member size changed while reading")
        return data

    @staticmethod
    def _xml(data: bytes):
        try:
            return SafeET.fromstring(data)
        except (ElementTree.ParseError, DefusedXmlException) as exc:
            raise ValueError("EPUB contains invalid XML") from exc

    def _load(self) -> None:
        try:
            archive = zipfile.ZipFile(BytesIO(self.raw))
        except zipfile.BadZipFile as exc:
            raise ValueError("invalid EPUB archive") from exc
        with archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES:
                raise ValueError("EPUB contains too many archive entries")
            total = 0
            for info in infos:
                name = _safe_name(info.filename)
                if info.is_dir():
                    continue
                if name in self.entries:
                    raise ValueError("EPUB contains duplicate archive members")
                if info.flag_bits & 0x1:
                    raise ValueError("encrypted EPUB members are not supported")
                if info.file_size < 0 or info.compress_size < 0:
                    raise ValueError("EPUB member size is invalid")
                total += info.file_size
                if total > MAX_TOTAL_UNCOMPRESSED:
                    raise ValueError("EPUB expands beyond the supported limit")
                if info.file_size > MAX_ENTRY_BYTES:
                    raise ValueError("EPUB member exceeds the supported size")
                self.entries[name] = info
            if "META-INF/encryption.xml" in self.entries:
                raise ValueError("DRM or encrypted EPUB content is not supported")

            container = self._xml(self._read(archive, "META-INF/container.xml"))
            rootfile = next(
                (node for node in container.iter() if _local(node.tag) == "rootfile" and node.attrib.get("full-path")),
                None,
            )
            if rootfile is None:
                raise ValueError("EPUB package document is missing")
            package_name = _safe_name(rootfile.attrib["full-path"])
            package = self._xml(self._read(archive, package_name))
            for node in package.iter():
                tag = _local(node.tag)
                if tag == "title" and not self.title:
                    self.title = " ".join("".join(node.itertext()).split())[:500]
                elif tag == "creator" and not self.author:
                    self.author = " ".join("".join(node.itertext()).split())[:500]
                elif tag == "meta":
                    name = str(node.attrib.get("name", "")).strip().casefold()
                    content = str(node.attrib.get("content", "")).strip()
                    if name == "cover" and content and not self.cover_item:
                        self.cover_item = content[:500]
                elif tag == "item":
                    item_id = str(node.attrib.get("id", "")).strip()
                    href = str(node.attrib.get("href", "")).strip()
                    if item_id and href:
                        properties = str(node.attrib.get("properties", ""))[:500]
                        self.manifest[item_id] = {
                            "path": _join(package_name, href),
                            "media_type": str(node.attrib.get("media-type", ""))[:200],
                            "properties": properties,
                        }
                        if "cover-image" in properties.split() and not self.cover_item:
                            self.cover_item = item_id
                elif tag == "itemref":
                    ref = str(node.attrib.get("idref", "")).strip()
                    if ref:
                        self.spine.append(ref)
            self.spine = self.spine[:MAX_CHAPTERS]
            if not self.spine:
                raise ValueError("EPUB reading order is empty")

    def metadata(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "author": self.author,
            "chapters": len(self.spine),
        }

    def cover(self) -> tuple[bytes, str] | None:
        item = self.manifest.get(self.cover_item)
        if not item:
            return None
        media_type = str(item.get("media_type", "")).casefold()
        if media_type not in SAFE_COVER_TYPES:
            return None
        with zipfile.ZipFile(BytesIO(self.raw)) as archive:
            payload = self._read(archive, item["path"])
        if len(payload) > MAX_COVER_BYTES:
            raise ValueError("EPUB cover exceeds the supported size")
        return payload, media_type

    def chapter(self, index: int) -> dict[str, Any]:
        if index < 0 or index >= len(self.spine):
            raise KeyError(index)
        item_id = self.spine[index]
        item = self.manifest.get(item_id)
        if not item:
            raise ValueError("EPUB spine references a missing manifest item")
        media_type = item.get("media_type", "")
        if media_type not in {"application/xhtml+xml", "text/html", "application/xml"}:
            raise ValueError("EPUB chapter has an unsupported media type")
        with zipfile.ZipFile(BytesIO(self.raw)) as archive:
            root = self._xml(self._read(archive, item["path"]))
        title = ""
        for node in root.iter():
            if _local(node.tag) in {"h1", "h2", "title"}:
                title = " ".join("".join(node.itertext()).split())[:500]
                if title:
                    break
        text = _text(root)
        # Render text, not publisher HTML: this blocks scripts, external URLs,
        # tracking pixels, CSS and active content while keeping reflow semantics.
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        body = "".join(f"<p>{html.escape(part)}</p>" for part in paragraphs)
        return {
            "index": index,
            "id": item_id,
            "path": item["path"],
            "title": title or f"Kapitel {index + 1}",
            "html": body,
            "text_length": len(text),
            "position_anchor": f"chapter:{item_id}",
        }

    def toc(self) -> list[dict[str, Any]]:
        result = []
        for index, item_id in enumerate(self.spine):
            item = self.manifest.get(item_id)
            if not item:
                continue
            result.append({
                "index": index,
                "chapter": item_id,
                "title": f"Kapitel {index + 1}",
            })
        return result
