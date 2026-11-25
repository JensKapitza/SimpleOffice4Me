"""Bounded, extraction-free EPUB reader using only local archive members."""
from __future__ import annotations

import html
import posixpath
import re
import unicodedata
import zipfile
from io import BytesIO
from xml.etree import ElementTree
from pathlib import PurePosixPath
from typing import Any

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from .epub_font_metadata import (
    MAX_FONT_METADATA_BYTES, OCF_NAMESPACE, XML_BASE, obfuscated_font_paths, validate_font_declarations,
)

MAX_EPUB_BYTES = 100 * 1024 * 1024
MAX_ENTRIES = 5_000
MAX_ENTRY_BYTES = 20 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 200 * 1024 * 1024
MAX_CHAPTERS = 1_000
MAX_CHAPTER_TEXT = 2 * 1024 * 1024
MAX_COVER_BYTES = 10 * 1024 * 1024
MAX_XML_DEPTH = 256
MAX_XML_NODES = 50_000
EPUB_SPEECH_SEGMENT_CHARS = 1200
_IGNORED_TEXT_TAGS = {"head", "script", "style", "noscript", "template"}
_SPEECH_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li", "p", "pre", "section", "td", "th", "tr",
}
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


def _spoken_text(body: ElementTree.Element) -> str:
    """Flatten only body text; block boundaries separate but inline words stay intact."""
    pieces: list[str] = []

    def visit(node: ElementTree.Element) -> None:
        tag = _local(node.tag)
        if tag in _IGNORED_TEXT_TAGS:
            return
        is_block = tag in _SPEECH_BLOCK_TAGS
        if is_block:
            pieces.append("\n\n")
        if node.text:
            pieces.append(re.sub(r"\s+", " ", node.text))
        for child in node:
            visit(child)
            if child.tail:
                pieces.append(re.sub(r"\s+", " ", child.tail))
        if is_block:
            pieces.append("\n\n")

    visit(body)
    paragraphs = (" ".join(piece.split()) for piece in "".join(pieces).split("\n\n"))
    text = "\n\n".join(piece for piece in paragraphs if piece)
    if len(text) > MAX_CHAPTER_TEXT:
        raise ValueError("EPUB chapter text exceeds the supported limit")
    return text


class EpubBook:
    def __init__(self, raw: bytes):
        if len(raw) > MAX_EPUB_BYTES:
            raise ValueError("EPUB exceeds the supported size")
        self.raw = raw
        self.entries: dict[str, zipfile.ZipInfo] = {}
        self._unused_fonts: set[str] = set()
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
        if safe in self._unused_fonts:
            raise ValueError("Embedded EPUB fonts are not read")
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

    def _detect_font_metadata(self, archive: zipfile.ZipFile) -> set[str]:
        metadata = "META-INF/encryption.xml"
        candidates = [name for name in self.entries if name.casefold() == metadata.casefold()]
        if not candidates:
            return set()
        if (candidates != [metadata] or self.entries[metadata].file_size > MAX_FONT_METADATA_BYTES
                or any(name.casefold() == "meta-inf/rights.xml" for name in self.entries)):
            raise ValueError("Unsupported encrypted EPUB metadata")

        # Do not let case variants or alternate path spellings make the otherwise
        # exact font declaration ambiguous on another reader or platform.
        names: set[str] = set()
        for name, info in self.entries.items():
            canonical = unicodedata.normalize("NFC", name).casefold()
            if canonical in names or info.filename != name:
                raise ValueError("Ambiguous EPUB archive with font metadata")
            names.add(canonical)

        paths = obfuscated_font_paths(self._xml(self._read(archive, metadata)))
        if not paths.issubset(self.entries):
            raise ValueError("Unverified EPUB font reference")
        return paths

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
            self._unused_fonts = self._detect_font_metadata(archive)

            container = self._xml(self._read(archive, "META-INF/container.xml"))
            rootfile = next(
                (node for node in container.iter() if _local(node.tag) == "rootfile" and node.attrib.get("full-path")),
                None,
            )
            if rootfile is None:
                raise ValueError("EPUB package document is missing")
            if self._unused_fonts and (
                container.tag != f"{{{OCF_NAMESPACE}}}container"
                or sum(_local(node.tag) == "rootfile" for node in container.iter()) != 1
                or any(XML_BASE in node.attrib for node in container.iter())
            ):
                raise ValueError("Ambiguous EPUB rendition with font metadata")
            package_name = _safe_name(rootfile.attrib["full-path"])
            if self._unused_fonts and rootfile.attrib["full-path"] != package_name:
                raise ValueError("Ambiguous EPUB package path")
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
                        if self._unused_fonts and item_id in self.manifest:
                            raise ValueError("Ambiguous EPUB manifest declarations")
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
            if self._unused_fonts:
                validate_font_declarations(package, self._unused_fonts, self.manifest)
                reader_items = (*self.spine, self.nav_item, self.ncx_item, self.cover_item)
                if any(self.manifest.get(item, {}).get("path") in self._unused_fonts for item in reader_items):
                    raise ValueError("An obfuscated font was declared as EPUB content")
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
        if self._unused_fonts:
            # The compatibility path is text-only; it never enables embedded asset serving.
            return None
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

    def _chapter_document(self, index: int) -> tuple[str, dict[str, str], ElementTree.Element]:
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
        return item_id, item, root

    @staticmethod
    def _speech_segments(root: Any) -> list[str]:
        body = root if _local(root.tag) == "body" else next(
            (node for node in root if _local(node.tag) == "body"), None,
        )
        if body is None:
            return []
        remaining = _spoken_text(body)
        parts = []
        start = 0
        while start < len(remaining):
            end = min(start + EPUB_SPEECH_SEGMENT_CHARS, len(remaining))
            if end < len(remaining):
                midpoint = start + EPUB_SPEECH_SEGMENT_CHARS // 2
                stop = max(remaining.rfind("\n\n", midpoint, end), remaining.rfind(" ", midpoint, end))
                if stop > start:
                    end = stop
            section = remaining[start:end].strip()
            if section:
                parts.append(section)
            start = end
        return parts

    def speech_segments(self, index: int) -> list[str]:
        _, _, root = self._chapter_document(index)
        return self._speech_segments(root)

    def chapter(self, index: int) -> dict[str, Any]:
        item_id, item, root = self._chapter_document(index)
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
        try:
            speech_count = len(self._speech_segments(root))
        except ValueError:
            # A speech cap must not prevent the existing text view from opening.
            speech_count = 0
        body = "".join(f"<p>{html.escape(part)}</p>" for part in paragraphs)
        return {
            "index": index,
            "id": item_id,
            "path": item["path"],
            "title": title or f"Kapitel {index + 1}",
            "html": body,
            "text_length": len(text),
            "speech_segments": speech_count,
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
