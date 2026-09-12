"""Video preview, timeline frames and derived playback variants for documents."""
from __future__ import annotations

import mimetypes
import subprocess
from pathlib import Path

from flask import abort, flash, redirect, render_template, request, send_file, url_for

from .documents_core import *  # noqa: F401,F403
from .preview_service import PreviewService, VIDEO_SUFFIXES, detect_preview_tools
from .safe_paths import resolve_file_under


def _video_document(document_id: str) -> dict:
    document = _document_or_404(document_id)
    if Path(str(document.get("last_path", ""))).suffix.lower() not in VIDEO_SUFFIXES:
        abort(404)
    return document


def _video_source(document: dict) -> Path:
    try:
        return resolve_file_under(_store().root, str(document.get("last_path", "")))
    except (OSError, ValueError):
        abort(404)


@bp.before_request
def open_video_previews_in_player():
    """Keep the historic preview URL but show a player for videos by default."""
    if request.endpoint != "documents.image_preview" or request.args.get("raw") == "1":
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
    return render_template(
        "documents/video_player.html",
        document=document,
        video=video,
        frames=frames,
        variants=variants,
        raw_url=raw_url,
        mime=mimetypes.guess_type(str(document.get("last_path", "")))[0] or "video/mp4",
        thumbnail_url=url_for("documents.document_thumbnail", document_id=document_id),
        ffmpeg_available=bool(detect_preview_tools()["commands"].get("ffmpeg")),
    )


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
    actor = str(g.user["username"])
    try:
        service = PreviewService(_store().root, detect_preview_tools())
        variant = service.transcode_video(
            _video_source(document),
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
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        flash(f"Video konnte nicht neu kodiert werden: {exc}")
    return redirect(url_for("documents.video_player", document_id=document_id))
