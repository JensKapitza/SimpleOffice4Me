"""Real Chromium/WebRTC smoke with synthetic capture and an isolated Flask fixture.

Run: python tests/browser/screen_support_smoke.py
No production accounts, documents or native desktop input are used.
"""
from pathlib import Path
import sys
import os
import shutil
import tempfile
import threading
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from flask import Blueprint, Flask, g, session
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect
from app import screen_share, screen_support
from app.security_controls import protect_browser_mutation

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "x" * 40


def fixture(directory):
    app = Flask(__name__, static_folder=str(ROOT / "static"))
    app.config.update(TESTING=True, TEST_CSRF_PROTECTION=True, SECRET_KEY="synthetic-screen-test", DOCUMENT_ROOT=directory)
    app.jinja_loader = ChoiceLoader([DictLoader({
        "layout.html": '<!doctype html><meta name="csrf-token" content="' + TOKEN + '"><style>.d-none{display:none!important}</style>{% block header %}{% endblock %}{% block body %}{% endblock %}',
        "documents/nav.html": '<nav><a href="/fixture/second">Andere Seite</a></nav>'
    }), FileSystemLoader(str(ROOT / "templates"))])
    app.jinja_env.globals["csrf_token"] = lambda: TOKEN

    @app.before_request
    def actor():
        from flask import request
        g.user = {"id": request.headers.get("X-Test-Actor", "owner"), "is_disabled": False}
        g.language = "de"
        session["_csrf_token"] = TOKEN

    app.before_request(protect_browser_mutation)
    documents = Blueprint("documents", __name__)

    @documents.get("/fixture/app")
    def index():
        return '<label for="title">Titel</label><form method="post" action="/fixture/saved"><input id="title" name="title" onchange="window.autoSaved=true"><input type="password" id="password"><button>Speichern</button></form><a href="/fixture/second">Weiter</a>'

    @documents.get("/fixture/second")
    def second():
        return '<h1>Zweite Seite</h1><label for="name">Name</label><input id="name" name="name"><a href="/fixture/app">Zurück</a>'

    @documents.post("/fixture/saved")
    def saved():
        return 'saved'

    relay = Blueprint("connectivity_relay_ice", __name__)

    @relay.get("/fixture/ice")
    def ice():
        return {"iceServers": []}

    app.register_blueprint(documents)
    app.register_blueprint(relay)
    app.register_blueprint(screen_share.bp)
    return app


def main():
    screen_share._sessions.clear()
    screen_support._runtime.clear()
    state = {"windows": {"available": False}, "linux": {"available": False}, "android_native": False}
    with tempfile.TemporaryDirectory() as directory, mock.patch.object(screen_share, "platform_status", return_value=state), mock.patch.object(screen_share, "audit"):
        server = make_server("127.0.0.1", 0, fixture(directory), threaded=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get("SCREEN_BROWSER_EXECUTABLE") or shutil.which("chromium"), headless=True, args=["--no-sandbox"])
                owner_context = browser.new_context(extra_http_headers={"X-Test-Actor": "owner"})
                receiver_context = browser.new_context(extra_http_headers={"X-Test-Actor": "receiver"})
                owner = owner_context.new_page()
                owner.add_init_script('''window.captureStops=0; navigator.mediaDevices.getDisplayMedia=async()=>{const c=document.createElement('canvas');c.width=320;c.height=200; c.getContext('2d').fillRect(0,0,320,200);const stream=c.captureStream(5);const t=stream.getVideoTracks()[0],stop=t.stop.bind(t);t.stop=()=>{window.captureStops++;stop()};return stream};''')
                owner.goto(origin + "/screen")
                owner.locator("#screen-share-start").click()
                expect(owner.locator("#screen-message")).to_contain_text("Teilen aktiv")
                code = owner.locator("#screen-share-code").input_value()
                receiver = receiver_context.new_page()
                receiver.goto(origin + "/screen/view/" + code)
                expect(receiver.locator("#screen-session-state")).to_have_text("connected", timeout=15000)
                expect(receiver.locator("#screen-support-request")).to_be_enabled()
                receiver.locator("#screen-support-request").click()
                expect(owner.locator("#screen-support-permission-popup")).to_be_visible()
                owner.locator("#screen-support-popup-grant").click()
                expect(receiver.locator("#screen-support-fields label")).to_have_text(["Titel"])
                receiver.locator("#screen-support-fields input").fill("Support-Titel")
                receiver.locator("#screen-support-fields button").click()
                frame = owner.frame_locator("#screen-workspace-frame")
                expect(frame.locator("#title")).to_have_value("Support-Titel")
                assert not owner.frames[1].evaluate("Boolean(window.autoSaved)")
                expect(receiver.locator("#screen-support-history")).to_contain_text("übernommen")
                owner.evaluate("document.documentElement.dataset.sentinel='alive'")
                frame.get_by_text("Weiter", exact=True).click()
                expect(frame.locator("h1")).to_have_text("Zweite Seite")
                assert owner.evaluate("document.documentElement.dataset.sentinel") == "alive"
                assert owner.evaluate("window.captureStops") == 0
                expect(owner.locator("#screen-session-state")).to_have_text("connected")
                expect(receiver.locator("#screen-support-fields label")).to_have_text(["Name"])
                receiver.locator("#screen-support-notice").fill("Kein Ton <script>unsafe</script>")
                receiver.locator("#screen-support-send").click()
                expect(owner.locator("#screen-support-popup")).to_be_visible()
                expect(owner.locator("#screen-support-popup-text")).to_have_text("Kein Ton <script>unsafe</script>")
                expect(receiver.locator("#screen-support-popup")).to_be_hidden()
                owner.locator("#screen-support-ack").click()
                expect(receiver.locator("#screen-support-history")).to_contain_text("Zur Kenntnis genommen")
                owner.locator("#screen-support-banner-revoke").click()
                expect(receiver.locator("#screen-support-fields")).to_be_empty()
                owner.locator("#screen-share-stop").click()
                expect(owner.locator("#screen-session-state")).to_have_text("nicht verbunden")
                assert owner.evaluate("window.captureStops") == 1
                owner.reload()
                expect(owner.locator("#screen-support-history")).to_contain_text("Kein Ton <script>unsafe</script>")
                expect(owner.locator("#screen-support-history")).to_contain_text("Sitzung beendet")
                browser.close()
                print("PASS: Chromium/WebRTC, navigation persistence, consent/input/no auto-save, notice/ack/XSS text, revoke, archive after reload")
        finally:
            server.shutdown()
            worker.join(timeout=5)
            screen_share._sessions.clear()
            screen_support._runtime.clear()


if __name__ == "__main__":
    main()
