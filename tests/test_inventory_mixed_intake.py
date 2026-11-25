"""Exercise the actual authenticated mixed scanner page with synthetic photos."""

from __future__ import annotations

import io
import json
import shutil
import uuid
from unittest import mock
import unittest

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

from app import app
from app.capture_inbox_store import CaptureInboxStore
from app.inventory_catalog import lookup_catalog
from app.inventory_intake_evidence import local_proposal, proposal_from_ids
from app.object_store import ObjectStore
from tests import test_inventory_capture_persist as fixture


class MixedIntakeTests(unittest.TestCase):
    setup = fixture.CapturePersistRouteTests
    password = setup.password
    keys = setup.keys
    setUp = setup.setUp
    tearDown = setup.tearDown
    login = setup.login

    path = "/inventory/capture/intake"

    def get_page(self, who=None):
        page = (who or self.client).get(self.path)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        return BeautifulSoup(page.data, "html.parser")

    def post(self, suffix="", who=None, csrf=True, **fields):
        who = who or self.client
        if csrf:
            fields["_csrf_token"] = self.get_page(who).select_one('input[name="_csrf_token"]')["value"]
        return who.post(self.path + suffix, data=fields, headers={"Accept": "application/json"})

    def photo(self, data=fixture.PNG_1PX, **fields):
        result = self.post(photo=(io.BytesIO(data), "synthetic.png"),
                           capture_token=uuid.uuid4().hex, **fields)
        self.assertEqual(result.status_code, 201, result.data)
        return result.json["draft"]

    def test_capture_private_without_analysis_and_ordered_on_reload(self):
        page = self.get_page()
        self.assertEqual(len(page.select("article")), 0)
        with mock.patch("app.object_vision._run_tesseract") as ocr,                 mock.patch("app.inventory_catalog.lookup_catalog") as external:
            first = self.photo(barcode="9780140328721")
            second = self.photo()
        ocr.assert_not_called()
        external.assert_not_called()
        ids = [first["id"], second["id"]]
        self.assertEqual([a["id"] for a in self.get_page().select("article")], ["draft-" + id_ for id_ in ids])
        self.assertEqual(self.get_page(self.bob).select("article"), [])
        self.assertEqual(self.bob.get(first["preview_url"]).status_code, 404)
        self.assertEqual(self.post(f'/{first["id"]}/edit', who=self.bob, revision="1", title="No").status_code, 404)
        self.assertEqual(self.post(f'/{first["id"]}/edit', csrf=False, revision="1", title="No").status_code, 403)
        self.assertEqual(ObjectStore(self.documents).objects(), [])

    def test_local_ocr_result_does_not_replace_manual_or_confirm(self):
        draft = self.photo()
        url = f'/{draft["id"]}'
        store = CaptureInboxStore(self.documents)
        def late(_path, *, timeout):
            from app.capture_intake_state import edit_draft

            store.update_intake(draft["id"], "anna", 1, lambda d: edit_draft(d, {"title": "Manual first"}))
            return {"status": "completed", "text": "Titel: Old OCR"}
        with mock.patch("app.object_vision._run_tesseract", side_effect=late):
            result = self.post(url + "/ocr", revision="1")
        self.assertEqual(result.status_code, 409, result.data)
        self.assertEqual(store.get(draft["id"], "anna")["intake"]["manual"]["title"], "Manual first")
        self.assertEqual(store.get(draft["id"], "anna")["intake"]["ocr"], {})

    @unittest.skipUnless(shutil.which("tesseract"), "stock Tesseract unavailable")
    def test_real_local_ocr_one_photo_then_explicit_private_confirm(self):
        photo = Image.new("RGB", (820, 200), "white")
        font = ImageFont.truetype("DejaVuSans.ttf", 50)
        ImageDraw.Draw(photo).text((20, 40), "Marke: Synthetic Pencil", font=font, fill="black")
        png = io.BytesIO()
        photo.save(png, format="PNG")
        draft = self.photo(png.getvalue())
        url = f'/{draft["id"]}'
        read = self.post(url + "/ocr", revision="1")
        self.assertEqual(read.status_code, 200, read.data)
        evidence = read.json["draft"]["evidence"]
        self.assertEqual([(row["label"], row["value"]) for row in evidence], [("OCR", "Synthetic Pencil")])
        revision_after_ocr = str(read.json["draft"]["revision"])
        self.assertEqual(revision_after_ocr, "2")
        selected = self.post(url + "/choose", revision=revision_after_ocr, source_id=evidence[0]["source_id"])
        self.assertEqual(selected.status_code, 200, selected.data)
        revision = str(selected.json["draft"]["revision"])
        self.assertEqual(ObjectStore(self.documents).objects(), [])
        saved = self.post(url + "/confirm", revision=revision, kind="object", title="Pencil")
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.json["draft"]["state"], "confirmed")
        self.assertEqual(saved.json["draft"]["confirmed"]["fields"]["manufacturer"], "Synthetic Pencil")
        self.assertEqual(saved.json["draft"]["confirmed"]["sources"]["manufacturer"]["kind"], "ocr")
        objects = ObjectStore(self.documents).objects()
        self.assertEqual(len(objects), 1)
        self.assertFalse(objects[0]["share_inventory"])
        self.assertEqual(objects[0]["fields"]["manufacturer"], "Synthetic Pencil")
        replay = self.post(url + "/confirm", revision=revision, kind="object", title="Duplicate")
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(len(ObjectStore(self.documents).objects()), 1)

    def test_catalog_contract_sources_and_untrusted_proposal(self):
        title = "Fantastic Mr. Fox"
        code = "9780140328721"
        draft = self.photo(barcode=code, title=title, kind="book")
        route = f'/{draft["id"]}/catalog'
        called = []
        def provider(name, identifier):
            called.append((name, identifier))
            if name == "openlibrary":
                return {f"ISBN:{code}": {"identifiers": {"isbn_13": [code]}, "title": title,
                                       "publishers": [{"name": "Puffin"}], "publish_date": "1988"}}
            return {"items": [{"volumeInfo": {"title": title, "publisher": "Puffin", "publishedDate": "1988",
                "industryIdentifiers": [{"type":"ISBN_13", "identifier": code}]}}]}
        with mock.patch("app.inventory_catalog.lookup_catalog",
                        side_effect=lambda *a, **kw: lookup_catalog(*a, **kw, book_fetch=provider)):
            self.assertEqual(self.post(route, revision="1").status_code, 400)
            self.assertEqual(called, [])
            response = self.post(route, revision="1", catalog_consent="1")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(called, [("openlibrary", code), ("google_books", code)])
        evidence = response.json["draft"]["evidence"]
        self.assertEqual({x["label"] for x in evidence if x["field"] == "title"},
                         {"Manuell", "Open Library", "Google Books"})
        self.assertEqual(len({x["source_id"] for x in evidence if x["field"] == "title"}), 3)
        state = CaptureInboxStore(self.documents).get(draft["id"], "anna")["intake"]
        exact = proposal_from_ids(state, {"title":"manual:title"})
        self.assertEqual(exact[0]["value"], title)
        self.assertEqual(exact[0]["supports"], ["manual:title"])
        with self.assertRaises(ValueError):
            proposal_from_ids(state, {"publisher": "fabricated"})
        self.assertEqual(ObjectStore(self.documents).objects(), [])

    def test_verified_runtime_does_not_send_any_prompt_until_attested(self):
        draft = self.photo(title="Synthetic approved text")
        endpoint = f'/{draft["id"]}/proposal'
        state = CaptureInboxStore(self.documents).get(draft["id"], "anna")["intake"]
        attempts = []
        models = {"models": [{"name": "synthetic:latest", "model": "synthetic:latest",
                              "size": 1024, "digest": "a" * 64}]}
        config = {"CONTEXTFLOW_OLLAMA_MODEL": "synthetic", "CONTEXTFLOW_OLLAMA_URL": "http://127.0.0.1:11434"}

        def fake(status, tags=models):
            def answer(req, timeout):
                path = req.selector
                attempts.append((req.get_method(), path, req.data))
                payload = status if path == "/api/status" else tags
                if path == "/api/chat":
                    payload = {"message": {"content": '{"title":"manual:title"}'}, "done": True}
                return io.BytesIO((json.dumps(payload) + "\n").encode())
            return answer

        own = {key: app.config.get(key) for key in config}
        self.addCleanup(app.config.update, own)
        app.config.update(config)
        with mock.patch.dict("os.environ", {"SIMPLEOFFICE_INTAKE_LOCAL_OLLAMA": "1"}):
            for rejected in ({"cloud": {"disabled": False}}, {"cloud": {"disabled": "true"}}, {}):
                with mock.patch("app.contextflow.vision.local_urlopen", side_effect=fake(rejected)):
                    self.assertEqual(self.post(endpoint, revision="1").status_code, 422)
            self.assertEqual(attempts, [("GET", "/api/status", None)] * 3)
            attempts.clear()
            remote = {"models": [{**models["models"][0], "remote_host": "https://example.invalid"}]}
            trusted = {"cloud": {"disabled": True}}
            with mock.patch("app.contextflow.vision.local_urlopen", side_effect=fake(trusted, remote)):
                self.assertIsNone(local_proposal(state, config))
            self.assertEqual(attempts, [("GET", "/api/status", None), ("GET", "/api/tags", None)])
            attempts.clear()
            with mock.patch("app.contextflow.vision.local_urlopen", side_effect=fake(trusted)):
                result = self.post(endpoint, revision="1")
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual([v[:2] for v in attempts],
                             [("GET", "/api/status"), ("GET", "/api/tags"), ("POST", "/api/chat")])
            request_json = json.loads(attempts[2][2])
            self.assertEqual(request_json["model"], "synthetic:latest:local")
            self.assertNotIn("images", str(request_json))
            provenance = result.json["draft"]["proposal"]["fields"][0]
            self.assertEqual(provenance["label"], "Lokale KI")
            self.assertEqual(provenance["supports"], ["manual:title"])
            self.assertEqual(ObjectStore(self.documents).objects(), [])

    def test_encrypted_v2_refuses_new_drafts_before_any_sidecar(self):
        from pathlib import Path

        from app.document_store import DocumentStore
        from app.v2.cutover import activate_v2, prepare_shadow
        from app.v2.encrypted_cutover import encrypted_blob_cutover
        from app.v2.master_keys import MasterKeyProfileStore
        from app.v2.migration import create_migration_backup, transfer_legacy_documents
        from app.v2.runtime_keys import STORAGE_PROFILE_ID

        root = Path(self.documents)
        root.mkdir(exist_ok=True)
        (root / "normal.txt").write_text("synthetic", encoding="utf-8")
        DocumentStore(root).scan()
        backup = Path(self.temp.name) / "backup"
        create_migration_backup(root, backup)
        transfer_legacy_documents(root, backup)
        prepare_shadow(root, apply=True, acknowledge_local_plaintext=True)
        activate_v2(root, apply=True, acknowledge_local_plaintext=True)
        keys = MasterKeyProfileStore(root, "synthetic-intake-v2")
        password = "disposable-intake-key-not-for-production"
        keys.create(STORAGE_PROFILE_ID, password)
        master = keys.unlock_with_password(STORAGE_PROFILE_ID, password)
        self.assertTrue(encrypted_blob_cutover(root, master, apply=True)["ready"])
        self.assertIsNone(self.get_page().select_one("#intake-capture"))
        refused = self.post(photo=(io.BytesIO(fixture.PNG_1PX), "normal.png"),
                            capture_token=uuid.uuid4().hex)
        self.assertEqual(refused.status_code, 409)
        self.assertFalse(CaptureInboxStore(root).media_directory.exists())

    def test_one_old_catalog_response_cannot_replace_new_manual_title(self):
        draft = self.photo(barcode="9780140328721", title="Earlier")
        store = CaptureInboxStore(self.documents)
        def delayed(*args, **kwargs):
            from app.capture_intake_state import edit_draft

            store.update_intake(draft["id"], "anna", 1, lambda state: edit_draft(state, {"title": "Edited"}))
            return {"status": "review"}
        with mock.patch("app.inventory_catalog.lookup_catalog", side_effect=delayed):
            result = self.post(f'/{draft["id"]}/catalog', revision="1", catalog_consent="1")
        self.assertEqual(result.status_code, 409)
        stored = store.get(draft["id"], "anna")["intake"]
        self.assertEqual(stored["manual"]["title"], "Edited")
        self.assertEqual(stored["catalog"], {})

    def test_valid_barcode_only_has_private_placeholder_and_no_media_or_automatic_io(self):
        token = uuid.uuid4().hex
        code = "4006381333931"
        with mock.patch("app.object_vision._run_tesseract") as ocr, \
                mock.patch("app.inventory_catalog.lookup_catalog") as lookup:
            invalid = self.post(barcode="4006381333932", capture_token=token)
            self.assertEqual(invalid.status_code, 400)
            accepted = self.post(barcode=code, capture_token=token)
            self.assertEqual(accepted.status_code, 201, accepted.data)
            row = accepted.json["draft"]
            self.assertIsNone(row["preview_url"])
            self.assertEqual(row["manual"]["barcode"], code)
            replay = self.post(barcode=code, capture_token=token)
            self.assertEqual(replay.json["draft"]["id"], row["id"])
            clash = self.post(barcode="9780140328721", capture_token=token)
            self.assertEqual(clash.status_code, 409)
            self.assertEqual(self.post(f'/{row["id"]}/ocr', revision="1").status_code, 422)
            ocr.assert_not_called()
            lookup.assert_not_called()
        self.assertFalse(CaptureInboxStore(self.documents).media_directory.exists())
        tile = self.get_page().select_one("#draft-" + row["id"])
        self.assertIn("Ohne Foto", tile.get_text(" ", strip=True))
        self.assertIsNone(tile.select_one("img"))
        self.assertFalse(self.get_page(self.bob).select("article"))
        self.assertEqual(ObjectStore(self.documents).objects(), [])
        saved = self.post(f'/{row["id"]}/confirm', revision="1", title="Item without a photo", kind="object")
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.json["draft"]["state"], "confirmed")
        self.assertFalse(ObjectStore(self.documents).objects()[0]["share_inventory"])

    def test_new_catalog_evidence_invalidates_old_local_proposal(self):
        draft = self.photo(barcode="9780140328721", title="Same manual title")
        base = f'/{draft["id"]}'
        def while_computing(state, config):
            # A second tab completes its explicitly requested catalog lookup.
            response = self.post(base + "/catalog", revision="1", catalog_consent="1")
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.json["draft"]["revision"], 2)
            return proposal_from_ids(state, {"title": "manual:title"})
        scope = {"status": "review", "reason": "single_source", "providers": {}, "candidates": []}
        with mock.patch("app.inventory_catalog.lookup_catalog", return_value=scope), \
                mock.patch("app.inventory_intake_routes.local_proposal", side_effect=while_computing):
            late = self.post(base + "/proposal", revision="1")
        self.assertEqual(late.status_code, 409)
        state = CaptureInboxStore(self.documents).get(draft["id"], "anna")["intake"]
        self.assertEqual(state["catalog"]["status"], "review")
        self.assertEqual(state["proposal"], {})
