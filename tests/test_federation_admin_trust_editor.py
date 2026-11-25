"""The Network peer editor round-trips stored local trust settings."""

import unittest

from bs4 import BeautifulSoup

from app.federation_catalog import FederationCatalog
from app.federation_trust_store import FederationTrustStore
from tests.federation_admin_ui_fixture import FederationAdminUiFixture


class FederationAdminTrustEditorTests(FederationAdminUiFixture, unittest.TestCase):
    network = "/admin/federation/peer-discovery"
    peer_id = "office-b"

    def setUp(self):
        super().setUp()
        self._remember_pairing_peer()
        self.trust = FederationTrustStore(self.root)
        self.trust.remember("new-office", "DE")
        self.catalog = FederationCatalog(self.root)

    def form_values(self, peer_id):
        response = self.client.get(self.network)
        self.assertEqual(200, response.status_code)
        page = BeautifulSoup(response.get_data(as_text=True), "html.parser")
        action = f"{self.network}/trust/{peer_id}"
        form = page.find("form", action=action)
        self.assertIsNotNone(form)
        values = {item["name"]: item["value"] for item in form.select("input[name][value]")}
        for select in form.select("select[name]"):
            # Match browser defaults when no option has a selected attribute.
            option = select.find("option", selected=True) or select.find("option")
            values[select["name"]] = option.get("value", option.get_text(strip=True))
        return action, values

    def seed_configured_peer(self):
        self.trust.set_trust(self.peer_id, "NORMAL", "VERIFIED_MUTUAL", "TRANSITIVE", 2)
        generation = "fixture-1"
        self.catalog.begin_inventory_index(self.peer_id, generation)
        self.catalog.ingest_inventory(self.peer_id, generation, [
            {"identifier_type": "isbn", "identifier": "9780141036144", "display_name": "Fixture book"},
        ])
        self.catalog.finish_inventory_index(self.peer_id, generation)
        self.assertEqual(1, len(self.catalog.remote_inventory_items(self.peer_id)))

    def assert_saved_settings(self, trust_level, max_hops):
        edge = self.trust.get_trust(self.peer_id)
        self.assertEqual(
            ("VERIFIED_MUTUAL", trust_level, "TRANSITIVE", max_hops),
            tuple(edge[name] for name in ("verification_state", "trust_level", "propagation", "max_hops")),
        )

    def test_changing_only_hops_preserves_verification_trust_and_the_inventory_cache(self):
        self.seed_configured_peer()
        action, submitted = self.form_values(self.peer_id)
        result = self.client.post(action, data={**submitted, "max_hops": "1"}, follow_redirects=True)
        self.assertEqual(200, result.status_code)
        self.assert_saved_settings("NORMAL", 1)
        self.assertEqual(1, len(self.catalog.remote_inventory_items(self.peer_id)))
        self.assertEqual("2", submitted["max_hops"])
        self.assertEqual(
            {"verification": "VERIFIED_MUTUAL", "trust_level": "NORMAL", "propagation": "TRANSITIVE"},
            {key: submitted[key] for key in ("verification", "trust_level", "propagation")},
        )
        _, reloaded = self.form_values(self.peer_id)
        self.assertEqual("1", reloaded["max_hops"])

    def test_peer_without_saved_trust_still_starts_with_safe_defaults(self):
        _, values = self.form_values("new-office")
        self.assertEqual(
            ("KNOWN_UNVERIFIED", "NONE", "DIRECT_ONLY", "0"),
            tuple(values[name] for name in ("verification", "trust_level", "propagation", "max_hops")),
        )
        self.assertIsNone(self.trust.get_trust("new-office"))

    def test_deliberately_revoking_trust_still_clears_the_inventory_cache(self):
        self.seed_configured_peer()
        action, data = self.form_values(self.peer_id)
        self.assertEqual(302, self.client.post(action, data={**data, "trust_level": "NONE"}).status_code)
        self.assert_saved_settings("NONE", 2)
        self.assertEqual([], self.catalog.remote_inventory_items(self.peer_id))
