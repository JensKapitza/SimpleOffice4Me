"""DocumentStore implementation part 5 of 5."""
from __future__ import annotations

from .document_store_core import *  # noqa: F401,F403


class _DocumentStorePart5:
    @staticmethod
    def _pdf_text(path: Path) -> str:
        executable = shutil.which("pdftotext")
        if executable:
            try:
                result = subprocess.run([executable, "-layout", str(path), "-"], capture_output=True, text=True, timeout=90, check=False)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("PDF text extraction timed out after 90 seconds") from exc
            if result.returncode == 0:
                return "\n".join(line.rstrip() for line in result.stdout.splitlines()).strip()

        # GitHub Actions and portable installations do not necessarily provide
        # Poppler's pdftotext binary. Keep a pure-Python fallback available.
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except Exception as exc:
            if executable:
                raise RuntimeError(result.stderr.strip() or "PDF text extraction failed") from exc
            raise RuntimeError("PDF text extraction requires pypdf when pdftotext is unavailable") from exc

    def _pdf_image_ocr(self, path: Path) -> str:
        executable = shutil.which("pdfimages")
        if not executable:
            return ""
        with tempfile.TemporaryDirectory(prefix="simpleoffice-pdf-images-") as temp:
            output_prefix = Path(temp) / "image"
            try:
                result = subprocess.run([executable, "-png", str(path), str(output_prefix)], capture_output=True, text=True, timeout=120, check=False)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("PDF image extraction timed out after 120 seconds") from exc
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "PDF image extraction failed")
            texts: list[str] = []
            for image_path in sorted(Path(temp).glob("image-*.png"))[:100]:
                try:
                    text = self._image_ocr(image_path)
                    if text:
                        texts.append(text)
                except RuntimeError:
                    continue
            return "\n".join(texts)

    @staticmethod
    def _file_text(path: Path) -> tuple[str, str]:
        suffix = path.suffix.lower()
        if suffix in {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".log", ".eml", ".ics", ".vcf", ".py", ".java", ".js", ".css", ".sql", ".yml", ".yaml"}:
            return path.read_text(encoding="utf-8", errors="replace"), "plain_text"
        if suffix in {".docx", ".odt", ".xlsx", ".ods"}:
            try:
                with zipfile.ZipFile(path) as archive:
                    text_parts = []
                    for name in archive.namelist():
                        if not name.endswith(".xml") or name.startswith("docProps/"):
                            continue
                        try:
                            root = DefusedElementTree.fromstring(archive.read(name))
                            text_parts.extend(value.strip() for value in root.itertext() if value.strip())
                        except (ElementTree.ParseError, DefusedXmlException):
                            continue
                return "\n".join(text_parts), "office_zip"
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

        try:
            ocr_text = self._image_ocr(path)
            metadata["ocr_text"] = ocr_text
            analysis["ocr_status"] = "completed"
            analysis["ocr_characters"] = len(ocr_text)
            generated_tags.update(self._ocr_tags(ocr_text))
        except RuntimeError as exc:
            analysis["ocr_status"] = "unavailable"
            analysis["ocr_error"] = str(exc)
        metadata["image_analysis"] = analysis
        metadata["tags"] = sorted({*metadata.get("tags", []), *generated_tags}, key=str.casefold)

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
    def _image_ocr(path: Path) -> str:
        executable = shutil.which("tesseract")
        if not executable:
            raise RuntimeError("Tesseract OCR is not installed")
        environment = ocr_subprocess_environment()
        try:
            result = subprocess.run([executable, str(path), "stdout", "-l", "deu+eng"], capture_output=True, text=True, timeout=90, check=False, env=environment)
            if result.returncode != 0 and "deu" in result.stderr.lower():
                result = subprocess.run([executable, str(path), "stdout", "-l", "eng"], capture_output=True, text=True, timeout=90, check=False, env=environment)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("OCR timed out after 90 seconds") from exc
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Tesseract OCR failed")
        return " ".join(result.stdout.split())

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
            db.execute("DELETE FROM document_search WHERE document_id = ?", (metadata["document_id"],))
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
        connection = sqlite3.connect(self.index_path, timeout=15)
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

