"""Regression checks for the recursive document upload path-validation contract.

These checks exercise the real upload view through a minimal Flask request
context, mocking only its authentication and storage dependencies.
"""
from __future__ import annotations

import io

import pytest
from flask import Flask, g, request

from app import documents_routes_workflows as workflows


@pytest.mark.parametrize("relative_path", [
    "../secret.txt",
    "root/../secret.txt",
    "/absolute/secret.txt",
    "root//secret.txt",
    "root/CON/secret.txt",
    "root/folder./secret.txt",
    "root/folder /secret.txt",
    "root/folder:bad/secret.txt",
])
def test_folder_upload_rejects_unsafe_relative_paths(monkeypatch, relative_path):
    app = Flask(__name__)
    app.secret_key = "test-only"
    app.add_url_rule("/documents", "documents.index", lambda: "index")

    # The route is decorated with login_required; invoke the wrapped view in
    # a request context, where validation runs before storage is initialized.
    view = workflows.upload
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__

    with app.test_request_context(
        "/documents/upload",
        method="POST",
        data={
            "folder_import": "1",
            "relative_paths": relative_path,
            "files": (io.BytesIO(b"payload"), "secret.txt"),
        },
        content_type="multipart/form-data",
    ):
        g.user = {"username": "tester"}
        response = view()
        assert response.status_code == 302
        assert response.location.endswith("/documents")
        messages = __import__("flask").get_flashed_messages()
        assert any("Ungültig" in msg or "ungültig" in msg for msg in messages)
