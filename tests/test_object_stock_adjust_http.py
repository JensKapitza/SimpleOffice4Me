"""Blackbox: reconcile counted stock against recorded quantity (Issue #1111)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.object_store import ObjectStore


class ObjectStockAdjustHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
        app.config.update(
            TESTING=True, TEST_CSRF_PROTECTION=False,
            DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(base / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "stocker", "password": "stocker-password-123"})
        self.client.post("/auth/login", data={"username": "stocker", "password": "stocker-password-123"})
        self.root = base / "documents"
        self.objects = ObjectStore(self.root)
        self.item = self.objects.create(
            {"name": "Schrauben M4", "type": "part", "quantity": "10", "unit": "Stk"}, "stocker")

    def tearDown(self):
        app.config.update(self.saved)

    def adjust(self, **data):
        return self.client.post(f"/inventory/{self.item['object_id']}/stock", data=data, follow_redirects=True)

    def test_adjustment_updates_quantity_and_keeps_audit_trail(self):
        response = self.adjust(counted="7", reason="schwund", note="Kiste halb leer")
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("Stock corrected to 7 (was: 10).", html)
        stored = self.objects.object(self.item["object_id"])
        self.assertEqual("7", stored["quantity"])
        self.assertEqual(1, len(stored["stock_adjustments"]))
        event = stored["stock_adjustments"][0]
        self.assertEqual("10", event["previous"])
        self.assertEqual("7", event["counted"])
        self.assertEqual("schwund", event["reason"])
        self.assertEqual("stocker", event["adjusted_by"])
        self.assertIn("Kiste halb leer", html)
        self.assertIn("Fehlmenge kl", html)

    def test_adjustment_requires_login(self):
        anonymous = app.test_client()
        response = anonymous.post(
            f"/inventory/{self.item['object_id']}/stock",
            data={"counted": "5", "reason": "zaehlfehler"}, follow_redirects=False)
        self.assertIn(response.status_code, (302, 401, 403))
        self.assertEqual("10", self.objects.object(self.item["object_id"])["quantity"])

    def test_invalid_reason_and_quantity_are_rejected(self):
        response = self.adjust(counted="9", reason="geklaut")
        self.assertIn("invalid stock adjustment reason", response.get_data(as_text=True))
        response = self.adjust(counted="-3", reason="korrektur")
        self.assertIn("must not be negative", response.get_data(as_text=True))
        response = self.adjust(counted="viele", reason="korrektur")
        self.assertIn("must be a number", response.get_data(as_text=True))
        stored = self.objects.object(self.item["object_id"])
        self.assertEqual("10", stored["quantity"])
        self.assertEqual([], stored["stock_adjustments"])

    def test_counted_mode_also_works_as_json(self):
        response = self.client.post(
            f"/inventory/{self.item['object_id']}/stock",
            json={"counted": "6", "reason": "zaehlfehler", "note": "JSON-Abgleich"})
        self.assertEqual(200, response.status_code)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual("6", body["quantity"])
        self.assertEqual("zaehlfehler", body["reason"])
        stored = self.objects.object(self.item["object_id"])
        self.assertEqual("6", stored["quantity"])
        self.assertEqual("10", stored["stock_adjustments"][0]["previous"])

    def test_numeric_zero_is_a_valid_count_and_nan_is_rejected(self):
        endpoint = f"/inventory/{self.item['object_id']}/stock"
        invalid = self.client.post(endpoint, json={"counted": "NaN", "reason": "korrektur"})
        self.assertEqual(400, invalid.status_code)
        response = self.client.post(endpoint, json={"counted": 0, "reason": "schwund"})
        self.assertEqual(200, response.status_code)
        self.assertEqual("0", response.get_json()["quantity"])
        events = self.objects.object(self.item["object_id"])["stock_adjustments"]
        self.assertEqual(["0"], [event["counted"] for event in events])

    def test_empty_counted_falls_back_to_delta_mode(self):
        response = self.client.post(
            f"/inventory/{self.item['object_id']}/stock",
            data={"counted": "", "delta": "-2", "reason": "Werkstatt"},
            follow_redirects=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("8 Stk", response.get_data(as_text=True))
        stored = self.objects.object(self.item["object_id"])
        self.assertEqual("8", stored["quantity"])
        self.assertEqual([], stored["stock_adjustments"])

    def test_adjustment_history_survives_reload(self):
        self.adjust(counted="8", reason="zaehlfehler")
        self.adjust(counted="8", reason="korrektur", note="Nachkontrolle ok")
        response = self.client.get(f"/inventory/{self.item['object_id']}")
        html = response.get_data(as_text=True)
        self.assertIn("zaehlfehler", html)
        self.assertIn("Nachkontrolle ok", html)
        stored = ObjectStore(self.root).object(self.item["object_id"])
        self.assertEqual(2, len(stored["stock_adjustments"]))
        self.assertEqual("8", stored["quantity"])
