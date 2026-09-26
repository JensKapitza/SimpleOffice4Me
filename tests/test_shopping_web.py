import io
import tempfile
import unittest
from pathlib import Path

from flask import Flask, g
from PIL import Image

from app.shopping_store import ShoppingStore
from app.shopping_web import bp


class ShoppingWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="shopping-test", DOCUMENT_ROOT=str(self.root))
        self.app.register_blueprint(bp)
        self.user = {"username": "alice"}
        self.app.before_request(lambda: setattr(g, "user", self.user))
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_create_list_and_item_through_http(self):
        response = self.client.post("/shopping/lists", data={"name": "Woche", "store": "Lidl"})
        self.assertEqual(302, response.status_code)
        store = ShoppingStore(self.root)
        shopping = store.lists("alice")[0]
        response = self.client.post(
            f"/shopping/lists/{shopping['list_id']}/items",
            data={"name": "Milch", "barcode": "4006381333931", "quantity": "2", "unit": "l"},
        )
        self.assertEqual(302, response.status_code)
        self.assertEqual("Milch", store.items("alice", list_id=shopping["list_id"])[0]["name"])

    def test_barcode_lookup_reuses_only_visible_local_knowledge(self):
        store = ShoppingStore(self.root)
        store.create_list("Privat", "alice", list_id="private")
        store.add_item("private", "Vollmilch", "alice", {"barcode": "4006381333931"})

        response = self.client.get("/shopping/barcode?code=4006381333931")
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["known"])
        self.assertEqual("Vollmilch", response.get_json()["item"]["name"])
        self.assertEqual("private, no-store", response.headers["Cache-Control"])

        self.user = {"username": "bob"}
        response = self.client.get("/shopping/barcode?code=4006381333931")
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.get_json()["known"])

    def test_invalid_barcode_is_rejected(self):
        response = self.client.get("/shopping/barcode?code=4006381333932")
        self.assertEqual(400, response.status_code)

    def test_ui_has_camera_decoder_manual_fallback_and_navigation(self):
        project = Path(__file__).parents[1]
        template = (project / "templates" / "shopping" / "index.html").read_text(encoding="utf-8")
        script = (project / "static" / "js" / "shopping.js").read_text(encoding="utf-8")
        nav = (project / "templates" / "documents" / "nav.html").read_text(encoding="utf-8")
        bootstrap = (project / "app" / "__init__.py").read_text(encoding="utf-8")

        self.assertIn('id="shopping-barcode"', template)
        self.assertIn("manuell eingegeben", template)
        self.assertIn("BarcodeDetector", script)
        self.assertIn("getUserMedia", script)
        self.assertIn("ean_13", script)
        self.assertIn("upc_e", script)
        self.assertIn("localStorage", script)
        self.assertIn("simpleoffice-shopping-offline-v1", script)
        self.assertIn("delete fields._csrf_token", script)
        self.assertIn("X-Shopping-Sync", script)
        self.assertIn("bleibt lokal vorgemerkt", script)
        self.assertIn('name="request_id"', template)
        self.assertIn('id="shopping-brand"', template)
        self.assertIn('id="shopping-product-photo"', template)
        self.assertIn('capture="environment"', template)
        self.assertIn('enctype="multipart/form-data"', template)
        self.assertIn('id="shopping-photo-preview"', template)
        self.assertIn("photo_url", script)
        self.assertIn("URL.createObjectURL", script)
        self.assertIn("product_photo", script)
        self.assertIn("Noch einmal hinzufügen", template)
        self.assertIn("shopping.index", nav)
        self.assertIn("app.register_blueprint(shopping_web.bp)", bootstrap)

    def test_product_photo_is_stored_locally_reused_by_barcode_and_access_controlled(self):
        store = ShoppingStore(self.root)
        store.create_list("Fotos", "alice", list_id="photos")

        image = io.BytesIO()
        Image.new("RGB", (32, 24), "white").save(image, format="PNG")
        image.seek(0)
        response = self.client.post(
            "/shopping/lists/photos/items",
            data={
                "name": "Joghurt",
                "barcode": "4006381333931",
                "product_photo": (image, "joghurt.png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(302, response.status_code)

        item = store.items("alice", list_id="photos")[0]
        photo_id = item.get("photo_id", "")
        self.assertRegex(photo_id, r"^[0-9a-f]{32}\.jpg$")
        photo_path = store.photo_dir / photo_id
        self.assertTrue(photo_path.is_file())

        photo = self.client.get(f"/shopping/photos/{photo_id}")
        self.assertEqual(200, photo.status_code)
        self.assertEqual("image/jpeg", photo.mimetype)
        self.assertIn("private", photo.headers.get("Cache-Control", ""))

        lookup = self.client.get("/shopping/barcode?code=4006381333931").get_json()
        self.assertTrue(lookup["known"])
        self.assertEqual(photo_id, lookup["item"]["photo_id"])
        self.assertIn(f"/shopping/photos/{photo_id}", lookup["item"]["photo_url"])

        self.user = {"username": "bob"}
        denied = self.client.get(f"/shopping/photos/{photo_id}")
        self.assertEqual(404, denied.status_code)


    def test_sync_add_returns_json_and_rejects_invalid_data_without_redirect(self):
        store = ShoppingStore(self.root)
        store.create_list("Offline", "alice", list_id="sync")

        response = self.client.post(
            "/shopping/lists/sync/items",
            data={"name": "Milch", "request_id": "sync-1"},
            headers={"X-Shopping-Sync": "1"},
        )
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(1, len(store.items("alice", list_id="sync")))

        response = self.client.post(
            "/shopping/lists/sync/items",
            data={"name": "Fehler", "barcode": "4006381333932", "request_id": "sync-2"},
            headers={"X-Shopping-Sync": "1"},
        )
        self.assertEqual(409, response.status_code)
        self.assertFalse(response.get_json()["ok"])
        self.assertEqual(1, len(store.items("alice", list_id="sync")))

    def test_product_favorite_route_keeps_memory_private(self):
        store = ShoppingStore(self.root)
        store.create_list("Woche", "alice", list_id="favorite")
        store.add_item(
            "favorite",
            "Kaffee",
            "alice",
            {"barcode": "4006381333931", "brand": "Test"},
        )
        product = store.products("alice")[0]
        response = self.client.post(
            f"/shopping/products/{product['product_id']}/favorite",
            data={"favorite": "1", "list_id": "favorite"},
        )
        self.assertEqual(302, response.status_code)
        self.assertTrue(store.products("alice")[0]["favorite"])

        self.user = {"username": "bob"}
        response = self.client.post(
            f"/shopping/products/{product['product_id']}/favorite",
            data={"favorite": "0"},
        )
        self.assertEqual(302, response.status_code)
        self.assertTrue(store.products("alice")[0]["favorite"])

    def test_barcode_lookup_uses_private_product_memory_after_list_archive(self):
        store = ShoppingStore(self.root)
        store.create_list("Alt", "alice", list_id="archived-memory")
        store.add_item(
            "archived-memory",
            "Reis",
            "alice",
            {"barcode": "4006381333931", "brand": "Hausmarke", "pack_size": "1 kg"},
        )
        store.archive_list("archived-memory", "alice")
        response = self.client.get("/shopping/barcode?code=4006381333931")
        payload = response.get_json()
        self.assertEqual(200, response.status_code)
        self.assertTrue(payload["known"])
        self.assertEqual("Hausmarke", payload["item"]["brand"])
        self.assertEqual("1 kg", payload["item"]["pack_size"])


if __name__ == "__main__":
    unittest.main()
