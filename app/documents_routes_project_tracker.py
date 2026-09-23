"""Trac-style project issues, wiki, Git views and activity routes."""
from __future__ import annotations

from pathlib import Path
import subprocess

from .documents_core import *  # noqa: F401,F403
from .project_git import ProjectGitError, ProjectGitService
from .project_markdown import render_markdown
from .project_store import ProjectStore
from .project_tracker import (
    ISSUE_PRIORITIES,
    ISSUE_STATES,
    ISSUE_TYPES,
    ProjectTrackerStore,
)


def _project_tracker() -> ProjectTrackerStore:
    return ProjectTrackerStore(current_app.config["DOCUMENT_ROOT"])


def _project_workspace(project_id: str) -> tuple[dict[str, Any], ProjectGitService, bool]:
    project = ProjectStore(current_app.config["DOCUMENT_ROOT"]).project(project_id)
    git = ProjectGitService(current_app.config["DOCUMENT_ROOT"], project)
    return project, git, git.available()


def _issue_view(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        **issue,
        "body_html": render_markdown(issue.get("body_markdown", ""), maximum_chars=100_000),
        "comments": [
            {
                **comment,
                "body_html": render_markdown(comment.get("body_markdown", ""), maximum_chars=20_000),
            }
            for comment in issue.get("comments", [])
        ],
    }


@bp.route("/projects/<project_id>/issues", methods=("GET", "POST"))
@login_required
def project_issues(project_id: str):
    actor = str(g.user["username"])
    try:
        project, git, code_available = _project_workspace(project_id)
        tracker = _project_tracker()
        if request.method == "POST":
            issue = tracker.create_issue(project_id, request.form.to_dict(), actor)
            flash(f"Issue #{issue['number']} angelegt.")
            return redirect(url_for(
                "documents.project_issue_detail",
                project_id=project_id,
                issue_number=issue["number"],
            ))
        query = request.args.get("q", "").strip()
        status = request.args.get("status", "").strip()
        issue_type = request.args.get("type", "").strip()
        issues = tracker.issues(
            project_id,
            query=query,
            status=status,
            issue_type=issue_type,
        )
    except (RuntimeError, ValueError) as exc:
        flash(str(exc))
        return redirect(url_for("documents.projects"))
    return render_template(
        "documents/project_issues.html",
        project=project,
        issues=issues,
        query=query,
        selected_status=status,
        selected_type=issue_type,
        issue_types=sorted(ISSUE_TYPES),
        issue_states=sorted(ISSUE_STATES),
        issue_priorities=("low", "normal", "high", "urgent"),
        code_available=code_available,
    )


@bp.route("/projects/<project_id>/issues/<int:issue_number>", methods=("GET", "POST"))
@login_required
def project_issue_detail(project_id: str, issue_number: int):
    actor = str(g.user["username"])
    try:
        project, git, code_available = _project_workspace(project_id)
        tracker = _project_tracker()
        if request.method == "POST":
            issue = tracker.update_issue(
                project_id,
                issue_number,
                request.form.to_dict(),
                actor,
                expected_updated_at=request.form.get("expected_updated_at", ""),
            )
            flash(f"Issue #{issue_number} gespeichert.")
            return redirect(url_for(
                "documents.project_issue_detail",
                project_id=project_id,
                issue_number=issue_number,
            ))
        issue = _issue_view(tracker.issue(project_id, issue_number))
        related_commits = []
        if code_available:
            try:
                related_commits = git.commits_for_issue(issue_number)
            except (OSError, ProjectGitError, ValueError, subprocess.TimeoutExpired):
                related_commits = []
    except (ProjectGitError, RuntimeError, ValueError) as exc:
        flash(str(exc))
        return redirect(url_for("documents.project_issues", project_id=project_id))
    return render_template(
        "documents/project_issue_detail.html",
        project=project,
        issue=issue,
        issue_types=sorted(ISSUE_TYPES),
        issue_states=sorted(ISSUE_STATES),
        issue_priorities=("low", "normal", "high", "urgent"),
        related_commits=related_commits,
        code_available=code_available,
    )


@bp.post("/projects/<project_id>/issues/<int:issue_number>/comments")
@login_required
def project_issue_comment(project_id: str, issue_number: int):
    try:
        ProjectStore(current_app.config["DOCUMENT_ROOT"]).project(project_id)
        _project_tracker().add_comment(
            project_id,
            issue_number,
            request.form.get("body_markdown", ""),
            str(g.user["username"]),
        )
        flash("Kommentar gespeichert.")
    except (RuntimeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for(
        "documents.project_issue_detail",
        project_id=project_id,
        issue_number=issue_number,
    ))


