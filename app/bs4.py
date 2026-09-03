"""Small compatibility helpers for legacy top-level template routes.

Historically every rendered page was parsed a second time with BeautifulSoup and
html5lib only to pretty-print it.  That adds noticeable CPU/RAM cost on small
systems (especially Termux), can subtly rewrite valid HTML, and provides no
runtime benefit.  Keep the public helper name for compatibility but return the
Jinja result directly.
"""

from __future__ import annotations

from pathlib import Path

from flask import Response, render_template, request, send_from_directory


def download_file(static_dir: str = "", dirname: str = "", filename: str = ""):
    """Serve one static file without allowing the requested directory to escape.

    Flask's ``send_from_directory`` performs its own safe join for ``filename``;
    this additional check protects the separately supplied ``dirname`` argument.
    Normal application static assets should use Flask's built-in static route.
    """
    if request.path == "/favicon.ico":
        return Response(status=204)

    root = Path(static_dir).expanduser().resolve()
    directory = (root / str(dirname or "")).resolve()
    if root not in (directory, *directory.parents):
        return Response("not found", status=404)
    return send_from_directory(directory, filename, as_attachment=False)


def renderwithbs4(myFile: str = "index.html") -> str:
    """Render a template once; retained under its historic function name."""
    template = str(myFile or "index.html").strip()
    if not template.endswith(".html"):
        template += ".html"
    return render_template(template)
