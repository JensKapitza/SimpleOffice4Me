"""Application registration hook for SOFP federation routes."""
from __future__ import annotations


def init_app(app) -> None:
    from . import federation_http
    app.register_blueprint(federation_http.bp)