@bp.route("/projects/<project_id>/wiki", methods=("GET", "POST"))
@login_required
def project_wiki(project_id: str):
    actor = str(g.user["username"])
    try:
        project, _git, code_available = _project_workspace(project_id)
        tracker = _project_tracker()
        if request.method == "POST":
            revision_text = request.form.get("expected_revision", "").strip()
            expected_revision = int(revision_text) if revision_text else None
            page = tracker.save_wiki_page(
                project_id,
                request.form.to_dict(),
                actor,
                expected_revision=expected_revision,
            )
            flash(f"Wiki-Seite „{page['title']}“ gespeichert.")
            return redirect(url_for(
                "documents.project_wiki",
                project_id=project_id,
                page=page["slug"],
            ))
        pages = tracker.wiki_pages(project_id)
        requested = request.args.get("page", "").strip()
        if requested:
            page = tracker.wiki_page(project_id, requested)
        else:
            page = next((item for item in pages if item.get("slug") == "home"), None)
            if page is None and pages:
                page = pages[0]
        page_view = (
            {
                **page,
                "body_html": render_markdown(page.get("body_markdown", ""), maximum_chars=200_000),
            }
            if page else None
        )
    except (RuntimeError, ValueError) as exc:
        flash(str(exc))
        return redirect(url_for("documents.project_detail", project_id=project_id))
    return render_template(
        "documents/project_wiki.html",
        project=project,
        pages=pages,
        page=page_view,
        code_available=code_available,
    )


def _require_project_git(project_id: str) -> tuple[dict[str, Any], ProjectGitService]:
    project, git, available = _project_workspace(project_id)
    if not available:
        raise ProjectGitError("Code ist nur verfügbar, wenn ein freigegebenes Git-Repository erkannt wurde.")
    return project, git


@bp.get("/projects/<project_id>/code")
@login_required
def project_code(project_id: str):
    try:
        project, git = _require_project_git(project_id)
        ref = request.args.get("ref", "HEAD")
        path = request.args.get("path", "")
        summary = git.summary()
        tree = git.tree(ref=ref, path=path) if summary["has_head"] else []
    except (OSError, ProjectGitError, ValueError, subprocess.TimeoutExpired) as exc:
        flash(str(exc))
        return redirect(url_for("documents.project_detail", project_id=project_id))
    return render_template(
        "documents/project_code.html",
        project=project,
        summary=summary,
        tree=tree,
        current_path=path,
        ref=ref,
        code_available=True,
    )


@bp.get("/projects/<project_id>/code/file")
@login_required
def project_code_file(project_id: str):
    try:
        project, git = _require_project_git(project_id)
        ref = request.args.get("ref", "HEAD")
        path = request.args.get("path", "")
        file = git.text_file(path, ref=ref)
        suffix = Path(path).suffix.casefold()
        markdown_html = (
            render_markdown(file["text"], maximum_chars=512 * 1024)
            if suffix in {".md", ".markdown"} else None
        )
    except (OSError, ProjectGitError, ValueError, subprocess.TimeoutExpired) as exc:
        flash(str(exc))
        return redirect(url_for("documents.project_code", project_id=project_id))
    return render_template(
        "documents/project_code_file.html",
        project=project,
        file=file,
        markdown_html=markdown_html,
        code_available=True,
    )


@bp.get("/projects/<project_id>/activity")
@login_required
def project_activity(project_id: str):
    try:
        project, git, code_available = _project_workspace(project_id)
        events = list(_project_tracker().activity(project_id, limit=200))
        if code_available:
            try:
                commits = git.commits(limit=50)
            except (OSError, ProjectGitError, ValueError, subprocess.TimeoutExpired):
                commits = []
            events.extend({
                "event_id": "git:" + commit["sha"],
                "kind": "git_commit",
                "summary": commit["subject"],
                "reference": "commit:" + commit["sha"],
                "actor": commit["author"],
                "at": commit["authored_at"],
                "short_sha": commit["short_sha"],
            } for commit in commits)
        events.sort(key=lambda item: str(item.get("at", "")), reverse=True)
    except (ProjectGitError, RuntimeError, ValueError) as exc:
        flash(str(exc))
        return redirect(url_for("documents.project_detail", project_id=project_id))
    return render_template(
        "documents/project_activity.html",
        project=project,
        events=events[:250],
        code_available=code_available,
    )
