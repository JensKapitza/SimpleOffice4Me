"""Smoke-test selected calendar pages against an APK-shaped real loopback session."""
import json
import os
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import build_opener, HTTPCookieProcessor, Request
from urllib.parse import urlencode
from urllib.error import HTTPError
from http.cookiejar import CookieJar
from werkzeug.serving import make_server


def main():
    with TemporaryDirectory() as folder:
        path = Path(folder)
        os.environ["SIMPLEOFFICE_DOCUMENT_ROOT"] = str(path / "data")
        from app import app
        from app.db import ensure_auth_database
        from app.calendar_collections import CalendarCollections
        from app.caldav import _parse_ics
        from tests.http_api_fixture import CsrfMetadata
        from tests.test_android_managed_snapshot import ICS

        app.config.update(DOCUMENT_ROOT=str(path / "data"), DATABASE=str(path / "auth.sqlite3"))
        with app.app_context():
            ensure_auth_database()
        store = CalendarCollections(path / "data")
        store.put_event("default", "series.ics", _parse_ics(ICS), "owner")
        for n in range(129):
            store.put_event("default", f"event{n}.ics", {"uid": f"synthetic-{n}", "title": f"Synthetic {n}",
                            "start": "2026-12-24"}, "owner")
        server = make_server("127.0.0.1", 8765, app)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        try:
            opener = build_opener(HTTPCookieProcessor(CookieJar()))
            opener.addheaders = [("User-Agent", "Synthetic test SimpleOffice4Me-Android/1")]
            base = "http://127.0.0.1:8765"
            page = "/android/device-sources/"
            def token(route):
                parser = CsrfMetadata()
                with opener.open(base + route, timeout=5) as result:
                    parser.feed(result.read().decode())
                assert parser.token
                return parser.token
            for route in ("/auth/register", "/auth/login"):
                data = urlencode({"username": "owner", "password": "synthetic-only-password"}).encode()
                req = Request(base + route, data=data, headers={"X-CSRF-Token": token(route)})
                with opener.open(req, timeout=5):
                    pass
            csrf = token(page)
            def fetch(offset, revision):
                data = json.dumps({"calendar_ids": ["default"], "offset": offset, "revision": revision}).encode()
                req = Request(base + page + "managed/calendar", data=data,
                              headers={"Content-Type": "application/json", "X-CSRF-Token": csrf})
                with opener.open(req, timeout=5) as response:
                    assert "no-store" in response.headers["Cache-Control"]
                    return json.load(response)
            first = fetch(0, "")
            offset = first["next_offset"]
            pages = [first]
            while offset is not None:
                result = fetch(offset, first["revision"])
                assert result["principal"] == first["principal"]
                pages.append(result)
                offset = result["next_offset"]
            sizes = [len(p["rows"]) for p in pages]
            assert sizes == [25, 25, 25, 25, 25, 6], sizes
            rows = [row for p in pages for row in p["rows"]]
            assert rows[-1]["title"] == "Verschoben" and any(r["id"] == rows[-1]["original_id"] for r in rows[:-1])
            store.put_event("default", "new.ics", {"uid": "later", "title": "Changed", "start": "2026-12-20"}, "owner")
            try:
                fetch(first["next_offset"], first["revision"])
            except HTTPError as err:
                assert err.code == 409
            else:
                raise AssertionError("Stale revision accepted")
            print("PASS: real loopback HTTP, 131 synthetic events and series cases over six pages, stale revision rejected")
        finally:
            server.shutdown()
            serving.join(3)



if __name__ == "__main__":
    main()
