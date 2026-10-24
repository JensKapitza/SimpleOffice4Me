"""Authenticated routes for dynamically discovered and activated Smart Views."""
from __future__ import annotations

import csv
import io

from .documents_core import *  # noqa: F401,F403
from .smart_folder_views import SmartFolderViews


def _smart_views() -> SmartFolderViews:
    return SmartFolderViews(_store(), str(g.user["username"]))


def _spreadsheet_safe_cell(value: object) -> str:
    text = "" if value is None else str(value)
    first = text.lstrip(" \t\r\n\x00")[:1]
    return "'" + text if first in {"=", "+", "-", "@"} else text


@bp.get("/smart-folders")
@login_required
def smart_folders():
    views = _smart_views()
    assignment_document = _assignment_document(request.args.get("assign_document_id", ""))
    return render_template(
        "documents/smart_folders.html",
        folders=views.folders(),
        selected=None,
        assignment_document=assignment_document,
    )


def _assignment_document(document_id: str) -> dict | None:
    document_id = str(document_id or "").strip()
    if not document_id:
        return None
    try:
        return _store().get_document(document_id)
    except ValueError:
        abort(404)


def _folder_tasks(views: SmartFolderViews, folder: str, actor: str) -> list[dict]:
    try:
        folder_ids = set(views.task_folder_ids(folder, actor))
    except ValueError:
        return []
    if not folder_ids:
        return []
    return [
        task for task in _todos().items(actor)
        if task.get("smart_folder_id") in folder_ids
    ]


@bp.get("/smart-folders/view")
@login_required
def smart_folder_view():
    folder = request.args.get("folder", "").strip()
    if not folder:
        return redirect(url_for("documents.smart_folders"))
    views = _smart_views()
    assignment_document = _assignment_document(request.args.get("assign_document_id", ""))
    try:
        folders = views.folders()
        selected = views.view(folder, folders)
    except ValueError:
        abort(404)
    rules = views.rules_for(selected["path"])
    try:
        rows = views.extract_rows(selected["path"]) if rules else []
    except (OSError, RuntimeError, ValueError) as exc:
        current_app.logger.warning("Smart folder extraction failed: %s", type(exc).__name__)
        rows = []
        flash("Die tabellarische Auswertung ist derzeit nicht verfügbar.")
    try:
        tasks = _folder_tasks(views, selected["path"], str(g.user["username"]))
    except (OSError, RuntimeError, ValueError) as exc:
        current_app.logger.warning("Smart folder tasks unavailable: %s", type(exc).__name__)
        tasks = []
        flash("Die Sammelordner-Aufgaben sind derzeit nicht verfügbar.")
    return render_template(
        "documents/smart_folders.html",
        folders=folders,
        selected=selected,
        rules=rules,
        rows=rows,
        tasks=tasks,
        assignment_document=assignment_document,
    )


@bp.post("/smart-folders/activate")
@login_required
def activate_smart_folder():
    folder = request.form.get("folder", "")
    try:
        _smart_views().activate(folder, str(g.user["username"]), request.form.get("notes", ""))
        flash("Sammelordner aktiviert.")
    except ValueError as exc:
        flash(str(exc))
    except (OSError, RuntimeError) as exc:
        current_app.logger.warning("Smart folder activation failed: %s", type(exc).__name__)
        flash("Sammelordner konnte nicht gespeichert werden.")
    return redirect(url_for("documents.smart_folder_view", folder=folder))


