"""Bounded, user-scoped background work for Smart Folder members."""
from __future__ import annotations

import hmac
from pathlib import Path

from .v3_jobs import Job, JobStore, PermanentJobError, RetryableJobError

MAX_BATCH_DOCUMENTS = 100
MAX_BATCH_FILE_BYTES = 128 * 1024 * 1024
MAX_BATCH_TOTAL_BYTES = 512 * 1024 * 1024
BATCH_ACTIONS = {"preview", "ocr", "preview_ocr"}


def batch_handler(root: str | Path, jobs: JobStore, worker_id: str):
    """Build a worker handler with lease-bound progress reporting."""
    root = Path(root).expanduser().resolve()
    worker_id = str(worker_id or "").strip()

    def run(job: Job) -> None:
        if not isinstance(job.payload, dict):
            raise PermanentJobError("invalid Smart Folder batch payload")
        action = str(job.payload.get("action", ""))
        folder = str(job.payload.get("folder", ""))
        document_ids = job.payload.get("document_ids")
        if action not in BATCH_ACTIONS or not isinstance(document_ids, list):
            raise PermanentJobError("invalid Smart Folder batch request")
        ids = [str(value).strip() for value in document_ids]
        if (
            not ids
            or len(ids) > MAX_BATCH_DOCUMENTS
            or any(not value for value in ids)
            or len(set(ids)) != len(ids)
        ):
            raise PermanentJobError("Smart Folder batch exceeds its document limit")
        principal = str(job.principal or "").strip()
        if not principal or not folder or not worker_id:
            raise PermanentJobError("Smart Folder batch identity is incomplete")

        from flask import g

        from . import app as flask_app
        from .db import get_db
        from .document_store import DocumentStore, sha256_file
        from .preview_service import PreviewService, detect_preview_tools
        from .safe_paths import resolve_file_under
        from .smart_folder_views import SmartFolderViews

        try:
            with flask_app.test_request_context("/"):
                database = get_db()
                user = database.execute(
                    "SELECT is_admin,is_disabled FROM user WHERE username=?",
                    (principal,),
                ).fetchone()
                if user is None or bool(user["is_disabled"]):
                    raise PermanentJobError("job principal is unavailable")
                g.user = {"username": principal, "is_admin": bool(user["is_admin"])}
                store = DocumentStore(root)
                views = SmartFolderViews(store, principal)
                folder_ids = views.task_folder_ids(folder, principal)
                requested_folder_id = str(job.payload.get("folder_id", ""))
                if requested_folder_id not in folder_ids:
                    raise PermanentJobError("Smart Folder changed before the job started")
                current_ids = {
                    str(document["document_id"])
                    for document in views.view(folder)["members"]
                }
                if any(document_id not in current_ids for document_id in ids):
                    raise PermanentJobError("a selected document left the Smart Folder")

                documents = []
                total_bytes = 0
                for document_id in ids:
                    metadata = store.get_document(document_id)
                    path = resolve_file_under(root, str(metadata.get("last_path", "")))
                    size = path.stat().st_size
                    if size > MAX_BATCH_FILE_BYTES:
                        raise PermanentJobError("a selected file exceeds the batch size limit")
                    total_bytes += size
                    if total_bytes > MAX_BATCH_TOTAL_BYTES:
                        raise PermanentJobError("Smart Folder batch exceeds the total size limit")
                    documents.append(document_id)

                previews = PreviewService(root, detect_preview_tools())
                jobs.update_progress(job.job_id, worker_id, {
                    "action": action, "completed": 0, "total": len(documents),
                })
                for index, document_id in enumerate(documents, start=1):
                    metadata = store.get_document(document_id)
                    path = resolve_file_under(root, str(metadata.get("last_path", "")))
                    if action in {"ocr", "preview_ocr"}:
                        metadata = store.refresh_document_analysis(
                            document_id,
                            principal,
                            max_bytes=MAX_BATCH_FILE_BYTES,
                        )
                        path = resolve_file_under(root, str(metadata.get("last_path", "")))
                    expected_digest = str(metadata.get("sha256", ""))
                    if not expected_digest or not hmac.compare_digest(
                        sha256_file(path), expected_digest,
                    ):
                        raise PermanentJobError("document changed before batch processing")
                    result = str(
                        metadata.get("text_extraction", {}).get("status", "completed")
                        if action in {"ocr", "preview_ocr"} else "skipped"
                    )
                    if action in {"preview", "preview_ocr"} and previews.supports(path):
                        preview = previews.generate(path, metadata)
                        if not hmac.compare_digest(sha256_file(path), expected_digest):
                            raise PermanentJobError("document changed during preview generation")
                        store.set_preview_metadata(document_id, preview)
                        result = str(preview.get("status", "failed"))
                    jobs.update_progress(job.job_id, worker_id, {
                        "action": action,
                        "completed": index,
                        "total": len(documents),
                        "last_document_id": document_id,
                        "last_result": result,
                    })
        except PermanentJobError:
            raise
        except PermissionError as exc:
            raise PermanentJobError("document access was revoked before processing") from exc
        except (FileNotFoundError, OSError) as exc:
            raise RetryableJobError("a selected document is temporarily unavailable") from exc
        except ValueError as exc:
            raise PermanentJobError("Smart Folder batch validation failed") from exc

    return run
