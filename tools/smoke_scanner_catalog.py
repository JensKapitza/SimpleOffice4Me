"""Run scanner catalog reconciliation over a disposable real TLS instance.

The catalog/provider and model outputs are synthetic. The app routes,
login, CSRF, persistence, consent, confirmation and Tesseract are real.
No model runtime, real accounts, network providers or device are needed.
"""
from __future__ import annotations

import json
import secrets
import ssl
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError

if not __package__:  # The disposable child launches this file directly.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ISBN = "9780140328721"
OTHER = "9780261103573"
TITLE = "Synthetic exact edition"
ENTRY = "/inventory/capture/intake"

def serve():
    from tools import smoke_federation_loopback as tls

    root = Path(sys.argv[2])
    sys.path.insert(0, str(root / "checkout"))
    from app import inventory_catalog, inventory_intake_routes
    from app.inventory_intake_evidence import proposal_from_ids, proposal_evidence
    real_catalog = inventory_catalog.lookup_catalog

    def provider(source, code):
        if source == "openlibrary":
            return {f"ISBN:{code}": {"title": TITLE, "publishers": [{"name": "Example Press"}],
                "publish_date": "1988", "edition_name": "First printing", "physical_format": "Paperback",
                "identifiers": {"isbn_13": [code]}}}
        return {"totalItems": 1, "items": [{"volumeInfo": {"title": TITLE, "publisher": "Example Press",
            "publishedDate": "1988-11-01" if code == ISBN else "1999",
            "industryIdentifiers": [{"type": "ISBN_13", "identifier": code}]}}]}

    def sources(*args, **kwargs):
        return real_catalog(*args, **kwargs, book_fetch=provider)

    def local_selection(state, config):
        rows = proposal_evidence(state)
        refs = {row["field"]: row["source_id"] for row in rows
                if (row.get("provider"), row["field"]) in
                    {("openlibrary", "edition"), ("google_books", "published_date")}}
        if set(refs) != {"edition", "published_date"}:
            raise ValueError("No compatible synthetic evidence")
        return proposal_from_ids(state, refs)

    inventory_catalog.lookup_catalog = sources
    inventory_intake_routes.local_proposal = local_selection
    tls.serve(root, sys.argv[3], int(sys.argv[4]), Path(sys.argv[5]))

def exercise(browser, document_root):
    from app.object_store import ObjectStore
    from tools.smoke_book_scans import image_file

    csrf = browser.csrf(ENTRY)

    def post(path, fields):
        return json.loads(browser.request(path, fields={"_csrf_token": csrf, **fields},
                                         headers={"Accept": "application/json"}))["draft"]

    def draft(barcode, label=None):
        token = browser.page(ENTRY).select_one('#intake-capture input[name="capture_token"]')["value"]
        fields = {"capture_token": token, "barcode": barcode, "title": TITLE, "kind": "book"}
        if label is None:
            return post(ENTRY, fields)
        boundary = "SyntheticBookScanProof"
        data = b"".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
            for key, value in {"_csrf_token": csrf, **fields}.items()
        )
        data += (f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="book.png"\r\n'
                 'Content-Type: image/png\r\n\r\n').encode() + image_file(label) + f'\r\n--{boundary}--\r\n'.encode()
        return json.loads(browser.request(ENTRY, data=data, headers={"Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}"}))["draft"]

    def action(draft, verb, **fields):
        return post(f"{ENTRY}/{draft['id']}/{verb}", {"revision": draft["revision"], **fields})

    def blocked(expected, callback):
        try:
            callback()
        except HTTPError as error:
            assert error.code == expected, error.code
            error.close()
        else:
            raise AssertionError("Unexpected accepted request")

    matched = action(draft(ISBN), "catalog", catalog_consent="1")
    assert matched["catalog"]["status"] == "confirmed"
    proposal = action(matched, "proposal")
    rows = proposal["proposal"]["fields"]
    assert {(r["field"], r["value"]) for r in rows} == {("edition", "First printing"), ("published_date", "1988-11-01")}
    assert all(r["supports"][0].startswith(r["candidate_id"]) for r in rows)
    assert not proposal["selected"] and not proposal["confirmed"]
    for row in rows:
        proposal = action(proposal, "choose", source_id=row["source_id"])
    blocked(409, lambda: action(matched, "confirm"))
    for initial in ("ocr", "catalog"):
        first = draft(ISBN, "Verlag: Other Imprint")
        if initial == "ocr":
            first = action(first, "ocr")
        current = action(first, "catalog", catalog_consent="1")
        if initial == "catalog":
            assert current["catalog"]["status"] == "confirmed"
            current = action(current, "ocr")
        assert current["catalog"]["reason"] == "labelled_ocr_differs", current["catalog"]["reason"]
        assert current["catalog"]["recommended"] is None and len(current["catalog"]["candidates"]) == 2
        assert "Other Imprint" in current["ocr"]["text"]
        blocked(422, lambda: action(current, "proposal"))
    mismatch = action(draft(OTHER), "catalog", catalog_consent="1")
    assert mismatch["catalog"]["reason"] == "conflicting_edition"
    blocked(422, lambda: action(mismatch, "proposal"))
    assert ObjectStore(document_root).objects() == [], "Inventory changed before explicit confirmation"
    saved = action(proposal, "confirm")
    assert saved["confirmed"]["fields"]["edition"] == "First printing"
    assert saved["confirmed"]["fields"]["published_date"] == "1988-11-01"
    objects = ObjectStore(document_root).objects()
    assert len(objects) == 1 and objects[0]["object_id"] == saved["confirmed"]["object_id"]

def main():
    if not __debug__:
        return 1
    if len(sys.argv) == 6 and sys.argv[1] == "--serve":
        serve()
        return 0
    if len(sys.argv) != 1:
        print("Usage: python -m tools.smoke_scanner_catalog", file=sys.stderr)
        return 2

    from tools import smoke_federation_loopback as tls
    from tools.smoke_book_scans import require_stock_recognition

    require_stock_recognition()
    tls.__file__ = str(Path(__file__).resolve())
    with TemporaryDirectory(prefix="scanner-catalog-real-tls-") as tmp:
        root = Path(tmp)
        cert = root / "cert"
        cert.mkdir()
        tls.make_certificates(cert)
        ssl_context = ssl.create_default_context(cafile=str(cert / "ca.crt"))
        peer = root / "instance"
        tls.initialize(peer)
        with tls.instance(peer, tls.PEERS[0], cert, ssl_context, secrets.token_urlsafe(24),
                          secrets.token_urlsafe(32)) as (browser, _port):
            exercise(browser, peer / "documents")
    print("Verified real TLS: exact two-source selection, simulated catalog/model transport, stock labelled OCR "
          "in both orders, distinct edition and stale rejection, one explicit inventory write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
