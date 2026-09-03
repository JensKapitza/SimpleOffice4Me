"""Mobile-friendly bulk photo import with rich, provenance-aware metadata."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, current_app, flash, g, jsonify, redirect, request, url_for

from .auth import login_required
from .document_store import DocumentStore, sha256_file, utc_now
from .settings_store import SettingsStore


bp = Blueprint("photo_upload", __name__, url_prefix="/documents/photos")

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_METADATA_DEPTH = 7
MAX_METADATA_ITEMS = 4096
MAX_METADATA_TEXT = 8192
MAX_USER_AGENT = 512
MAX_RELATIVE_PATH = 1024


def _bounded_text(value: Any, limit: int = MAX_METADATA_TEXT) -> str:
    return str(value or "").replace("\x00", "")[:limit]


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    """Convert EXIF/XMP values to bounded JSON without discarding binary facts."""
    if depth >= MAX_METADATA_DEPTH:
        return _bounded_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_METADATA_TEXT]
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {"byte_length": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    if isinstance(value, dict):
        items = list(value.items())[:MAX_METADATA_ITEMS]
        return {
            _bounded_text(key, 160): _json_safe(item, depth=depth + 1)
            for key, item in items
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item, depth=depth + 1) for item in list(value)[:MAX_METADATA_ITEMS]]
    try:
        # Pillow's IFDRational and similar numeric wrappers are losslessly useful as floats.
        numerator = getattr(value, "numerator")
        denominator = getattr(value, "denominator")
        if denominator:
            return float(numerator) / float(denominator)
    except (AttributeError, TypeError, ValueError, ZeroDivisionError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return _bounded_text(value)


def _float_value(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        try:
            numerator, denominator = value
            return float(numerator) / float(denominator)
        except (TypeError, ValueError, ZeroDivisionError):
            return None


def _gps_coordinate(value: Any, reference: Any) -> float | None:
    try:
        degrees, minutes, seconds = value
    except (TypeError, ValueError):
        return None
    parts = [_float_value(part) for part in (degrees, minutes, seconds)]
    if any(part is None for part in parts):
        return None
    result = float(parts[0]) + float(parts[1]) / 60.0 + float(parts[2]) / 3600.0
    if str(reference or "").strip().upper() in {"S", "W"}:
        result *= -1
    return round(result, 8)


def _pillow_metadata(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read image/container details and every Pillow-exposed EXIF IFD."""
    from PIL import ExifTags, Image

    result: dict[str, Any] = {}
    derived: dict[str, Any] = {}
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        result.update({
            "format": image.format or path.suffix.lstrip(".").upper(),
            "mime_type": Image.MIME.get(image.format or "", ""),
            "mode": image.mode,
            "width": int(image.width),
            "height": int(image.height),
            "frames": int(getattr(image, "n_frames", 1) or 1),
            "animated": bool(getattr(image, "is_animated", False)),
            "info": _json_safe(dict(image.info)),
        })
        derived.update({
            "format": result["format"],
            "mime_type": result["mime_type"],
            "width": result["width"],
            "height": result["height"],
        })
        exif = image.getexif()
        root: dict[str, Any] = {}
        for key, value in list(exif.items())[:MAX_METADATA_ITEMS]:
            label = ExifTags.TAGS.get(key, str(key))
            root[str(label)] = _json_safe(value)
        result["exif"] = root

        ifds: dict[str, Any] = {}
        ifd_enum = getattr(ExifTags, "IFD", None)
        for name in ("Exif", "GPSInfo", "Interop", "IFD1"):
            ifd_id = getattr(ifd_enum, name, None) if ifd_enum is not None else None
            if ifd_id is None or not hasattr(exif, "get_ifd"):
                continue
            try:
                values = exif.get_ifd(ifd_id)
            except (KeyError, TypeError, ValueError):
                continue
            labels = ExifTags.GPSTAGS if name == "GPSInfo" else ExifTags.TAGS
            mapped = {
                str(labels.get(key, key)): _json_safe(value)
                for key, value in list(values.items())[:MAX_METADATA_ITEMS]
            }
            if mapped:
                ifds[name] = mapped
            if name == "GPSInfo" and values:
                latitude = _gps_coordinate(values.get(2), values.get(1))
                longitude = _gps_coordinate(values.get(4), values.get(3))
                altitude = _float_value(values.get(6))
                if latitude is not None and longitude is not None:
                    derived["gps"] = {
                        "latitude": latitude,
                        "longitude": longitude,
                        **({"altitude_m": round(float(altitude), 3)} if altitude is not None else {}),
                    }
        result["ifds"] = ifds

        exif_ifd = ifds.get("Exif", {})
        for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
            value = exif_ifd.get(key) or root.get(key)
            if value:
                derived["captured_at"] = _bounded_text(value, 128)
                break
        derived["camera_make"] = _bounded_text(root.get("Make", ""), 160)
        derived["camera_model"] = _bounded_text(root.get("Model", ""), 160)
        derived["software"] = _bounded_text(root.get("Software", ""), 240)
        derived["lens_model"] = _bounded_text(exif_ifd.get("LensModel", root.get("LensModel", "")), 240)
        orientation = root.get("Orientation")
        if orientation not in (None, ""):
            derived["orientation_exif"] = orientation
    return result, derived


