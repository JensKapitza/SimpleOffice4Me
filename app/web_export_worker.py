"""Small Playwright renderer for same-origin SimpleOffice exports."""
from __future__ import annotations
import json, sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit

def _same_origin(url, origin):
    p=urlsplit(url)
    return p.scheme==origin.scheme and p.hostname==origin.hostname and p.port==origin.port

def main():
    payload=json.load(sys.stdin)
    base=str(payload["base_url"]).rstrip("/")+"/"
    origin=urlsplit(base)
    target=urljoin(base,str(payload["target"]).lstrip("/"))
    if not _same_origin(target,origin):
        raise RuntimeError("target origin rejected")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        context=browser.new_context(extra_http_headers={"X-SimpleOffice-Export-Token":str(payload["token"])})
        page=context.new_page()
        page.route("**/*",lambda route: route.continue_() if _same_origin(route.request.url,origin) else route.abort())
        response=page.goto(urljoin(base,"web-export/session"),wait_until="domcontentloaded",timeout=15000)
        if response is None or response.status != 204:
            raise RuntimeError("export authentication rejected")
        context.set_extra_http_headers({})
        response=page.goto(target,wait_until="networkidle",timeout=30000)
        if response is None or response.status >= 400:
            raise RuntimeError("target rejected")
        page.emulate_media(media=str(payload["media"]))
        if payload["format"]=="pdf":
            page.pdf(path=str(Path(payload["output"])),print_background=True,prefer_css_page_size=True)
        else:
            page.screenshot(path=str(Path(payload["output"])),full_page=True)
        context.close(); browser.close()
    return 0
if __name__=="__main__":
    raise SystemExit(main())
