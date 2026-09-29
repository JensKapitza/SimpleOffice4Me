"""Standalone loopback demo for the SimpleOffice V3 extension HTTP contract.

This process is never imported or executed by SimpleOffice.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json


class Handler(BaseHTTPRequestHandler):
    server_version = "SimpleOfficeExtensionExample/1"

    def do_POST(self):
        if self.path != "/simpleoffice-extension":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if length < 0 or length > 256 * 1024:
            self.send_error(413)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_error(400)
            return
        point = str(payload.get("point", ""))
        operation = str(payload.get("operation", ""))
        values = payload.get("payload", {})
        if point == "health_check" and operation == "check":
            result = {"status": "ok"}
        elif point == "search_provider" and operation == "search":
            query = str(values.get("query", "")).strip()
            result = {
                "results": [
                    {
                        "title": f"Externer Demo-Treffer: {query}",
                        "subtitle": "Read-only loopback extension",
                        "kind": "Extension",
                        "url": "/documents/",
                        "ref_id": "demo-result",
                    }
                ] if query else []
            }
        else:
            self.send_error(404)
            return
        raw = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
