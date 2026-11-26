"""Exercise the actual authenticated mixed scanner page with synthetic photos."""

from __future__ import annotations

import io
import json
import os
import shutil
import uuid
from unittest import mock
import unittest

from bs4 import BeautifulSoup

from app import app, inventory_catalog
from app.capture_inbox_store import CaptureInboxStore
from app.inventory_catalog import lookup_catalog
from app.inventory_intake_evidence import local_proposal, proposal_from_ids
from app.object_store import ObjectStore
from tests import test_inventory_capture_persist as fixture
from tools.smoke_book_scans import image_file


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
        self.assertIsNone(page.select_one("#intake-pending img"))
        self.assertIsNotNone(page.select_one("#intake-pending [data-intake-pending-photo][hidden]"))
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
        draft = self.photo(image_file("Marke: Synthetic Pencil"))
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

    def test_catalog_cache_follows_google_credential_rotation_for_same_barcode(self):
        code = "9780140328721"
        key_a, key_b = "synthetic-google-alpha", "synthetic-google-beta"
        inventory_catalog._cache.clear()
        self.addCleanup(inventory_catalog._cache.clear)
        google_calls = []

        def provider(host, path, *, headers=None):
            if host == "openlibrary.org":
                return {}
            self.assertEqual(host, "www.googleapis.com")
            self.assertIn(code, path)
            key = headers["X-Goog-Api-Key"]
            google_calls.append(key)
            title = "First Google Fixture" if key == key_a else "Second Google Fixture"
            return {"totalItems": 1, "items": [{"volumeInfo": {"title": title, "publisher": "Synthetic",
                "industryIdentifiers": [{"type": "ISBN_13", "identifier": code}]}}]}

        with mock.patch("app.inventory_catalog_http.get_json", side_effect=provider):
            for key, title in ((key_a, "First Google Fixture"), (key_a, "First Google Fixture"),
                               (key_b, "Second Google Fixture"), (key_a, "First Google Fixture")):
                with mock.patch.dict(os.environ, {"SIMPLEOFFICE_GOOGLE_BOOKS_API_KEY": key}):
                    draft = self.photo(barcode=code, kind="book")
                    response = self.post(f'/{draft["id"]}/catalog', revision="1", catalog_consent="1")
                self.assertEqual(response.status_code, 200)
                evidence = response.json["draft"]["evidence"]
                self.assertIn(title, [row["value"] for row in evidence if row["field"] == "title"])
                self.assertNotIn(key_a, response.get_data(as_text=True))
                self.assertNotIn(key_b, response.get_data(as_text=True))
        self.assertEqual([key_a, key_b], google_calls)
        self.assertEqual([], ObjectStore(self.documents).objects())

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


    def review_sources(self, draft, *, different=False):
        from tests.test_inventory_catalog import books, google_volume, open_record

        responses, _ = books(open_record(edition_name="First printing", physical_format="Paperback"),
                             google_volume(publishedDate="1999-01-12" if different else "1988-11-01"))
        with mock.patch("app.inventory_catalog.lookup_catalog",
                        side_effect=lambda *a, **kw: lookup_catalog(*a, **kw, book_fetch=responses)):
            return self.post(f'/{draft["id"]}/catalog', revision=str(draft["revision"]), catalog_consent="1")

    def observe_label(self, draft, text):
        with mock.patch("app.object_vision._run_tesseract", return_value={"status": "completed", "text": text}):
            reply = self.post(f'/{draft["id"]}/ocr', revision=str(draft["revision"]))
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.json["draft"]

    def test_same_edition_complementary_pick_keeps_actual_source_ids_until_explicit_save(self):
        from tests.test_inventory_catalog import ISBN

        draft = self.photo(barcode=ISBN, title="A Book: Ä Edition", kind="book")
        draft = self.observe_label(draft, "A possible different title line")  # Unlabelled is only a hint.
        review = self.review_sources(draft)
        self.assertEqual(200, review.status_code, review.data)
        reviewed = review.json["draft"]
        self.assertEqual("confirmed", reviewed["catalog"]["status"])
        self.assertEqual({"manual", "ocr", "external_catalog"}, {r["kind"] for r in reviewed["evidence"]})
        self.assertTrue(any(row.get("unlabelled") for row in reviewed["evidence"]))
        base = f'/{reviewed["id"]}'
        refs = {}
        for field, provider in (("edition", "openlibrary"), ("published_date", "google_books")):
            refs[field] = next(row["source_id"] for row in reviewed["evidence"]
                               if row["field"] == field and row.get("provider") == provider)
        with mock.patch("app.inventory_intake_routes.local_proposal",
                        side_effect=lambda state, _: proposal_from_ids(state, refs)):
            proposal = self.post(base + "/proposal", revision=str(reviewed["revision"]))
        self.assertEqual(200, proposal.status_code, proposal.data)
        current = proposal.json["draft"]
        self.assertEqual({"manual", "ocr", "external_catalog", "local_ai"}, {r["kind"] for r in current["evidence"]})
        rows = current["proposal"]["fields"]
        self.assertEqual({r["field"]: r["supports"] for r in rows}, {k: [v] for k, v in refs.items()})
        self.assertEqual({}, current["selected"])
        self.assertEqual("A Book: Ä Edition", current["manual"]["title"])
        sources = self.get_page().select_one(f'#draft-{current["id"]} [data-intake-sources]')
        self.assertFalse(sources.has_attr("open"))
        self.assertEqual([], current["conflict_fields"])
        title = sources.select_one('[data-intake-source-field="title"]')
        self.assertNotIn("Abweichende Angaben", title.get_text(" ", strip=True))
        title_sources = title.select('[data-intake-source-kind]')
        self.assertEqual(["Manuell", "Open Library", "Google Books", "OCR (mögliche Titelzeile)"],
                         [node.select_one(".badge").get_text(strip=True) for node in title_sources])
        self.assertEqual(2, sum(node.select_one('[translate="no"]').get_text(strip=True) == "A Book: Ä Edition"
                                for node in title_sources))
        self.assertIn("Titel nicht eindeutig erkannt", title_sources[-1].get_text(" ", strip=True))
        edition = sources.select_one('[data-intake-source-field="edition"]')
        self.assertIn("Belegt durch Open Library",
                      edition.select_one('[data-intake-source-kind="local_ai"]').get_text(" ", strip=True))
        self.assertEqual([], ObjectStore(self.documents).objects())
        for row in rows:
            r = self.post(base + "/choose", revision=str(current["revision"]), source_id=row["source_id"])
            self.assertEqual(200, r.status_code, r.data)
            current = r.json["draft"]
        self.assertEqual([], ObjectStore(self.documents).objects())
        visible = self.get_page().select_one(f'#draft-{current["id"]} [data-intake-sources]')
        selected_edition = visible.select_one('[data-intake-source-field="edition"] [data-intake-source-kind="local_ai"]')
        self.assertIn("Ausgewählt", selected_edition.get_text(" ", strip=True))
        self.assertIsNone(selected_edition.select_one('button[name="source_id"]'))
        stale = self.post(base + "/confirm", revision=str(reviewed["revision"]))
        self.assertEqual(409, stale.status_code)
        saved = self.post(base + "/confirm", revision=str(current["revision"]), publisher="", edition="", published_date="")
        self.assertEqual(200, saved.status_code, saved.data)
        self.assertEqual("First printing", saved.json["draft"]["confirmed"]["fields"]["edition"])
        self.assertEqual("1988-11-01", saved.json["draft"]["confirmed"]["fields"]["published_date"])
        self.assertEqual(1, len(ObjectStore(self.documents).objects()))

    def test_card_and_source_comparison_distinguish_title_conflicts_from_hints(self):
        for text, conflict in (("Other cover line", False),
                               ("Titel: A BOOK: A\u0308 EDITION", False),
                               ("Titel: Different title", True)):
            with self.subTest(text=text):
                draft = self.photo(title="A Book: Ä Edition", kind="book")
                draft = self.observe_label(draft, text)
                self.assertEqual({}, draft["catalog"])
                self.assertEqual(["title"] if conflict else [], draft["conflict_fields"])
                self.assertEqual("question" if conflict else "in_review", draft["state"])
                card = self.get_page().select_one(f'#draft-{draft["id"]}')
                self.assertEqual("Rückfrage: Titel" if conflict else "In Prüfung",
                                 card.select_one("[data-intake-state]").get_text(" ", strip=True))
                source_panel = card.select_one("[data-intake-sources]")
                self.assertEqual(conflict, source_panel.has_attr("open"))
                rows = source_panel.select('[data-intake-source-field="title"] li')
                self.assertEqual(2, len(rows))
                self.assertEqual("A Book: Ä Edition", card.select_one('input[name="title"]')["value"])
        self.assertEqual([], ObjectStore(self.documents).objects())

    def test_labelled_ocr_conflict_controls_review_in_both_orders(self):
        from tests.test_inventory_catalog import ISBN

        for order in ("ocr-first", "catalog-first"):
            with self.subTest(order=order):
                draft = self.photo(barcode=ISBN, kind="book")
                if order == "ocr-first":
                    draft = self.observe_label(draft, "Verlag: Other Imprint")
                review = self.review_sources(draft)
                self.assertEqual(200, review.status_code, review.data)
                current = review.json["draft"]
                if order == "catalog-first":
                    self.assertEqual("confirmed", current["catalog"]["status"])
                    current = self.observe_label(current, "Verlag: Other Imprint")
                self.assertEqual("question", current["state"])
                self.assertEqual("labelled_ocr_differs", current["catalog"]["reason"])
                self.assertIsNone(current["catalog"]["recommended"])
                self.assertEqual(2, len(current["catalog"]["candidates"]))
                self.assertEqual("Other Imprint", current["ocr"]["fields"][0]["value"])
                self.assertIn("widerspricht dem Katalog", self.get_page().get_text(" ", strip=True))
                external = next(row for row in current["evidence"] if row["kind"] == "external_catalog")
                with mock.patch("app.inventory_intake_routes.local_proposal",
                                side_effect=lambda state, _: proposal_from_ids(state, {external["field"]: external["source_id"]})):
                    blocked = self.post(f'/{current["id"]}/proposal', revision=str(current["revision"]))
                self.assertEqual(422, blocked.status_code)
        self.assertEqual([], ObjectStore(self.documents).objects())

    def test_conflicting_editions_not_proposed_and_late_conflict_does_not_save_mixed_selection(self):
        from tests.test_inventory_catalog import ISBN

        draft = self.photo(barcode=ISBN, kind="book", title="A Book: Ä Edition")
        wrong = self.review_sources(draft, different=True).json["draft"]
        self.assertEqual("conflicting_edition", wrong["catalog"]["reason"])
        self.assertIn("published_date", wrong["conflict_fields"])
        card = self.get_page().select_one(f'#draft-{wrong["id"]}')
        self.assertIn("Erscheinungsjahr", card.select_one("[data-intake-state]").get_text(" ", strip=True))
        wrong_ref = next(r for r in wrong["evidence"] if r["kind"] == "external_catalog" and r["field"] == "published_date")
        with mock.patch("app.inventory_intake_routes.local_proposal", side_effect=lambda state, _:
                        proposal_from_ids(state, {"published_date": wrong_ref["source_id"]})):
            self.assertEqual(422, self.post(f'/{wrong["id"]}/proposal', revision=str(wrong["revision"])).status_code)
        current = self.review_sources(wrong).json["draft"]
        route = f'/{current["id"]}'
        for field, provider in (("edition", "openlibrary"), ("published_date", "google_books")):
            row = next(r for r in current["evidence"] if r["field"] == field and r.get("provider") == provider)
            chosen = self.post(route + "/choose", revision=str(current["revision"]), source_id=row["source_id"])
            self.assertEqual(200, chosen.status_code, chosen.data)
            current = chosen.json["draft"]
        current = self.observe_label(current, "Jahr: 1997")
        self.assertEqual({"edition", "published_date"}, set(current["selected"]))
        self.assertFalse(current["compatible_selection"])
        refused = self.post(route + "/confirm", revision=str(current["revision"]))
        self.assertEqual(409, refused.status_code, refused.data)
        self.assertEqual([], ObjectStore(self.documents).objects())
        clear = self.post(route + "/choose", revision=str(current["revision"]), clear_field="published_date")
        self.assertEqual(200, clear.status_code, clear.data)
        self.assertTrue(clear.json["draft"]["compatible_selection"])
        edit = self.post(route + "/edit", revision=str(clear.json["draft"]["revision"]), published_date="1997")
        self.assertEqual(200, edit.status_code, edit.data)
        self.assertTrue(edit.json["draft"]["catalog"]["stale"])
        self.assertEqual("1997", edit.json["draft"]["manual"]["published_date"])
        self.assertEqual([], ObjectStore(self.documents).objects())