def _exiftool_metadata(path: Path) -> dict[str, Any]:
    """Use ExifTool when the host provides it; Pillow remains the zero-config fallback."""
    executable = shutil.which("exiftool")
    if not executable:
        return {"available": False, "reason": "exiftool is not installed"}
    try:
        completed = subprocess.run(
            [executable, "-json", "-G1", "-a", "-s", "-n", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"available": True, "status": "timeout"}
    if completed.returncode != 0:
        return {"available": True, "status": "error", "error": completed.stderr.strip()[:1000]}
    try:
        rows = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {"available": True, "status": "error", "error": f"invalid ExifTool JSON: {exc}"}
    fields = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
    fields.pop("SourceFile", None)
    fields.pop("File:SourceFile", None)
    return {"available": True, "status": "completed", "fields": _json_safe(fields)}


def _field(fields: dict[str, Any], *names: str) -> Any:
    wanted = {name.casefold() for name in names}
    for key, value in fields.items():
        tail = str(key).rsplit(":", 1)[-1].casefold()
        if str(key).casefold() in wanted or tail in wanted:
            if value not in (None, ""):
                return value
    return ""


def _merge_exiftool_derived(derived: dict[str, Any], exiftool: dict[str, Any]) -> None:
    fields = exiftool.get("fields", {}) if isinstance(exiftool.get("fields"), dict) else {}
    if not fields:
        return
    mapping = {
        "captured_at": ("SubSecDateTimeOriginal", "DateTimeOriginal", "CreateDate", "MediaCreateDate"),
        "camera_make": ("Make",),
        "camera_model": ("Model", "CameraModelName"),
        "lens_model": ("LensModel", "LensID"),
        "software": ("Software",),
    }
    for target, candidates in mapping.items():
        if not derived.get(target):
            value = _field(fields, *candidates)
            if value not in (None, ""):
                derived[target] = _bounded_text(value, 256)
    latitude = _float_value(_field(fields, "GPSLatitude"))
    longitude = _float_value(_field(fields, "GPSLongitude"))
    altitude = _float_value(_field(fields, "GPSAltitude"))
    if latitude is not None and longitude is not None and "gps" not in derived:
        derived["gps"] = {
            "latitude": round(latitude, 8),
            "longitude": round(longitude, 8),
            **({"altitude_m": round(float(altitude), 3)} if altitude is not None else {}),
        }


def extract_photo_metadata(path: Path) -> dict[str, Any]:
    """Extract rich local metadata without ever modifying the original image."""
    result: dict[str, Any] = {"extracted_at": utc_now(), "pillow": {}, "exiftool": {}}
    derived: dict[str, Any] = {}
    try:
        pillow, derived = _pillow_metadata(path)
        result["pillow"] = pillow
    except ImportError:
        result["pillow"] = {"status": "unavailable", "error": "Pillow is not installed"}
    except Exception as exc:  # Pillow raises format-specific subclasses across versions.
        result["pillow"] = {"status": "error", "error": _bounded_text(exc, 1000)}
    result["exiftool"] = _exiftool_metadata(path)
    _merge_exiftool_derived(derived, result["exiftool"])
    result["derived"] = derived
    return result


def _date_parts(value: Any) -> tuple[str, str, str] | None:
    text = _bounded_text(value, 128).strip()
    match = re.search(r"(?<!\d)(\d{4})[:/-](\d{2})[:/-](\d{2})(?!\d)", text)
    if not match:
        return None
    year, month, day = match.groups()
    try:
        datetime(int(year), int(month), int(day))
    except ValueError:
        return None
    return year, f"{year}-{month}", f"{year}-{month}-{day}"


def _instant_local_parts(value: str, timezone_name: str) -> tuple[str, str, str] | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(timezone_name) if timezone_name else timezone.utc
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    local = parsed.astimezone(zone)
    return local.strftime("%Y"), local.strftime("%Y-%m"), local.strftime("%Y-%m-%d")


def metadata_tags(
    *,
    received_at: str,
    timezone_name: str,
    client_last_modified_at: str,
    rich_metadata: dict[str, Any],
) -> set[str]:
    """Create bounded, searchable tags from useful normalized photo facts."""
    derived = rich_metadata.get("derived", {}) if isinstance(rich_metadata.get("derived"), dict) else {}
    tags = {"bild", "foto-upload"}

    upload_parts = _instant_local_parts(received_at, timezone_name)
    if upload_parts:
        year, month, day = upload_parts
        tags.update({f"upload-jahr-{year}", f"upload-monat-{month}", f"upload-{day}"})
    file_parts = _instant_local_parts(client_last_modified_at, timezone_name) if client_last_modified_at else None
    if file_parts:
        year, month, day = file_parts
        tags.update({f"datei-jahr-{year}", f"datei-monat-{month}", f"datei-{day}"})
    capture_parts = _date_parts(derived.get("captured_at", ""))
    if capture_parts:
        year, month, day = capture_parts
        tags.update({f"jahr-{year}", f"aufnahme-monat-{month}", f"aufnahme-{day}"})

    image_format = _bounded_text(derived.get("format", ""), 32).casefold()
    if image_format:
        tags.add(f"format-{DocumentStore._tag_token(image_format)}")
    make = _bounded_text(derived.get("camera_make", ""), 160)
    model = _bounded_text(derived.get("camera_model", ""), 160)
    camera = " ".join(value for value in (make, model) if value).strip()
    if camera:
        tags.add(f"kamera-{DocumentStore._tag_token(camera)}")
    lens = _bounded_text(derived.get("lens_model", ""), 240)
    if lens:
        tags.add(f"objektiv-{DocumentStore._tag_token(lens)}")

    try:
        width, height = int(derived.get("width", 0)), int(derived.get("height", 0))
    except (TypeError, ValueError):
        width = height = 0
    if width > 0 and height > 0:
        tags.add(f"aufloesung-{width}x{height}")
        tags.add("querformat" if width > height else "hochformat" if height > width else "quadrat")
    if isinstance(derived.get("gps"), dict):
        tags.add("gps")
    if rich_metadata.get("pillow") and not rich_metadata.get("pillow", {}).get("status"):
        tags.add("metadaten-pillow")
    if rich_metadata.get("exiftool", {}).get("status") == "completed":
        tags.add("metadaten-exiftool")
    return tags


def _parse_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _safe_filename(filename: str) -> str:
    original = _bounded_text(filename, 512).replace("\\", "/")
    name = Path(original).name.strip().strip(".")
    if not name:
        name = "foto"
    suffix = Path(name).suffix.lower()
    stem = Path(name).stem[:180] or "foto"
    return f"{stem}{suffix}"


def _verify_photo(path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix not in PHOTO_EXTENSIONS:
        raise ValueError("Dieses Bildformat ist für den Handy-Massenimport nicht freigegeben.")
    try:
        from PIL import Image
        with Image.open(path) as image:
            image.verify()
    except ImportError as exc:
        raise RuntimeError("Pillow wird für die sichere Bildprüfung benötigt") from exc
    except Exception as exc:
        raise ValueError(f"Bilddatei konnte nicht sicher gelesen werden: {exc}") from exc


class PhotoBulkImporter:
    """Import one queue item without triggering a complete archive rescan."""

    def __init__(self, root: str | Path):
        self.store = DocumentStore(root)

    def import_photo(
        self,
        upload: Any,
        filename: str,
        actor: str,
        *,
        archive: bool = False,
        max_bytes: int = 512 * 1024 * 1024,
        client: dict[str, Any] | None = None,
        run_ocr: bool = False,
    ) -> dict[str, Any]:
        self.store._require_actor(actor)
        self.store.initialize()
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("upload size limit must be positive")
        original_filename = _bounded_text(filename, 512)
        safe_name = _safe_filename(original_filename)
        if Path(safe_name).suffix.lower() not in PHOTO_EXTENSIONS:
            raise ValueError("Erlaubt sind JPEG, PNG, GIF und WebP.")

        received_at = utc_now()
        source = getattr(upload, "stream", upload)
        staging = self.store.control / "staging" / f"photo-{uuid.uuid4().hex}-{safe_name}"
        staging.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        digest = ""
        target: Path | None = None
        try:
            with staging.open("xb") as destination:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError(f"Bild überschreitet das Upload-Limit von {max_bytes // (1024 * 1024)} MiB")
                    destination.write(chunk)
            if written < 1:
                raise ValueError("Leere Bilddatei wurde nicht importiert")
            digest = sha256_file(staging)
            _verify_photo(staging)
            destination_dir = self.store.root / (Path("archive") / digest[:2] / digest if archive else Path("inbox"))
            destination_dir.mkdir(parents=True, exist_ok=True)
            self.store.ensure_folder_policy(destination_dir, actor)
            target = destination_dir / safe_name
            while target.exists():
                target = destination_dir / f"{Path(safe_name).stem}-{uuid.uuid4().hex[:8]}{Path(safe_name).suffix}"
            staging.replace(target)
        finally:
            staging.unlink(missing_ok=True)

        # A full DocumentStore.scan() for every queue item makes hundreds of phone
        # photos quadratic in archive size. Index exactly the new file instead.
        restore_ocr = None
        if not run_ocr:
            restore_ocr = self.store._image_ocr
            self.store._image_ocr = lambda _path: ""  # type: ignore[method-assign]
        try:
            self.store._scan_file(target.resolve(), force_hash=True)
        finally:
            if restore_ocr is not None:
                self.store._image_ocr = restore_ocr  # type: ignore[method-assign]

        metadata = self.store.get_document(target)
        if not run_ocr:
            analysis = metadata.setdefault("image_analysis", {})
            analysis["ocr_status"] = "deferred_bulk_upload"
            analysis["ocr_characters"] = 0
            analysis.pop("ocr_error", None)
            metadata["ocr_text"] = ""
            metadata["extracted_text"] = ""
            metadata["text_extraction"] = {
                "source_sha256": metadata.get("sha256", ""),
                "extracted_at": received_at,
                "status": "deferred",
                "kind": "image",
                "native_characters": 0,
                "image_ocr_characters": 0,
                "characters": 0,
            }
            self.store._save_document(metadata)
            self.store._refresh_search_index(metadata)

        rich = extract_photo_metadata(target)
        client = dict(client or {})
        upload_metadata = {
            "source": "mobile-web-bulk",
            "received_at": received_at,
            "original_filename": original_filename,
            "stored_filename": target.name,
            "size_bytes": written,
            "sha256": digest,
            "client_mime_type": _bounded_text(client.get("mime_type", ""), 160),
            "client_reported_size_bytes": _parse_int(client.get("size", written), written, 0, max_bytes),
            "client_last_modified_at": _bounded_text(client.get("last_modified_at", ""), 128),
            "client_timezone": _bounded_text(client.get("timezone", ""), 120),
            "client_timezone_offset_minutes": _parse_int(client.get("timezone_offset_minutes", 0), 0, -1440, 1440),
            "client_relative_path": _bounded_text(client.get("relative_path", ""), MAX_RELATIVE_PATH),
            "batch_id": _bounded_text(client.get("batch_id", ""), 120),
            "batch_index": _parse_int(client.get("batch_index", 1), 1, 1, 100000),
            "batch_count": _parse_int(client.get("batch_count", 1), 1, 1, 100000),
            "user_agent": _bounded_text(client.get("user_agent", ""), MAX_USER_AGENT),
            "ocr_during_upload": bool(run_ocr),
        }
        tags = metadata_tags(
            received_at=received_at,
            timezone_name=upload_metadata["client_timezone"],
            client_last_modified_at=upload_metadata["client_last_modified_at"],
            rich_metadata=rich,
        )
        defaults = SettingsStore(self.store.root).settings()["documents"]
        tags.update(str(tag).strip() for tag in defaults.get("default_tags", []) if str(tag).strip())
        metadata = self.store.update_metadata(
            metadata["document_id"],
            attributes={"photo_upload": upload_metadata, "photo_metadata": rich},
            tags=[*metadata.get("tags", []), *sorted(tags)],
            author=actor,
        )
        if defaults.get("default_state", "new") != "new":
            self.store.set_state(metadata["document_id"], str(defaults["default_state"]), actor)
            metadata = self.store.get_document(metadata["document_id"])
        self.store._event("photo_bulk_uploaded", {
            "document_id": metadata["document_id"], "actor": actor,
            "received_at": received_at, "size_bytes": written, "sha256": digest,
            "batch_id": upload_metadata["batch_id"], "batch_index": upload_metadata["batch_index"],
            "batch_count": upload_metadata["batch_count"],
        })
        return metadata


def _client_metadata() -> dict[str, Any]:
    return {
        "mime_type": request.form.get("client_mime_type", ""),
        "size": request.form.get("client_size", ""),
        "last_modified_at": request.form.get("client_last_modified_at", ""),
        "timezone": request.form.get("client_timezone", ""),
        "timezone_offset_minutes": request.form.get("client_timezone_offset_minutes", "0"),
        "relative_path": request.form.get("client_relative_path", ""),
        "batch_id": request.form.get("batch_id", ""),
        "batch_index": request.form.get("batch_index", "1"),
        "batch_count": request.form.get("batch_count", "1"),
        "user_agent": request.headers.get("User-Agent", ""),
    }


@bp.post("/upload")
@login_required
def upload_photo():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"ok": False, "error": "Keine Bilddatei empfangen."}), 400
    try:
        metadata = PhotoBulkImporter(current_app.config["DOCUMENT_ROOT"]).import_photo(
            uploaded,
            uploaded.filename,
            str(g.user["username"]),
            archive=request.form.get("archive") == "1",
            max_bytes=int(current_app.config["MAX_CONTENT_LENGTH"]),
            client=_client_metadata(),
            run_ocr=request.form.get("run_ocr") == "1",
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({
        "ok": True,
        "document_id": metadata["document_id"],
        "path": metadata.get("last_path", ""),
        "tags": metadata.get("tags", []),
        "received_at": metadata.get("attributes", {}).get("photo_upload", {}).get("received_at", ""),
        "detail_url": url_for("documents.detail", document_id=metadata["document_id"]),
    })


@bp.post("/<document_id>/refresh-metadata")
@login_required
def refresh_metadata(document_id: str):
    store = DocumentStore(current_app.config["DOCUMENT_ROOT"])
    try:
        metadata = store.get_document(document_id)
        path = store.root / str(metadata.get("last_path", ""))
        _verify_photo(path)
        rich = extract_photo_metadata(path)
        upload_metadata = metadata.get("attributes", {}).get("photo_upload", {})
        tags = metadata_tags(
            received_at=str(upload_metadata.get("received_at") or metadata.get("first_seen_at") or utc_now()),
            timezone_name=str(upload_metadata.get("client_timezone", "")),
            client_last_modified_at=str(upload_metadata.get("client_last_modified_at", "")),
            rich_metadata=rich,
        )
        store.update_metadata(
            document_id,
            attributes={"photo_metadata": rich},
            tags=[*metadata.get("tags", []), *sorted(tags)],
            author=str(g.user["username"]),
        )
        flash("Foto-Metadaten vollständig neu eingelesen.")
    except (OSError, RuntimeError, ValueError) as exc:
        flash(f"Foto-Metadaten konnten nicht aktualisiert werden: {exc}")
    return redirect(request.referrer or url_for("documents.images"))