@bp.post("/smart-folders/previews")
@login_required
def set_smart_folder_previews():
    folder = request.form.get("folder", "")
    try:
        _smart_views().set_preview_documents(
            folder,
            request.form.getlist("document_ids"),
            str(g.user["username"]),
        )
        flash("Sammelordner-Vorschau gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    except (OSError, RuntimeError) as exc:
        current_app.logger.warning("Smart folder preview selection failed: %s", type(exc).__name__)
        flash("Sammelordner-Vorschau konnte nicht gespeichert werden.")
    return redirect(url_for("documents.smart_folder_view", folder=folder))


@bp.post("/smart-folders/task")
@login_required
def create_smart_folder_task():
    folder = request.form.get("folder", "")
    actor = str(g.user["username"])
    try:
        folder_id = _smart_views().folder_task_id(folder, actor)
        _todos().add(
            request.form.get("title", ""),
            actor,
            {"description": request.form.get("description", "")},
            smart_folder_id=folder_id,
        )
        flash("Aufgabe mit dem Sammelordner verknüpft.")
    except ValueError as exc:
        flash(str(exc))
    except (OSError, RuntimeError) as exc:
        current_app.logger.warning("Smart folder task creation failed: %s", type(exc).__name__)
        flash("Aufgabe konnte nicht gespeichert werden.")
    return redirect(url_for("documents.smart_folder_view", folder=folder))


@bp.get("/smart-folders/merge-preview")
@login_required
def smart_folder_merge_preview():
    source = request.args.get("source", "").strip()
    target = request.args.get("target", "").strip()
    if not source or not target:
        return redirect(url_for("documents.smart_folders"))
    views = _smart_views()
    try:
        impact = views.merge_preview(source, target)
    except ValueError:
        abort(404)
    actor = str(g.user["username"])
    try:
        source_tasks = _folder_tasks(views, source, actor)
        target_tasks = _folder_tasks(views, target, actor)
        impact["source_tasks"] = len(source_tasks)
        impact["target_tasks"] = len(target_tasks)
        impact["source_task_rows"] = source_tasks[:200]
        impact["target_task_rows"] = target_tasks[:200]
        impact["source_tasks_truncated"] = len(source_tasks) > 200
        impact["target_tasks_truncated"] = len(target_tasks) > 200
        impact["task_preview_available"] = True
    except (OSError, RuntimeError, ValueError) as exc:
        current_app.logger.warning("Smart folder task merge preview failed: %s", type(exc).__name__)
        impact["source_tasks"] = None
        impact["target_tasks"] = None
        impact["task_preview_available"] = False
    return render_template("documents/smart_folder_merge_preview.html", impact=impact)


@bp.post("/smart-folders/merge")
@login_required
def merge_smart_folders():
    source = request.form.get("source", "")
    target = request.form.get("target", "")
    if request.form.get("confirm") != "ZUSAMMENFUEHREN":
        flash("Fusion nicht bestätigt.")
        return redirect(url_for("documents.smart_folders"))
    try:
        views = _smart_views()
        actor = str(g.user["username"])
        _folder_tasks(views, source, actor)
        _folder_tasks(views, target, actor)
        views.merge_assignments(source, target, actor)
        flash("Logische Zuordnungen und Regeln übernommen. Originaldateien wurden nicht verschoben.")
        return redirect(url_for("documents.smart_folder_view", folder=target))
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("documents.smart_folders"))
    except (OSError, RuntimeError) as exc:
        current_app.logger.warning("Smart folder merge failed: %s", type(exc).__name__)
        flash("Ordner konnten nicht zusammengeführt werden.")
        return redirect(url_for("documents.smart_folders"))


@bp.post("/smart-folders/rule")
@login_required
def save_smart_folder_rule():
    folder = request.form.get("folder", "")
    try:
        _smart_views().save_rule(
            folder,
            request.form.get("name", ""),
            request.form.get("field", ""),
            str(g.user["username"]),
        )
        flash("Extraktionsregel gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    except (OSError, RuntimeError) as exc:
        current_app.logger.warning("Smart folder rule save failed: %s", type(exc).__name__)
        flash("Extraktionsregel konnte nicht gespeichert werden.")
    return redirect(url_for("documents.smart_folder_view", folder=folder))


@bp.get("/smart-folders/export.csv")
@login_required
def export_smart_folder():
    folder = request.args.get("folder", "").strip()
    if not folder:
        return redirect(url_for("documents.smart_folders"))
    try:
        rows = _smart_views().extract_rows(folder)
    except ValueError:
        abort(404)
    except (OSError, RuntimeError) as exc:
        current_app.logger.warning("Smart folder export failed: %s", type(exc).__name__)
        return Response(
            "Die tabellarische Auswertung ist derzeit nicht verfügbar.\n",
            status=503,
            content_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )
    columns = list(dict.fromkeys(key for row in rows for key in row))
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow([_spreadsheet_safe_cell(column) for column in columns])
    for row in rows:
        writer.writerow([_spreadsheet_safe_cell(row.get(column, "")) for column in columns])
    response = Response(
        stream.getvalue(),
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="smart-folder.csv"'},
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.post("/<document_id>/smart-folder")
@login_required
def assign_smart_folder(document_id: str):
    folder = request.form.get("folder", "")
    try:
        _smart_views().assign(document_id, folder)
        flash("Dokument wurde der Smart View zugeordnet. Der Originalspeicherort bleibt erhalten.")
    except ValueError as exc:
        flash(str(exc))
    except (OSError, RuntimeError) as exc:
        current_app.logger.warning("Smart folder assignment failed: %s", type(exc).__name__)
        flash("Dokument konnte der Smart View nicht zugeordnet werden.")
    return redirect(url_for("documents.detail", document_id=document_id))
