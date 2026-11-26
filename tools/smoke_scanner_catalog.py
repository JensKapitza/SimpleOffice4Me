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
PRODUCT = "036000291452"
UNREACHABLE = "042100005264"
PRODUCT_TITLE = "Synthetic mint tube"
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

    def product_provider(source, code):
        assert source == "ebay"
        if code == UNREACHABLE:
            raise OSError("Synthetic provider offline")
        assert code == PRODUCT
        return {"items": [
            {"gtin": PRODUCT, "title": PRODUCT_TITLE, "variant": "Mint", "size": "75", "unit": "ml"},
            {"gtin": UNREACHABLE, "title": "Unrelated package", "variant": "Mint", "size": "125", "unit": "ml"},
        ]}

    def sources(*args, **kwargs):
        return real_catalog(*args, **kwargs, book_fetch=provider, product_fetch=product_provider)

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

    def draft(barcode, label=None, **observed):
        token = browser.page(ENTRY).select_one('#intake-capture input[name="capture_token"]')["value"]
        fields = {"capture_token": token, "barcode": barcode, "title": TITLE, "kind": "book", **observed}
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

    book = draft(ISBN)
    same_size = draft(PRODUCT, title=PRODUCT_TITLE, kind="object", variant="Mint", size="75", unit="ml")
    different_size = draft(PRODUCT, title=PRODUCT_TITLE, kind="object", variant="Mint", size="125", unit="ml")
    no_barcode = draft("", "Titel: Synthetic unlabelled package", title="", kind="object")
    offline = draft(UNREACHABLE, title="Synthetic offline package", kind="object")
    batch = (book, same_size, different_size, no_barcode, offline)
    assert all(not row["catalog"] and not row["ocr"] and not row["confirmed"] for row in batch)
    assert [card["id"] for card in browser.page(ENTRY).select('article[id^="draft-"]')] == [
        f"draft-{row['id']}" for row in batch
    ]

    matching_product = action(same_size, "catalog", catalog_consent="1")
    assert matching_product["catalog"]["reason"] == "verify_variant"
    assert len(matching_product["catalog"]["candidates"]) == 1
    assert matching_product["catalog"]["candidates"][0]["variant_match"] == "matches_observed"
    assert not matching_product["book_suggestions"] and not matching_product["selected"]
    product_conflict = action(different_size, "catalog", catalog_consent="1")
    assert product_conflict["catalog"]["reason"] == "conflicting_variant" and "size" in product_conflict["conflict_fields"]
    assert product_conflict["manual"]["size"] == "125" and not product_conflict["selected"]
    assert not no_barcode["manual"]["barcode"] and browser.request(no_barcode["preview_url"])
    offline = action(offline, "catalog", catalog_consent="1")
    assert offline["catalog"]["reason"] == "provider_unavailable" and not offline["catalog"]["candidates"]

    objects = ObjectStore(document_root)
    assert objects.objects() == [], "Inventory changed during private capture and analysis"
    room = browser.page("/library")
    location_form = room.select_one('form[action="/library/locations"]')
    assert location_form is not None
    browser.submit(location_form, {"name": "Synthetic batch box", "temporary": "1"})
    card = browser.page(ENTRY).select_one(f'#draft-{offline["id"]}')
    location = next(option["value"] for option in card.select('select[name="location_id"] option')
                    if "Synthetic batch box" in option.get_text(" ", strip=True))
    assert not offline["manual"].get("location_id")
    offline = action(offline, "edit", title="Synthetic manual fallback", location_id=location)
    assert offline["manual"]["location_id"] == location and offline["manual"]["title"] == "Synthetic manual fallback"
    card = browser.page(ENTRY).select_one(f'#draft-{offline["id"]}')
    assert card.select_one('select[name="location_id"] option[selected]')["value"] == location
    assert not offline["confirmed"]

    matched = action(book, "catalog", catalog_consent="1")
    assert matched["catalog"]["status"] == "confirmed" and matched["book_suggestions"]
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
    before = objects.objects()
    assert [(row["type"], row["name"]) for row in before] == [("shelf", "Synthetic batch box")], (
        "Only the separately created location may exist before confirmation"
    )
    saved = action(proposal, "confirm")
    assert saved["confirmed"]["fields"]["edition"] == "First printing"
    assert saved["confirmed"]["fields"]["published_date"] == "1988-11-01"
    after = {row["object_id"]: row for row in objects.objects()}
    assert len(after) == 2 and after[before[0]["object_id"]] == before[0]
    assert after[saved["confirmed"]["object_id"]]["type"] == "book"

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
    print("Verified real TLS: ordered private book/product stack, exact package kept for review, size conflict, "
          "photo without barcode, simulated provider outage with manual edit and later box assignment, "
          "simulated catalog/model transport, stock labelled OCR in both arrival orders, "
          "and one explicit inventory write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
