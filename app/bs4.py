"""Small compatibility helpers for legacy top-level template routes.

Historically every rendered page was parsed a second time with BeautifulSoup and
html5lib only to pretty-print it. That adds noticeable CPU/RAM cost on small
systems and provides no runtime benefit.
"""

from __future__ import annotations

from pathlib import Path

from flask import Response, render_template, request, send_file

from .safe_paths import normalize_path, resolve_file_under


def download_file(static_dir: str = "", dirname: str = "", filename: str = ""):
    """Serve one static file only after canonical path validation."""
    if request.path == "/favicon.ico":
        return Response(status=204)

    try:
        root = normalize_path(static_dir, strict=True)
        relative = Path(str(dirname or "")) / str(filename or "")
        target = resolve_file_under(root, relative)
    except (OSError, ValueError):
        return Response("not found", status=404)
    return send_file(target, conditional=True)


def renderwithbs4(myFile: str = "index.html") -> str:
    """Render a template once; retained under its historic function name."""
    template = str(myFile or "index.html").strip()
    if not template.endswith(".html"):
        template += ".html"
    return render_template(template)
