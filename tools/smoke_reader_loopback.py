"""Opt-in, real Waitress/Poppler/Tesseract/Piper check with synthetic input only."""

from __future__ import annotations

import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import wave
from http.cookiejar import CookieJar
from importlib.metadata import version as package_version
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = "/admin/settings/documents-media"
FIXTURE = "synthetic-mixed-reader.pdf"
TOOLS = ("pdftotext", "pdftoppm", "pdfimages", "tesseract", "piper")


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def prerequisites() -> Path | None:
    value = os.environ.get("SIMPLEOFFICE_TEST_PIPER_MODEL", "").strip()
    if not value:
        print("SKIP: set SIMPLEOFFICE_TEST_PIPER_MODEL to a licensed local Piper .onnx model")
        return None
    candidate = Path(value).expanduser()
    if "://" in value or value.startswith(("//", "\\")) or candidate.suffix.casefold() != ".onnx":
        raise ValueError("Provide a local Piper .onnx model, not a URL")
    model = candidate.resolve()
    if not model.is_file() or not model.with_suffix(".onnx.json").is_file():
        raise ValueError("The supplied .onnx model and its .onnx.json config must both exist")
    missing = [tool for tool in TOOLS if shutil.which(tool) is None]
    if missing:
        print("SKIP: missing required local tools: " + ", ".join(missing))
        return None
    langs = subprocess.run(["tesseract", "--list-langs"], check=True, capture_output=True, text=True, timeout=10)
    if not {"deu", "eng"}.intersection(langs.stdout.splitlines()):
        print("SKIP: Tesseract needs an installed deu or eng language pack")
        return None
    return model


class SameOriginRedirect(HTTPRedirectHandler):
    def __init__(self, origin):
        self.origin = urlsplit(origin)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        check((target.scheme, target.netloc) == (self.origin.scheme, self.origin.netloc),
              "Loopback fixture rejected a cross-origin redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Browser:
    def __init__(self, origin):
        self.origin = origin
        self.client = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()), SameOriginRedirect(origin))

    def http(self, path, *, data=None, headers=None, expected=200):
        target = urlsplit(path)
        check(not target.netloc and not target.scheme and path.startswith("/") and not path.startswith("//"),
              "Loopback fixture needs a same-origin path")
        try:
            response = self.client.open(Request(self.origin + path, data=data, headers=headers or {}), timeout=75)
        except HTTPError as exc:
            response = exc
        with response:
            check(response.status == expected, f"{target.path}: expected HTTP {expected}, got {response.status}")
            return response.headers, response.read()

    def html(self, path, **options):
        from bs4 import BeautifulSoup

        return BeautifulSoup(self.http(path, **options)[1], "html.parser")

    def post_form(self, path, values):
        return self.html(path, data=urlencode(values).encode())


def csrf(page):
    return page.select_one('meta[name="csrf-token"]')["content"]


def configure_voice(browser, username, password, model):
    login = browser.html("/auth/login")
    browser.post_form("/auth/login", {"username": username, "password": password, "_csrf_token": csrf(login)})
    home = browser.html("/documents/")
    browser.post_form("/documents/settings/language", {"language": "de", "_csrf_token": csrf(home)})
    settings = browser.html(SETTINGS)
    form = settings.find("form", action=SETTINGS)
    values = {field["name"]: field.get("value", "") for field in form.select("input[name]")}
    values.update({field.find_parent("select")["name"]: field["value"]
                   for field in form.select("select[name] option[selected]")})
    values["piper_model"] = str(model)
    browser.post_form(SETTINGS, values)
    saved = browser.html(SETTINGS)
    check(saved.select_one("#piper-model")["value"] == str(model), "Media settings did not save the test voice")


def mixed_pdf():
    from PIL import Image, ImageDraw, ImageFont
    from pypdf import PdfReader
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=(600, 600))
    pdf.drawString(55, 450, "Erste Seite. Der lokale Reader liest diesen nativen Text.")
    pdf.showPage()
    with Image.new("RGB", (1450, 1000), "white") as raster:
        pen = ImageDraw.Draw(raster)
        font = ImageFont.load_default(size=72)
        pen.text((100, 230), "RECHNUNG MAGNETKOMPASS", font=font, fill="black")
        pen.text((100, 480), "Lieferdatum April 68214", font=font, fill="black")
        pdf.drawImage(ImageReader(raster), 45, 115, 510, 350)
    pdf.showPage()
    pdf.drawString(55, 450, "Dritte Seite. Hier geht der native Text weiter.")
    pdf.showPage()
    pdf.save()
    data = output.getvalue()
    pages = PdfReader(io.BytesIO(data)).pages
    check(len(pages) == 3 and bool(pages[0].extract_text().strip())
          and not pages[1].extract_text().strip() and bool(pages[2].extract_text().strip()),
          "Fixture must have native text only on pages 1 and 3")
    return data


