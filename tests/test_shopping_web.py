import tempfile
import unittest
from pathlib import Path

from flask import Flask, g

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
        self.assertIn("shopping.index", nav)
        self.assertIn("app.register_blueprint(shopping_web.bp)", bootstrap)


if __name__ == "__main__":
    unittest.main()
