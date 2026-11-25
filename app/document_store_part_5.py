"""DocumentStore implementation part 5 of 5."""
from __future__ import annotations

import os
import threading
import time

from .sqlite_utils import connect as sqlite_connect

from .document_store_core import *  # noqa: F401,F403
from .file_lock import exclusive_file_lock
from .ocr_preprocessing import OCR_RENDER_EDGE, jpeg_ocr_input

MAX_EML_INDEX_BYTES = 25 * 1024 * 1024
MAX_MANUAL_ANALYSIS_BYTES = 128 * 1024 * 1024


class _DocumentStorePart5:
    @staticmethod
    def _run_bounded_pdftotext(executable: str, path: Path) -> str:
        process = subprocess.Popen(
            [executable, "-layout", str(path), "-"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, close_fds=True,
        )
        chunks: list[bytes] = []
        total = 0
        exceeded = threading.Event()

        def collect() -> None:
            nonlocal total
            assert process.stdout is not None
            while chunk := process.stdout.read(64 * 1024):
                if total + len(chunk) > MAX_INDEXED_TEXT_CHARACTERS * 4:
                    exceeded.set()
                    process.kill()
                    return
                chunks.append(chunk)
                total += len(chunk)

        reader = threading.Thread(target=collect, name="simpleoffice-pdftotext", daemon=True)
        reader.start()
        try:
            process.wait(timeout=90)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            reader.join(timeout=2)
            if process.stdout is not None:
                process.stdout.close()
            raise RuntimeError("PDF text extraction timed out after 90 seconds") from exc
        reader.join(timeout=2)
        if reader.is_alive():
            process.kill()
            if process.stdout is not None:
                process.stdout.close()
            reader.join(timeout=2)
            raise RuntimeError("PDF text extraction did not close its output stream")
        if process.stdout is not None:
            process.stdout.close()
        if exceeded.is_set():
            raise RuntimeError("PDF text extraction exceeds the index output limit")
        if process.returncode != 0:
            return ""
        return b"".join(chunks).decode("utf-8", errors="replace")[:MAX_INDEXED_TEXT_CHARACTERS]

    @staticmethod
    def _pdf_text(path: Path) -> str:
        if path.stat().st_size > MAX_INDEXED_DOCUMENT_BYTES:
            raise RuntimeError("PDF exceeds the document-index size limit")
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            if len(reader.pages) > MAX_INDEXED_PDF_PAGES:
                raise RuntimeError("PDF exceeds the document-index page limit")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("PDF page count could not be read") from exc
        executable = shutil.which("pdftotext")
        if executable:
            text = _DocumentStorePart5._run_bounded_pdftotext(executable, path)
            if text.strip():
                return "\n".join(line.rstrip() for line in text.splitlines()).strip()

        # GitHub Actions and portable installations do not necessarily provide
        # Poppler's pdftotext binary. Keep a pure-Python fallback available.
        try:
            python_text_parts = []
            characters = 0
            for page in reader.pages:
                remaining = MAX_INDEXED_TEXT_CHARACTERS - characters
                if remaining <= 0:
                    break
                page_parts: list[str] = []
                page_characters = 0

                def collect_text(text, *_args):
                    nonlocal page_characters
                    if page_characters >= remaining:
                        return
                    fragment = str(text or "")[:remaining - page_characters]
                    page_parts.append(fragment)
                    page_characters += len(fragment)

                try:
                    page.extract_text(visitor_text=collect_text)
                    chunk = "".join(page_parts)
                except TypeError:
                    # Older supported pypdf versions lack visitor_text.
                    chunk = (page.extract_text() or "")[:remaining]
                python_text_parts.append(chunk)
                characters += min(len(chunk), remaining)
                if characters >= MAX_INDEXED_TEXT_CHARACTERS:
                    break
            python_text = "\n".join(python_text_parts).strip()
            if python_text or not executable:
                return python_text
            return ""
        except Exception as exc:
            if executable:
                raise RuntimeError("PDF text extraction failed") from exc
            raise RuntimeError("PDF text extraction requires pypdf when pdftotext is unavailable") from exc

    def _pdf_image_ocr(self, path: Path) -> dict[str, Any]:
        return self._pdf_rendered_ocr(path)

    def _pdf_page_ocr(
        self, source: Path, target: Path, tesseract: str | None,
    ) -> tuple[str, list[str]]:
        from .object_vision import analyze_ocr
        from .ocr_preprocessing import needs_ocr_supplement, prepare_ocr_image

        prepare_ocr_image(source, target)
        ml_result = analyze_ocr(target, fallback_tesseract=False)
        ml_text = str(ml_result.get("text") or "").strip()
        if ml_result.get("status") == "completed" and ml_text:
            if not tesseract or not needs_ocr_supplement(ml_text, ml_result.get("confidence")):
                return ml_text, ["rapidocr"]
            try:
                tesseract_text = self._image_ocr(target, timeout=30)
            except (OSError, RuntimeError, ValueError):
                return ml_text, ["rapidocr"]
            if not tesseract_text:
                return ml_text, ["rapidocr"]
            lines = ml_text.splitlines()
            known = {" ".join(line.split()).casefold() for line in lines if line.strip()}
            for line in tesseract_text.splitlines():
                normalized = " ".join(line.split()).casefold()
                if line.strip() and normalized not in known:
                    lines.append(line.rstrip())
                    known.add(normalized)
            return "\n".join(lines).strip(), ["rapidocr", "tesseract"]
        if not tesseract:
            raise RuntimeError("RapidOCR hat keinen Text erkannt und Tesseract ist nicht installiert")
        return self._image_ocr(target, timeout=30), ["tesseract"]

    def _pdf_rendered_ocr(self, path: Path) -> dict[str, Any]:
        renderer = shutil.which("pdftoppm")
        tesseract = shutil.which("tesseract")
        if not renderer:
            return {"status": "unavailable", "text": "", "engines": [], "pages_total": 0,
                    "pages_processed": 0, "pages_with_text": 0, "failed_pages": 0,
                    "error": "Poppler pdftoppm is not installed"}
        try:
            from pypdf import PdfReader

            page_count = len(PdfReader(str(path)).pages)
        except Exception as exc:
            return {"status": "failed", "text": "", "engines": [], "pages_total": 0,
                    "pages_processed": 0, "pages_with_text": 0, "failed_pages": 0,
                    "error": f"PDF page count could not be read ({type(exc).__name__})"}
        if page_count < 1:
            return {"status": "failed", "text": "", "engines": [], "pages_total": 0,
                    "pages_processed": 0, "pages_with_text": 0, "failed_pages": 0,
                    "error": "PDF contains no pages"}
        if page_count > MAX_INDEXED_PDF_PAGES:
            return {"status": "limited", "text": "", "engines": [], "pages_total": page_count,
                    "pages_processed": 0, "pages_with_text": 0, "failed_pages": 0,
                    "error": "PDF exceeds the document-index page limit"}
        deadline = time.monotonic() + 180
        with tempfile.TemporaryDirectory(prefix="simpleoffice-pdf-ocr-") as temp:
            page_text: dict[int, str] = {}
            indexed_characters = 0
            failed_pages: set[int] = set()
            engines: set[str] = set()
            batch_size = 10
            for first_page in range(1, page_count + 1, batch_size):
                if time.monotonic() >= deadline:
                    failed_pages.update(range(first_page, page_count + 1))
                    break
                last_page = min(page_count, first_page + batch_size - 1)

                def render(start: int, end: int) -> list[Path]:
                    prefix = Path(temp) / f"page-{start}-{end}"
                    pattern = f"{prefix.name}-*.png"
                    try:
                        result = subprocess.run(
                            [renderer, "-f", str(start), "-l", str(end), "-scale-to", str(OCR_RENDER_EDGE), "-gray", "-png", "--", str(path), str(prefix)],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            timeout=max(1, min(30, int(deadline - time.monotonic()))), check=False,
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        for image_path in Path(temp).glob(pattern):
                            image_path.unlink(missing_ok=True)
                        return []
                    images = sorted(Path(temp).glob(pattern))
                    if result.returncode != 0:
                        for image_path in images:
                            image_path.unlink(missing_ok=True)
                        return []
                    return images

                images = render(first_page, last_page)
                # A bad page should not prevent later pages from being OCRed.
                # Retry a failed batch one page at a time, retaining the same
                # per-render time limit and continuing after individual errors.
                if len(images) != last_page - first_page + 1:
                    for image_path in images:
                        image_path.unlink(missing_ok=True)
                    images = []
                    page_images: list[tuple[int, Path]] = []
                    for page in range(first_page, last_page + 1):
                        rendered_page = render(page, page)
                        if len(rendered_page) != 1:
                            failed_pages.add(page)
                        else:
                            page_images.append((page, rendered_page[0]))
                else:
                    page_images = [
                        (first_page + offset, image_path)
                        for offset, image_path in enumerate(images)
                    ]

                for page_number, image_path in page_images:
                    if time.monotonic() >= deadline:
                        failed_pages.update(range(page_number, page_count + 1))
                        break
                    enhanced_path = Path(temp) / f"ocr-{page_number:06d}.png"
                    try:
                        text, page_engines = self._pdf_page_ocr(image_path, enhanced_path, tesseract)
                        engines.update(page_engines)
                        remaining_text = MAX_INDEXED_TEXT_CHARACTERS - indexed_characters
                        if remaining_text > 0:
                            page_text[page_number] = text[:remaining_text]
                            indexed_characters += len(page_text[page_number])
                        else:
                            failed_pages.update(range(page_number, page_count + 1))
                            break
                    except (OSError, RuntimeError, ValueError):
                        failed_pages.add(page_number)
                    finally:
                        image_path.unlink(missing_ok=True)
                        enhanced_path.unlink(missing_ok=True)

        texts: list[str] = []
        character_count = 0
        for number in range(1, page_count + 1):
            page = page_text.get(number, "")
            if not page:
                continue
            remaining = MAX_INDEXED_TEXT_CHARACTERS - character_count
            if remaining <= 0:
                break
            texts.append(page[:remaining])
            character_count += min(len(page), remaining)
        processed = len(page_text)
        return {
            "status": "partial" if failed_pages else "completed",
            "text": "\n".join(texts),
            "engines": sorted(engines),
            "pages_total": page_count,
            "pages_processed": processed,
            "pages_with_text": len(texts),
            "failed_pages": len(failed_pages),
            "error": "Some PDF pages could not be rendered or recognized" if failed_pages else "",
        }

    @staticmethod
    def _file_text(path: Path) -> tuple[str, str]:
        suffix = path.suffix.lower()
        if suffix == ".eml":
            from email import policy
            from email.parser import BytesParser

            from .mail_reader import _header, _message_text

            try:
                if path.stat().st_size > MAX_EML_INDEX_BYTES:
                    raise RuntimeError("EML exceeds text-index size limit")
                with path.open("rb") as source:
                    message = BytesParser(policy=policy.default).parse(source)
            except OSError as exc:
                raise RuntimeError("EML text extraction could not read the source") from exc
            fields = (
                ("Subject", message.get("Subject")),
                ("From", message.get("From")),
                ("To", message.get("To")),
                ("Cc", message.get("Cc")),
                ("Date", message.get("Date")),
                ("Message-ID", message.get("Message-ID")),
            )
            header_text = "\n".join(
                f"{name}: {value}"
                for name, raw in fields
                if (value := _header(raw))
            )
            body_text = _message_text(message)[:MAX_INDEXED_TEXT_CHARACTERS]
            return "\n\n".join(part for part in (header_text, body_text) if part), "eml"
        if suffix in {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".log", ".ics", ".vcf", ".py", ".java", ".js", ".css", ".sql", ".yml", ".yaml"}:
            with path.open("rb") as source:
                raw = source.read(MAX_INDEXED_DOCUMENT_BYTES + 1)
            if len(raw) > MAX_INDEXED_DOCUMENT_BYTES:
                raise RuntimeError("text file exceeds the document-index size limit")
            text = raw[:MAX_INDEXED_DOCUMENT_BYTES].decode("utf-8", errors="replace")
            return text[:MAX_INDEXED_TEXT_CHARACTERS], "plain_text"
        if suffix in {".docx", ".odt", ".xlsx", ".ods"}:
            try:
                if path.stat().st_size > MAX_INDEXED_DOCUMENT_BYTES:
                    raise RuntimeError("office document exceeds the source size limit")
                with zipfile.ZipFile(path) as archive:
                    entries = archive.infolist()
                    if len(entries) > MAX_OFFICE_ARCHIVE_ENTRIES:
                        raise RuntimeError("office document exceeds the XML entry limit")
                    text_parts = []
                    expanded_bytes = 0
                    extracted_characters = 0
                    for entry in entries:
                        name = entry.filename
                        if not name.endswith(".xml") or name.startswith("docProps/"):
                            continue
                        expanded_bytes += entry.file_size
                        if entry.file_size > MAX_OFFICE_MEMBER_BYTES or expanded_bytes > MAX_OFFICE_EXPANDED_BYTES:
                            raise RuntimeError("office document exceeds the expanded XML size limit")
                        try:
                            root = DefusedElementTree.fromstring(archive.read(entry))
                            for value in root.itertext():
                                value = value.strip()
                                if not value:
                                    continue
                                remaining = MAX_INDEXED_TEXT_CHARACTERS - extracted_characters
                                if remaining <= 0:
                                    break
                                text_parts.append(value[:remaining])
                                extracted_characters += min(len(value), remaining)
                            if extracted_characters >= MAX_INDEXED_TEXT_CHARACTERS:
                                break
                        except (ElementTree.ParseError, DefusedXmlException):
                            continue
                return "\n".join(text_parts), "office_zip"
            except RuntimeError:
                raise
            except (OSError, zipfile.BadZipFile) as exc:
                raise RuntimeError(f"office document extraction failed: {exc}") from exc
        return "", "unsupported"

    def _apply_image_analysis(self, path: Path, metadata: dict[str, Any], force: bool = False) -> None:
        """Keep analysis local; failures are recorded with the document, not hidden."""
        current = metadata.get("image_analysis", {})
        if not force and current.get("source_sha256") == metadata.get("sha256"):
            return
        analysis: dict[str, Any] = {"source_sha256": metadata.get("sha256", ""), "analyzed_at": utc_now(), "exif": {}, "ocr_status": "not_run"}
        generated_tags = {"bild", f"format-{path.suffix.lower().lstrip('.')}"}
        try:
            from PIL import ExifTags, Image
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                analysis["format"] = image.format or path.suffix.lstrip(".").upper()
                analysis["width"], analysis["height"] = image.size
                exif: dict[str, Any] = {}
                raw_exif = image.getexif()
                for key, value in raw_exif.items():
                    label = ExifTags.TAGS.get(key, str(key))
                    if label in {"Make", "Model", "Software", "DateTime", "DateTimeOriginal", "DateTimeDigitized", "Orientation"}:
                        exif[label] = str(value)
                    elif label == "GPSInfo" and value:
                        gps = self._gps_data(value, ExifTags.GPSTAGS)
                        if gps:
                            exif["GPS"] = gps
                analysis["exif"] = exif
                camera = " ".join(part for part in (exif.get("Make", ""), exif.get("Model", "")) if part).strip()
                if camera:
                    generated_tags.add(f"kamera-{self._tag_token(camera)}")
                date_value = exif.get("DateTimeOriginal") or exif.get("DateTime")
                if date_value and len(date_value) >= 4 and date_value[:4].isdigit():
                    generated_tags.add(f"jahr-{date_value[:4]}")
        except ImportError:
            analysis["metadata_error"] = "Pillow is not installed"
        except (OSError, ValueError, SyntaxError) as exc:
            analysis["metadata_error"] = str(exc)

        from .ocr_preprocessing import photo_ocr_input

        try:
            with photo_ocr_input(path) as source:
                try:
                    ocr_text = self._image_ocr(source)
                except (OSError, RuntimeError) as exc:
                    if source == path:
                        raise
                    raise RuntimeError("Local OCR failed for the prepared grayscale PNG.") from exc
            metadata["ocr_text"] = ocr_text
            analysis["ocr_status"] = "completed"
            analysis["ocr_characters"] = len(ocr_text)
            generated_tags.update(self._ocr_tags(ocr_text))
        except RuntimeError as exc:
            analysis["ocr_status"] = "unavailable"
            analysis["ocr_error"] = str(exc)
        metadata["image_analysis"] = analysis
        metadata["tags"] = sorted({*metadata.get("tags", []), *generated_tags}, key=str.casefold)

    def refresh_document_analysis(
        self,
        reference: str | Path,
        actor: str,
        *,
        max_bytes: int = MAX_MANUAL_ANALYSIS_BYTES,
    ) -> dict[str, Any]:
        """Refresh bounded OCR/text analysis without changing original bytes."""
        self._require_actor(actor)
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("analysis size limit must be a positive byte count")
        with exclusive_file_lock(self.control / ".document-content.lock"):
            metadata = self.get_document(reference)
            self._require_document_editable(metadata)
            path = resolve_file_under(self.root, str(metadata.get("last_path", "")))
            if path.stat().st_size > min(max_bytes, MAX_MANUAL_ANALYSIS_BYTES):
                raise ValueError("document exceeds the manual analysis size limit")
            expected_digest = str(metadata.get("sha256", ""))
            if not expected_digest or not hmac.compare_digest(sha256_file(path), expected_digest):
                raise ValueError("document content changed before analysis")
            if self._is_image(path):
                self._apply_image_analysis(path, metadata, force=True)
            self._apply_document_text_extraction(path, metadata, force=True)
            if not hmac.compare_digest(sha256_file(path), expected_digest):
                raise ValueError("document content changed during analysis")
            self._save_document(metadata)
            self._refresh_search_index(metadata)
            details = {"document_id": metadata["document_id"], "actor": actor}
            self._event("document_analysis_refreshed", details)
            self._record_revision(
                "document_analysis_refreshed", actor, "documents",
                metadata["document_id"], details,
            )
            return metadata

    @staticmethod
    def _gps_data(value: Any, labels: dict[int, str]) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {"present": True}
        data = {labels.get(key, str(key)): str(item) for key, item in value.items()}
        return {"present": True, **data}

    @staticmethod
    def _tag_token(value: str) -> str:
        token = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
        return token[:64] or "unbekannt"

    @classmethod
    def _ocr_tags(cls, text: str) -> set[str]:
        ignored = {"aber", "alle", "auch", "dass", "der", "die", "das", "den", "dem", "des", "eine", "einer", "einem", "einen", "für", "mit", "nach", "oder", "und", "von", "zum", "zur", "this", "that", "with", "from", "your"}
        words = [cls._tag_token(word) for word in re.findall(r"[A-Za-zÄÖÜäöüß0-9]{4,}", text)]
        unique = list(dict.fromkeys(word for word in words if word not in ignored and word != "unbekannt" and not word.isdigit() and len(word) >= 4))
        return set(unique[:12])

    @staticmethod
    def _image_ocr(path: Path, timeout: int = 90) -> str:
        executable = shutil.which("tesseract")
        if not executable:
            raise RuntimeError("Tesseract OCR is not installed")
        environment = ocr_subprocess_environment()
        try:
            with jpeg_ocr_input(path) as source:
                result = subprocess.run([executable, str(source), "stdout", "-l", "deu+eng"], capture_output=True, text=True, timeout=timeout, check=False, env=environment)
                if result.returncode != 0 and "deu" in result.stderr.lower():
                    result = subprocess.run([executable, str(source), "stdout", "-l", "eng"], capture_output=True, text=True, timeout=timeout, check=False, env=environment)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"OCR timed out after {timeout} seconds") from exc
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Tesseract OCR failed")
        return "\n".join(line.rstrip() for line in result.stdout.splitlines()).strip()

    def _save_document(self, metadata: dict[str, Any]) -> None:
        atomic_json_write(self.documents / f"{metadata['document_id']}.json", metadata)
        relative_path = str(metadata.get("last_path", ""))
        if relative_path and not relative_path.startswith("[external]"):
            document_path = self.root / relative_path
            if document_path.is_file() and not document_path.is_symlink():
                atomic_json_write(document_path.parent / CONTROL_DIR / f"{metadata['document_id']}.json", metadata)
                self._write_context_xattrs(document_path, metadata)

    def _write_note_snapshot(self, document: dict[str, Any], note: dict[str, Any]) -> Path:
        """Create once; a note itself is immutable, so its PDF is a stable snapshot."""
        path = self.note_snapshots / f"{note['id']}.pdf"
        if path.exists():
            return path
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.units import mm
            from reportlab.pdfgen.canvas import Canvas
        except ImportError as exc:
            raise RuntimeError("reportlab is required for note PDF snapshots") from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        canvas = Canvas(str(temporary), pagesize=A4, pageCompression=1)
        width, height = A4
        x, y = 20 * mm, height - 20 * mm
        canvas.setFont("Helvetica-Bold", 14)
        canvas.drawString(x, y, "SimpleOffice4Me - Notiz-Snapshot")
        y -= 10 * mm
        canvas.setFont("Helvetica", 9)
        for line in (f"Dokument: {document.get('last_path', '')}", f"Dokument-ID: {document['document_id']}", f"Notiz-ID: {note['id']}", f"Autor: {note.get('author', '')}", f"Erstellt: {note.get('created_at', '')}"):
            canvas.drawString(x, y, line[:150])
            y -= 5 * mm
        y -= 4 * mm
        canvas.setFont("Helvetica", 11)
        words = note.get("text", "").split()
        line = ""
        for word in words or [""]:
            candidate = f"{line} {word}".strip()
            if canvas.stringWidth(candidate, "Helvetica", 11) > width - 40 * mm:
                canvas.drawString(x, y, line)
                y -= 6 * mm
                if y < 20 * mm:
                    canvas.showPage(); y = height - 20 * mm; canvas.setFont("Helvetica", 11)
                line = word
            else:
                line = candidate
        canvas.drawString(x, y, line)
        canvas.save()
        temporary.replace(path)
        return path

    @staticmethod
    def _write_context_xattrs(path: Path, metadata: dict[str, Any]) -> None:
        if not hasattr(os, "setxattr"):
            return
        try:
            os.setxattr(path, "user.simpleoffice.state", str(metadata.get("state", "new")).encode("utf-8"))
            notes = json.dumps(metadata.get("notes", []), ensure_ascii=False).encode("utf-8")
            if len(notes) <= 2048:
                os.setxattr(path, "user.simpleoffice.notes", notes)
            else:
                os.setxattr(path, "user.simpleoffice.notes_ref", f"{CONTROL_DIR}/{metadata['document_id']}.json".encode("utf-8"))
        except OSError:
            return

    def _record_revision(self, action: str, actor: str, category: str, key: str, snapshot: dict[str, Any]) -> None:
        commit = self.history.record(action, actor, category, key, snapshot)
        self._event("revision_recorded", {"action": action, "actor": actor, "commit": commit, "key": key})

    @staticmethod
    def _require_actor(actor: str) -> None:
        if not actor.strip():
            raise ValueError("a named user is required for every write action")

    def _deadline_rules(
        self, metadata: dict[str, Any]
    ) -> list[tuple[dict[str, Any], str, str]]:
        rules: list[tuple[dict[str, Any], str, str]] = [
            (rule, "document", metadata["document_id"])
            for rule in metadata.get("deadlines", [])
            if isinstance(rule, dict)
        ]
        relative_path = Path(str(metadata.get("last_path", "")))
        if relative_path.is_absolute() or str(relative_path).startswith("[external]"):
            return rules
        document_folder = (self.root / relative_path).parent.resolve()
        try:
            document_folder.relative_to(self.root)
        except ValueError:
            return rules
        folders = [self.root]
        current = self.root
        for part in document_folder.relative_to(self.root).parts:
            current = current / part
            folders.append(current)
        for folder in folders:
            policy = self._read_json(folder / POLICY_FILE, {})
            retention = policy.get("retention", {})
            configured = retention.get("rules", []) if isinstance(retention, dict) else []
            source = self.relative(folder)
            rules.extend(
                (rule, "folder", source) for rule in configured if isinstance(rule, dict)
            )
        return rules

    def _require_document_editable(self, metadata: dict[str, Any]) -> None:
        if metadata.get("cleanup_state") == "staged":
            raise ValueError("document is staged for manual deletion and cannot be edited")
        status = self.retention_status(metadata["document_id"])
        if status["work_locked"]:
            raise ValueError(
                f"document is locked since {status['work_until']}; only deadline and cleanup actions remain allowed"
            )

    def _refresh_search_index(self, metadata: dict[str, Any]) -> None:
        row = (
            metadata["document_id"],
            metadata.get("last_path", ""),
            metadata.get("state", ""),
            " ".join(metadata.get("tags", [])),
            "\n".join(note.get("text", "") for note in metadata.get("notes", [])),
            json.dumps(metadata.get("attributes", {}), ensure_ascii=False),
            "\n".join(part for part in (metadata.get("extracted_text", ""), metadata.get("ocr_text", "")) if part),
        )
        with self._db() as db:
            updated = db.execute(
                "UPDATE document_search SET path=?, state=?, tags=?, notes=?, attributes=?, content=? WHERE document_id=?",
                (*row[1:], row[0]),
            )
            if updated.rowcount == 0:
                db.execute(
                    "INSERT INTO document_search(document_id, path, state, tags, notes, attributes, content) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    row,
                )
        self._refresh_listing_index(metadata)

    def _refresh_listing_index(
        self,
        metadata: dict[str, Any],
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Update the small projection used by login, dashboard and inbox."""
        def update(db: sqlite3.Connection) -> None:
            db.execute(
                """INSERT INTO document_listing(
                       document_id, path, state, has_notes, has_relationships, last_seen_at,
                       version_series_id, version_number
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(document_id) DO UPDATE SET
                     path=excluded.path, state=excluded.state,
                     has_notes=excluded.has_notes,
                     has_relationships=excluded.has_relationships,
                     last_seen_at=excluded.last_seen_at,
                     version_series_id=excluded.version_series_id,
                     version_number=excluded.version_number""",
                (
                    metadata["document_id"],
                    str(metadata.get("last_path", "")),
                    str(metadata.get("state", "new") or "new"),
                    int(bool(metadata.get("notes"))),
                    int(bool(metadata.get("relationships"))),
                    str(metadata.get("last_seen_at", "")),
                    str(metadata.get("version_series_id", metadata["document_id"])),
                    int(metadata.get("version_number", 1)),
                ),
            )
            db.execute("DELETE FROM document_relationship WHERE source_id = ?", (metadata["document_id"],))
            db.executemany(
                """INSERT OR REPLACE INTO document_relationship(
                       source_id, target_id, propagates_retention
                   ) VALUES (?, ?, ?)""",
                [
                    (
                        metadata["document_id"],
                        str(link["target_document_id"]),
                        int(link.get("propagates_retention") is True),
                    )
                    for link in metadata.get("relationships", [])
                    if isinstance(link, dict) and link.get("target_document_id")
                ],
            )
        if connection is not None:
            update(connection)
            return
        with self._db() as db:
            update(db)

    def _all_documents(self) -> list[dict[str, Any]]:
        self.initialize()
        return [
            metadata
            for path in self.documents.glob("*.json")
            if (metadata := self._read_json(path, {})).get("document_id")
        ]

    @staticmethod
    def _mounted_roots() -> list[Path]:
        """Return mounted volume roots without recursively scanning drives."""
        roots: set[Path] = set()
        if sys.platform.startswith("win"):
            try:
                import ctypes
                mask = ctypes.windll.kernel32.GetLogicalDrives()
                for index in range(26):
                    if mask & (1 << index):
                        roots.add(Path(f"{chr(65 + index)}:/"))
            except (AttributeError, OSError):
                pass
        elif sys.platform == "darwin":
            roots.add(Path("/Volumes"))
            if Path("/Volumes").is_dir():
                roots.update(path for path in Path("/Volumes").iterdir() if path.is_dir())
        else:
            try:
                for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
                    fields = line.split()
                    if len(fields) > 1:
                        roots.add(Path(fields[1].replace("\\040", " ")))
            except OSError:
                roots.update(path for path in (Path("/media"), Path("/mnt")) if path.is_dir())
        return sorted((path for path in roots if path.is_dir()), key=str)

    def _db(self) -> sqlite3.Connection:
        self.control.mkdir(parents=True, exist_ok=True)
        # The scanner and web requests use short independent connections. WAL
        # permits readers while the scanner updates the index; the timeout also
        # prevents transient writer contention from becoming an HTTP 500.
        connection = sqlite_connect(self.index_path, timeout=15)
        connection.execute("PRAGMA busy_timeout = 15000")
        if self.index_path not in _WAL_CONFIGURED_INDEXES:
            with _WAL_CONFIGURATION_LOCK:
                if self.index_path not in _WAL_CONFIGURED_INDEXES:
                    connection.execute("PRAGMA journal_mode = WAL")
                    _WAL_CONFIGURED_INDEXES.add(self.index_path)
        return connection

    def _event(self, event_type: str, data: dict[str, Any]) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        record = {"at": utc_now(), "type": event_type, **data}
        with self.events.open("a", encoding="utf-8") as destination:
            destination.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    @staticmethod
    def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else default
        except (OSError, json.JSONDecodeError):
            return default

    @staticmethod
    def _read_xattrs(path: Path) -> dict[str, Any]:
        if not hasattr(os, "getxattr"):
            return {}
        try:
            result: dict[str, Any] = {}
            for key, name in (("document_id", "user.simpleoffice.id"), ("sha256", "user.simpleoffice.sha256"), ("tags", "user.simpleoffice.tags")):
                try:
                    value = os.getxattr(path, name).decode("utf-8")
                    result[key] = json.loads(value) if key == "tags" else value
                except OSError:
                    pass
            return result
        except OSError:
            return {}

    @staticmethod
    def _write_xattrs(path: Path, document_id: str, digest: str, tags: list[str]) -> None:
        if not hasattr(os, "setxattr"):
            return
        try:
            os.setxattr(path, "user.simpleoffice.id", document_id.encode("utf-8"))
            os.setxattr(path, "user.simpleoffice.sha256", digest.encode("utf-8"))
            os.setxattr(path, "user.simpleoffice.tags", json.dumps(tags).encode("utf-8"))
        except OSError:
            # FAT, SMB and backup media often do not support xattrs. Sidecars are enough.
            return
