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
from flask import Blueprint, Flask, g, session, redirect, request
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
        "documents/nav.html": '<nav class="app-navbar"><a href="/fixture/second">Andere Seite</a>'
            '<form id="outer-search" method="get" action="/fixture/search"><input name="q" aria-label="Suche"><button>Suchen</button></form>'
            '<form id="outer-language" method="post" action="/fixture/language"><input type="hidden" name="_csrf_token" value="' + TOKEN + '">'
            '<select aria-label="Sprache" name="language" onchange="this.form.submit()"><option value="de">DE</option><option value="en">EN</option></select></form></nav>'
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

    @documents.get("/fixture/search")
    def search():
        return '<h1>Suchergebnis</h1>'

    @documents.post("/fixture/language")
    def language():
        session["simpleoffice_language"] = request.form["language"]
        return redirect("/fixture/second")

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


def consent_race(browser, origin, replace_session=False):
    """Hold a real HTTP grant response while keeping the browser UI interactive."""
    owner_context = browser.new_context(extra_http_headers={"X-Test-Actor": "owner"})
    receiver_context = browser.new_context(extra_http_headers={"X-Test-Actor": "receiver"})
    held = {}
    try:
        owner = owner_context.new_page()
        owner.add_init_script("navigator.mediaDevices.getDisplayMedia=async()=>{const c=document.createElement('canvas');c.width=320;c.height=200;return c.captureStream(5)}")
        owner.goto(origin + "/screen")
        owner.locator("#screen-share-start").click()
        expect(owner.locator("#screen-message")).to_contain_text("Teilen aktiv")
        receiver = receiver_context.new_page()
        code = owner.locator("#screen-share-code").input_value()
        receiver.goto(origin + "/screen/view/" + code)
        expect(receiver.locator("#screen-session-state")).to_have_text("connected", timeout=15000)

        def intercept(route):
            body = route.request.post_data_json if route.request.method == "POST" else {}
            if body.get("type") == "grant" and not held:
                response = route.fetch()
                assert response.ok
                held.update(route=route, response=response, url=route.request.url)
                return  # The server granted; the browser has not received its answer.
            if body.get("type") == "revoke":
                route.abort("connectionfailed")  # Prove the local veto independently of server success.
                return
            route.continue_()

        owner.route("**/screen/api/sessions/*/support", intercept)
        receiver.locator("#screen-support-request").click()
        expect(owner.locator("#screen-support-permission-popup")).to_be_visible()
        owner.locator("#screen-support-popup-grant").click()
        expect(owner.locator("#screen-support-revoke")).to_be_enabled()
        assert held, "grant response must be held before applying the local veto"
        url = held["url"]
        if replace_session:
            owner.locator("#screen-share-stop").click()
            expect(owner.locator("#screen-session-state")).to_have_text("nicht verbunden")
            owner.locator("#screen-share-start").click()
            expect(owner.locator("#screen-message")).to_contain_text("Teilen aktiv")
            code = owner.locator("#screen-share-code").input_value()
            receiver.close()
            receiver = receiver_context.new_page()
            receiver.goto(origin + "/screen/view/" + code)
            expect(receiver.locator("#screen-session-state")).to_have_text("connected", timeout=15000)
            receiver.locator("#screen-support-request").click()
            expect(owner.locator("#screen-support-permission-popup")).to_be_visible()
            created = owner_context.request.post(origin + "/screen/api/join", headers={"X-CSRF-Token": TOKEN}, data={"code": code})
            assert created.ok
            session_id = created.json()["session"]["session_id"]
            url = origin + "/screen/api/sessions/" + session_id + "/support"
            # Simulate authoritative state arriving from another owner request. This
            # browser has not consented in the new session; an old answer must not do so.
            approved = owner_context.request.post(url, headers={"X-CSRF-Token": TOKEN}, data={"role": "sender", "type": "grant"})
            assert approved.ok
        else:
            owner.locator("#screen-support-revoke").click()
            expect(owner.locator("#screen-support-error")).to_contain_text("fetch", ignore_case=True)

        headers = {"X-Screen-Code": code, "X-CSRF-Token": TOKEN}
        snapshot = receiver_context.request.get(url + "?role=receiver", headers=headers)
        assert snapshot.ok
        data = snapshot.json()
        assert data["state"]["permission"] == "granted"
        assert data["fields"], "a valid current-page input must be available"
        queued = receiver_context.request.post(url, headers=headers, data={
            "role": "receiver", "type": "input", "generation": data["state"]["generation"],
            "epoch": data["epoch"], "field_id": data["fields"][0]["id"], "value": "MUST-NOT-APPLY"})
        assert queued.ok
        with owner.expect_response(lambda response: response.url == held["url"] and response.request.method == "POST" and response.request.post_data_json.get("type") == "grant"):
            held["route"].fulfill(response=held["response"])
            held["released"] = True
        sentinel = "Poll nach alter Freigabe " + ("Sitzungswechsel" if replace_session else "Widerruf")
        notice = receiver_context.request.post(url, headers=headers, data={"role": "receiver", "type": "notice", "text": sentinel})
        assert notice.ok
        expect(owner.locator("#screen-support-popup-text")).to_have_text(sentinel)
        expect(owner.locator("#screen-support-status")).to_contain_text("lokal gesperrt")
        expect(owner.locator("#screen-support-active-banner")).to_be_hidden()
        expect(owner.frame_locator("#screen-workspace-frame").locator("#title")).not_to_have_value("MUST-NOT-APPLY")
        history = owner_context.request.get(url + "?role=sender")
        assert history.ok
        assert not any(event["type"] == "input-result" and event.get("outcome") == "applied" for event in history.json()["events"])
        print("PASS: delayed grant after " + ("session replacement" if replace_session else "failed server revoke") + " cannot enable input")
    finally:
        if held and not held.get("released"):
            held["route"].abort()
        owner_context.close()
        receiver_context.close()


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
                owner.locator('#outer-search input').fill('Testsuche')
                owner.locator('#outer-search button').click()
                expect(frame.locator('h1')).to_have_text('Suchergebnis')
                owner.locator('#outer-language select').select_option('en')
                expect(frame.locator('h1')).to_have_text('Zweite Seite')
                assert owner.evaluate("document.documentElement.dataset.sentinel") == "alive"
                assert owner.evaluate("window.captureStops") == 0
                expect(owner.locator("#screen-session-state")).to_have_text("connected")
                owner.locator("#screen-support-banner-revoke").click()
                expect(receiver.locator("#screen-support-fields")).to_be_empty()
                owner.locator("#screen-share-stop").click()
                expect(owner.locator("#screen-session-state")).to_have_text("nicht verbunden")
                assert owner.evaluate("window.captureStops") == 1
                owner.reload()
                expect(owner.locator("#screen-support-history")).to_contain_text("Kein Ton <script>unsafe</script>")
                expect(owner.locator("#screen-support-history")).to_contain_text("Sitzung beendet")
                consent_race(browser, origin)
                consent_race(browser, origin, replace_session=True)
                browser.close()
                print("PASS: Chromium/WebRTC, navigation persistence, consent/input/no auto-save, notice/ack/XSS text, revoke, archive after reload")
        finally:
            server.shutdown()
            worker.join(timeout=5)
            screen_share._sessions.clear()
            screen_support._runtime.clear()


if __name__ == "__main__":
    main()
