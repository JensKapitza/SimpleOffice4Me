"""Project repository roots actions (no environment).

Additional allowed Git roots live in the user-home disk store (``projects``
section, default empty) and are edited from the admin projects page. Only
absolute paths are accepted; missing directories simply stay inactive until
they exist.
"""
from __future__ import annotations

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from .access_control import audit
from .admin import admin_required
from .project_git import validate_repo_roots
from .settings_store import SettingsStore
from .translations import translate

bp = Blueprint("project_repo_admin", __name__, url_prefix="/admin/projects")


def _current_roots() -> list[str]:
    return SettingsStore(current_app.config["DOCUMENT_ROOT"]).settings()["projects"]["repo_roots"]


@bp.get("/repo-roots")
@admin_required
def index():
    return render_template(
        "admin/project_repos.html",
        repo_roots=_current_roots(),
    )


@bp.post("/repo-roots")
@admin_required
def save():
    reset_defaults = request.form.get("reset") == "1"
    try:
        if reset_defaults:
            roots: list[str] = []
        else:
            raw = request.form.get("repo_roots", "")
            roots = validate_repo_roots(
                [line.strip() for line in raw.splitlines() if line.strip()],
            )
        SettingsStore(current_app.config["DOCUMENT_ROOT"]).save(
            {"projects": {"repo_roots": roots}}, str(g.user["username"]),
        )
        audit(
            "project_repo_roots_updated",
            "settings",
            "projects.repo_roots",
            detail={"repo_roots": roots, "reset_to_defaults": reset_defaults},
        )
        flash(translate(g.language, "admin.projects.reset")
            if reset_defaults else translate(g.language, "admin.projects.saved"))
    except ValueError as exc:
        current_app.logger.info("Project repo roots rejected: %s", type(exc).__name__)
        flash(str(exc))
    except OSError as exc:
        current_app.logger.warning("Project repo roots save failed: %s", type(exc).__name__)
        flash(translate(g.language, "admin.projects.failed"))
    return redirect(url_for("project_repo_admin.index"))