def upload_and_open(browser, data):
    listing = browser.html("/documents/")
    action = listing.select_one("#document-upload-form")["action"]
    boundary = "SyntheticReader" + secrets.token_hex(10)
    fields = {"_csrf_token": csrf(listing), "batch_upload": "1"}
    multipart = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in fields.items()
    )
    multipart += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{FIXTURE}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    _, body = browser.http(action, data=multipart, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    check(json.loads(body) == {"ok": True, "stored": 1}, "Synthetic PDF upload failed")
    shelf = browser.html("/documents/reader")
    link = next(a for a in shelf.select('a[href^="/documents/reader/"]') if "synthetic-mixed-reader" in a.get_text())
    return browser.html(link["href"])


def read_page(browser, url, page, version, token, expected=200):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-CSRF-Token"] = token
    data = json.dumps({"page": page, "document_version": version}).encode()
    return browser.http(url, data=data, headers=headers, expected=expected)


def check_audio(headers, data):
    check(headers.get_content_type() == "audio/wav" and headers.get("Cache-Control") == "private, no-store",
          "Read-aloud must stream a private, non-cacheable WAV")
    with wave.open(io.BytesIO(data), "rb") as audio:
        frames = audio.getnframes()
        check(frames > 1000 and audio.getframerate() > 0 and any(audio.readframes(frames)),
              "Piper did not return a non-silent WAV")
    return frames


def exercise(browser, doc_root, scratch):
    source = mixed_pdf()
    page = upload_and_open(browser, source)
    reader = page.select_one("[data-document-reader][data-page-count]")
    player = page.select_one("[data-reader-read-aloud]")
    check(reader["data-page-count"] == "3" and player.select_one("[data-speech-continuous]")
          and player.select_one("[data-speech-ocr]"), "Reader must render native continuation and explicit OCR controls")
    version = reader["data-document-version"]
    native, ocr = player["data-speech-url"], player["data-speech-ocr-url"]
    token = csrf(page)
    originals = {file.relative_to(doc_root): file.read_bytes() for file in doc_root.rglob("*.pdf")}
    check(list(originals.values()) == [source], "Only the synthetic original should exist")
    wav_paths = set(scratch.parent.rglob("*.wav"))

    read_page(browser, ocr, 2, version, "", expected=403)
    _, stale = read_page(browser, ocr, 2, "stale-test-version", token, expected=409)
    check(json.loads(stale) == {"error": "document_changed"}, "Stale Reader version did not fail")
    native_frames = check_audio(*read_page(browser, native, 1, version, token))
    headers, rejected = read_page(browser, native, 2, version, token, expected=422)
    check(json.loads(rejected) == {"error": "no_text"} and headers.get("Cache-Control") == "private, no-store",
          "Native read-aloud did not stop on the scan with a non-cacheable no_text")
    ocr_frames = check_audio(*read_page(browser, ocr, 2, version, token))
    final_frames = check_audio(*read_page(browser, native, 3, version, token))
    check(originals == {file.relative_to(doc_root): file.read_bytes() for file in doc_root.rglob("*.pdf")},
          "Read-aloud changed or exported the PDF")
    check(not list(scratch.iterdir()), "Read-aloud left files in the isolated temporary directory")
    check(wav_paths == set(scratch.parent.rglob("*.wav")), "Read-aloud left a new WAV in the isolated app")
    print(f"PASS: native page 1 WAV ({native_frames} frames), native page 2 422 no_text, "
          f"explicit OCR page 2 WAV ({ocr_frames} frames), native page 3 WAV ({final_frames} frames)")
    print("PASS: CSRF 403 and stale version 409; PDF unchanged; temporary directory empty; no WAV retained")
    print(f"Runtime: Python {sys.version.split()[0]} / Flask {package_version('Flask')} / "
          f"Waitress {package_version('waitress')}")


def serve(checkout, doc_root, scratch, model):
    from waitress import create_server

    sys.path.insert(0, str(checkout))
    from app import app, db
    from app.password_security import hash_password

    username, password = "synthetic-reader-smoke", secrets.token_urlsafe(24)
    with app.app_context():
        connection = db.get_db()
        connection.execute("INSERT INTO user(username,password,is_admin) VALUES(?,?,1)",
                           (username, hash_password(password)))
        connection.commit()
    server = create_server(app, host="127.0.0.1", port=0, threads=2, asyncore_loop_timeout=0.2)
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    try:
        browser = Browser(f"http://127.0.0.1:{server.effective_port}")
        configure_voice(browser, username, password, model)
        exercise(browser, doc_root, scratch)
    finally:
        server.task_dispatcher.shutdown()
        server.close()
        thread.join(timeout=2)


def main() -> int:
    model = prerequisites()
    if model is None:
        return 77
    with tempfile.TemporaryDirectory(prefix="simpleoffice-reader-loopback-") as directory:
        base = Path(directory)
        checkout = base / "checkout"
        checkout.mkdir()
        # The app initializes its auth DB and session key relative to its source
        # location on import. Copy only code and built-in assets, never live data.
        for name in ("app", "templates", "static", "tools"):
            shutil.copytree(ROOT / name, checkout / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for file in ROOT.glob("*.py"):
            shutil.copy2(file, checkout / file.name)
        doc_root, scratch, home = (base / name for name in ("documents", "scratch", "home"))
        for folder in (doc_root, scratch, home):
            folder.mkdir()
        for key in tuple(os.environ):
            if key.startswith("SIMPLEOFFICE_"):
                del os.environ[key]
        os.environ.update(
            SIMPLEOFFICE_DOCUMENT_ROOT=str(doc_root), SIMPLEOFFICE_LOG_STDERR_ONLY="1", HOME=str(home),
            XDG_CONFIG_HOME=str(home / "config"), XDG_DATA_HOME=str(home / "data"), APPDATA=str(home),
            LOCALAPPDATA=str(home), USERPROFILE=str(home), MODEL_API_KEY="",
            TMPDIR=str(scratch), TEMP=str(scratch), TMP=str(scratch),
        )
        tempfile.tempdir = str(scratch)
        previous_directory = Path.cwd()
        try:
            os.chdir(checkout)
            serve(checkout, doc_root, scratch, model)
        finally:
            os.chdir(previous_directory)
            tempfile.tempdir = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
