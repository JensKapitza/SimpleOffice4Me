"""Check JPEG-to-search OCR through the real offline Android APK WebView."""
from __future__ import annotations

import base64
import re
from urllib.parse import urlencode

from playwright.sync_api import expect

ORIGIN = "http://127.0.0.1:8765"
GERMAN = "ÄPFEL UND BÜCHER"
ENGLISH = "QUIVER LANTERN"
PRINTED = "manual-ocr-text-fixture.jpg"
BLANK = "manual-ocr-blank-fixture.jpg"


def synthetic_jpeg(page, lines):
    """Render a fresh JPEG with the platform sans font; no fixture or font downloads."""
    data = page.evaluate("""lines => {
        const canvas = document.createElement("canvas");
        canvas.width = 1300;
        canvas.height = 660;
        const context = canvas.getContext("2d");
        context.fillStyle = "#fff";
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.fillStyle = "#000";
        context.font = "bold 92px sans-serif";
        lines.forEach((line, i) => context.fillText(line, 120, 225 + i * 150));
        return canvas.toDataURL("image/jpeg", 0.94).split(",")[1];
    }""", lines)
    return base64.b64decode(data, validate=True)


def import_photos(page):
    page.goto(f"{ORIGIN}/documents/images", wait_until="domcontentloaded")
    upload = page.locator("#photo-bulk-form")
    expect(upload).to_be_visible()
    upload.locator("#photo-ocr").uncheck()
    # Use the real bulk-upload form and backend. The input selection itself is
    # synthetic; Android's document-picker behavior is tested separately.
    upload.locator("#photo-files").set_input_files([
        {"name": name, "mimeType": "image/jpeg", "buffer": synthetic_jpeg(page, lines)}
        for name, lines in [(PRINTED, [GERMAN, ENGLISH]), (BLANK, [])]
    ])
    upload.locator("#photo-start").click()
    results = page.locator("#photo-results")
    links = {}
    for name in (PRINTED, BLANK):
        link = results.get_by_role("link", name=re.compile(re.escape(name)))
        expect(link).to_be_visible(timeout=30000)
        href = link.get_attribute("href")
        assert href and re.fullmatch(r"/documents/[a-zA-Z0-9-]+", href), "Missing photo detail link"
        links[name] = href
    assert links[PRINTED] != links[BLANK], "Both independent JPEGs must exist"
    return links


def search_document_paths(page, term):
    query = urlencode({"q": term, "type": "document"})
    # Fetch within the same WebView session as the photo importer and form.
    matches = page.evaluate("""async path => {
        const response = await fetch(path, {credentials: "same-origin", cache: "no-store"});
        if (!response.ok) throw new Error("OCR search request failed");
        return (await response.json()).matches.map(match => match.url);
    }""", f"/documents/quick-search/api?{query}")
    return set(matches)


def run_manual_ocr(page, detail, expected):
    page.goto(ORIGIN + detail, wait_until="domcontentloaded")
    panel = page.locator("#image-analysis")
    expect(panel.locator("[data-photo-ocr-status=pending]")).to_be_visible()
    form = panel.locator('form[action$="/analyze-image"]')
    route = form.get_attribute("action")
    assert route and route == detail + "/analyze-image", "Photo OCR must target the current document"
    with page.expect_response(
        lambda response: response.url == ORIGIN + route and response.request.method == "POST",
        timeout=120000,
    ) as completed:
        form.locator("button[type=submit]").click()
    assert completed.value.status in {302, 303}, "The existing manual photo OCR form did not complete"
    expect(panel.locator(f"[data-photo-ocr-status={expected}]")).to_be_visible(timeout=30000)
    return panel


def verify(page):
    links = import_photos(page)
    assert links[PRINTED] not in search_document_paths(page, "QUIVER"), "Deferred JPEG was already indexed"
    panel = run_manual_ocr(page, links[PRINTED], "recognized")
    panel.locator("details summary").click()
    for line in (GERMAN, ENGLISH):
        expect(panel.locator("details pre")).to_contain_text(line, ignore_case=True)
    assert links[PRINTED] in search_document_paths(page, "QUIVER"), "Recognized text was not indexed"
    run_manual_ocr(page, links[BLANK], "empty")
    assert links[BLANK] not in search_document_paths(page, "QUIVER"), "Blank photo indexed another photo's text"
    page.goto(ORIGIN + links[PRINTED], wait_until="domcontentloaded")
    expect(page.locator("#image-analysis [data-photo-ocr-status=recognized]")).to_be_visible()
