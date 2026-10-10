#!/usr/bin/env python3
"""Real-browser acceptance for issue #606 against a running SimpleOffice server."""
from __future__ import annotations
import os
from pathlib import Path
from pypdf import PdfReader
from PIL import Image, ImageStat
from playwright.sync_api import sync_playwright

BASE_URL=os.environ.get("BASE_URL","http://127.0.0.1:8080").rstrip("/")
OUT=Path(os.environ.get("BROWSER_EXPORT_DIR","test-results/browser/web-export"))
USER=os.environ.get("BROWSER_TEST_USERNAME","web-export-admin")
PASSWORD=os.environ.get("BROWSER_TEST_PASSWORD","Browser-Test-2026!")

def login(page):
    page.goto(BASE_URL+"/auth/register",wait_until="domcontentloaded")
    if page.locator("#username").count():
        page.locator("#username").fill(USER); page.locator("#password").fill(PASSWORD)
        page.get_by_role("button",name="Lokales Konto erstellen",exact=True).click()
        page.wait_for_url("**/auth/login")
    else:
        page.goto(BASE_URL+"/auth/login",wait_until="domcontentloaded")
    page.locator("#username").fill(USER); page.locator("#password").fill(PASSWORD)
    page.get_by_role("button",name="Anmelden",exact=True).click()
    page.wait_for_load_state("networkidle")

def export(page,fmt,media):
    toolbar=page.locator("[data-web-export-toolbar]")
    toolbar.locator("select[name='media']").select_option(media)
    with page.expect_download(timeout=70000) as info:
        toolbar.locator(f"button[name='format'][value='{fmt}']").click()
    download=info.value
    path=OUT/f"{media}.{fmt}"
    download.save_as(path)
    return path

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page(accept_downloads=True)
        login(page)
        page.goto(BASE_URL+"/",wait_until="networkidle")
        assert page.locator("[data-web-export-toolbar]").count()==1
        # CSS and JavaScript are active in the rendered application before export.
        assert page.evaluate("() => getComputedStyle(document.body).display")!="none"
        assert page.evaluate("() => typeof window.SimpleOfficeTranslations")=="object"
        # Print mode must keep the normal application content visible.
        page.emulate_media(media="print")
        assert page.evaluate("""() => {
            const content = document.querySelector('main') || document.body;
            return getComputedStyle(content).visibility !== 'hidden'
                && !!content.innerText.trim();
        }"""), "Print CSS hides page content"
        page.emulate_media(media="screen")
        pdf=export(page,"pdf","print")
        png=export(page,"png","screen")
        assert pdf.stat().st_size>1000 and png.stat().st_size>1000
        with Image.open(png) as image:
            rgb = image.convert("RGB")
            assert max(ImageStat.Stat(rgb).stddev) > 3, "PNG appears blank/monochrome"
        text="\n".join((p.extract_text() or "") for p in PdfReader(str(pdf)).pages)
        assert text.strip(), "PDF must contain searchable text"
        # Exercise the second media mode for each format as well.
        assert export(page,"pdf","screen").stat().st_size>1000
        assert export(page,"png","print").stat().st_size>1000
        browser.close()
    print("web export browser acceptance: OK")

if __name__=="__main__":
    main()
