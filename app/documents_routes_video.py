"""Video preview, timeline frames and derived playback variants for documents."""
from __future__ import annotations

import subprocess
from pathlib import Path

from flask import abort, current_app, flash, redirect, render_template, request, send_file, url_for

from .access_control import audit, is_admin
from .documents_core import *  # noqa: F401,F403
from .preview_service import PreviewService, VIDEO_SUFFIXES, detect_preview_tools, media_mimetype
from .v2.materialize import materialize_verified_object
from .video_settings import save_video_preview_frame_count, video_preview_frame_count
from .settings_store import DEFAULT_VIDEO_PREVIEW_FRAMES


def _video_document(document_id: str) -> dict:
    document = _document_or_404(document_id)
    if Path(str(document.get("last_path", ""))).suffix.lower() not in VIDEO_SUFFIXES:
        abort(404)
    return document


def _require_video_admin() -> None:
    if not is_admin(g.user):
        abort(403)


@bp.before_request
def open_video_previews_in_player():
    """Keep the historic preview URL but show a player for videos by default."""
    if (
        request.endpoint != "documents.image_preview"
        or request.args.get("raw") == "1"
        or request.args.get("download") == "1"
    ):
        return None
    if getattr(g, "user", None) is None:
        return None
    document_id = str((request.view_args or {}).get("document_id") or "")
    if not document_id:
        return None
    try:
        document = _store().get_document(document_id)
    except ValueError:
        return None
    if Path(str(document.get("last_path", ""))).suffix.lower() in VIDEO_SUFFIXES:
        return redirect(url_for("documents.video_player", document_id=document_id))
    return None


@bp.get("/<document_id>/video")
@login_required
def video_player(document_id: str):
    document = _video_document(document_id)
    video = document.get("preview", {}).get("video", {})
    video = video if isinstance(video, dict) else {}
    frames = []
    for row in video.get("frames", []):
        if not isinstance(row, dict):
            continue
        frames.append({
            **row,
            "url": url_for("documents.video_frame", document_id=document_id, index=int(row.get("index") or 0)),
        })
    variants = []
    for row in video.get("variants", []):
        if not isinstance(row, dict) or not row.get("variant_id"):
            continue
        variants.append({
            **row,
            "url": url_for("documents.video_variant", document_id=document_id, variant_id=row["variant_id"]),
        })
    raw_url = url_for("documents.image_preview", document_id=document_id, raw=1)
    download_url = url_for("documents.image_preview", document_id=document_id, download=1)
    preview_service = PreviewService(_store().root)
    thumbnail_url = (
        url_for("documents.document_thumbnail", document_id=document_id)
        if preview_service.cached_path(document, "thumbnail") is not None
        else ""
    )
    return render_template(
        "documents/video_player.html",
        document=document,
        video=video,
        frames=frames,
        variants=variants,
        raw_url=raw_url,
        mime=media_mimetype(str(document.get("last_path", ""))) or "video/mp4",
        thumbnail_url=thumbnail_url,
        download_url=download_url,
        ffmpeg_available=bool(detect_preview_tools()["commands"].get("ffmpeg")),
        video_preview_frame_count=video_preview_frame_count(_store().root),
        can_manage_video=is_admin(g.user),
    )


@bp.post("/<document_id>/video/settings")
@login_required
def save_video_settings(document_id: str):
    _video_document(document_id)
    _require_video_admin()
    reset_defaults = request.form.get("reset") == "1"
    try:
        count = save_video_preview_frame_count(
            _store().root,
            DEFAULT_VIDEO_PREVIEW_FRAMES if reset_defaults else request.form.get("frame_count", ""),
            str(g.user["username"]),
        )
        audit(
            "video_preview_settings_updated",
            "settings",
            "media.video_preview_frames",
            detail={"frame_count": count, "reset_to_defaults": reset_defaults},
        )
        flash(
            f"Video-Vorschau auf {count} Bilder "
            f"{'zurückgesetzt' if reset_defaults else 'eingestellt'}. "
            "Vorhandene Videos werden beim nächsten Indexlauf mit dieser Anzahl neu aufgebaut."
        )
    except ValueError as exc:
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning("Video preview settings save failed: %s", type(exc).__name__)
        flash("Video-Vorschau-Einstellung konnte nicht gespeichert werden.")
    return redirect(url_for("documents.video_player", document_id=document_id))


@bp.get("/<document_id>/video/frames/<int:index>")
@login_required
def video_frame(document_id: str, index: int):
    document = _video_document(document_id)
    path = PreviewService(_store().root).cached_video_frame(document, index)
    if path is None:
        abort(404)
    response = send_file(path, conditional=True, etag=True, max_age=31536000)
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return response


@bp.get("/<document_id>/video/variants/<variant_id>")
@login_required
def video_variant(document_id: str, variant_id: str):
    document = _video_document(document_id)
    path = PreviewService(_store().root).cached_video_variant(document, variant_id)
    if path is None:
        abort(404)
    response = send_file(path, conditional=True, etag=True, max_age=31536000, mimetype="video/mp4")
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return response


@bp.post("/<document_id>/video/transcode")
@login_required
def transcode_video(document_id: str):
    document = _video_document(document_id)
    _require_video_admin()
    actor = str(g.user["username"])
    try:
        service = PreviewService(_store().root, detect_preview_tools())
        suffix = Path(str(document.get("last_path", ""))).suffix.lower()
        with materialize_verified_object(
            _store().root,
            actor,
            document_id,
            suffix=suffix,
        ) as source:
            variant = service.transcode_video(
                source,
                document,
                actor,
                request.form.get("profile", "h264-720p"),
            )
        preview = dict(document.get("preview", {}))
        video = dict(preview.get("video", {})) if isinstance(preview.get("video"), dict) else {}
        variants = [
            dict(row) for row in video.get("variants", [])
            if isinstance(row, dict) and row.get("variant_id") != variant["variant_id"]
        ]
        variants.append(variant)
        video["variants"] = variants
        preview["video"] = video
        _store().set_preview_metadata(document_id, preview)
        flash("Video wurde als verknüpfte Wiedergabevariante neu kodiert; das Originaldokument blieb unverändert.")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        flash(f"Video konnte nicht neu kodiert werden: {exc}")
    return redirect(url_for("documents.video_player", document_id=document_id))
