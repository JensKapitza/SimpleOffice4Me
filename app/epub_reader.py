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
MAX_XML_DEPTH = 256
MAX_XML_NODES = 50_000
SAFE_COVER_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


class _XmlStructureLimitError(ValueError):
    """Raised when untrusted EPUB XML exceeds bounded parser structure."""


class _BoundedTreeBuilder(ElementTree.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.nodes = 0

    def start(self, tag, attrs):
        self.depth += 1
        self.nodes += 1
        if self.depth > MAX_XML_DEPTH:
            raise _XmlStructureLimitError("EPUB XML nesting exceeds the supported limit")
        if self.nodes > MAX_XML_NODES:
            raise _XmlStructureLimitError("EPUB XML contains too many elements")
        return super().start(tag, attrs)

    def end(self, tag):
        element = super().end(tag)
        self.depth -= 1
        return element


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
        self.nav_item = ""
        self.ncx_item = ""
        self._navigation: list[dict[str, Any]] = []
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
            parser = SafeET.DefusedXMLParser(target=_BoundedTreeBuilder())
            parser.feed(data)
            return parser.close()
        except _XmlStructureLimitError:
            raise
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
                        if "nav" in properties.split() and not self.nav_item:
                            self.nav_item = item_id
                        if (
                            str(node.attrib.get("media-type", "")).casefold()
                            == "application/x-dtbncx+xml"
                            and not self.ncx_item
                        ):
                            self.ncx_item = item_id
                elif tag == "itemref":
                    ref = str(node.attrib.get("idref", "")).strip()
                    if ref:
                        self.spine.append(ref)
                elif tag == "spine":
                    self.ncx_item = self.ncx_item or str(node.attrib.get("toc", "")).strip()
            self.spine = self.spine[:MAX_CHAPTERS]
            if not self.spine:
                raise ValueError("EPUB reading order is empty")
            self._navigation = self._load_navigation(archive)

    def _load_navigation(self, archive: zipfile.ZipFile) -> list[dict[str, Any]]:
        """Read an EPUB 3 nav document or EPUB 2 NCX into spine positions."""
        spine_indexes = {}
        for index, item_id in enumerate(self.spine):
            item = self.manifest.get(item_id)
            if item:
                spine_indexes[item["path"]] = index

        def add_link(rows: list[dict[str, Any]], source_path: str, href: str, title: str) -> None:
            if len(rows) >= MAX_CHAPTERS or not href or not title:
                return
            try:
                target_path = _join(source_path, href)
            except ValueError:
                return
            index = spine_indexes.get(target_path)
            if index is None:
                return
            item_id = self.spine[index]
            if rows and rows[-1]["index"] == index:
                return
            rows.append({"index": index, "chapter": item_id, "title": title[:500]})

        if self.nav_item:
            item = self.manifest.get(self.nav_item)
            if item:
                try:
                    root = self._xml(self._read(archive, item["path"]))
                    navs = [node for node in root.iter() if _local(node.tag) == "nav"]
                    toc_nav = next(
                        (
                            node for node in navs
                            if any(
                                _local(str(key)) == "type" and "toc" in str(value).split()
                                for key, value in node.attrib.items()
                            )
                        ),
                        navs[0] if navs else None,
                    )
                    if toc_nav is not None:
                        rows: list[dict[str, Any]] = []
                        for node in toc_nav.iter():
                            if _local(node.tag) != "a":
                                continue
                            add_link(
                                rows,
                                item["path"],
                                str(node.attrib.get("href", "")),
                                _text(node),
                            )
                        if rows:
                            return rows
                except (ValueError, KeyError, OSError, RuntimeError, zipfile.BadZipFile):
                    pass

        ncx = self.manifest.get(self.ncx_item)
        if ncx:
            try:
                root = self._xml(self._read(archive, ncx["path"]))
                rows = []
                for node in root.iter():
                    if _local(node.tag) != "navpoint":
                        continue
                    content = next(
                        (child for child in node if _local(child.tag) == "content"), None
                    )
                    label = next(
                        (child for child in node if _local(child.tag) == "navlabel"), None
                    )
                    if content is None or label is None:
                        continue
                    text_node = next(
                        (child for child in label.iter() if _local(child.tag) == "text"), None
                    )
                    title = _text(text_node) if text_node is not None else ""
                    add_link(
                        rows,
                        ncx["path"],
                        str(content.attrib.get("src", "")),
                        title,
                    )
                if rows:
                    return rows
            except (ValueError, KeyError, OSError, RuntimeError, zipfile.BadZipFile):
                pass
        return []

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
        if self._navigation:
            return [dict(row) for row in self._navigation]
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
