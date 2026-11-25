"""Test the authenticated identity comparison at the existing pairing form."""

import unittest

from bs4 import BeautifulSoup

from app.federation_pairing_comparison import pairing_comparison
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore
from tests.federation_admin_ui_fixture import FederationAdminUiFixture


class FederationPairingComparisonTests(FederationAdminUiFixture, unittest.TestCase):
    def test_claim_form_shows_its_stored_public_fingerprint_and_saved_target(self):
        self._remember_pairing_peer()
        store = FederationStore(self.root)
        identity = FederationTrustStore(self.root).identity("office-b")
        response = self.client.get("/admin/federation/peer-discovery")
        self.assertEqual(200, response.status_code)
        page = BeautifulSoup(response.data, "html.parser")
        claim = page.select_one('form[action$="/pairing/office-b/claim"]')
        self.assertIsNotNone(claim)
        self.assertEqual(identity["fingerprint"], claim.select_one("[data-pairing-fingerprint]").get_text(strip=True))
        self.assertEqual(store.get_peer("office-b")["base_url"],
                         claim.select_one("[data-pairing-target]").get_text(strip=True))
        confirm = claim.select_one('[name="confirm_identity"]')
        self.assertIn("required", confirm.attrs)
        self.assertNotIn("checked", confirm.attrs)
        self.assertIsNotNone(claim.select_one(f'#{confirm["aria-describedby"]}'))
        self.assertNotIn(identity["public_key"], claim.get_text())
        self.assertFalse(store.get_peer("office-b")["enabled"])
        self.assertIsNone(FederationTrustStore(self.root).get_trust("office-b"))

    def test_comparison_does_not_echo_non_fingerprints_or_unsafe_target_fields(self):
        fingerprint = "a" * 64
        self.assertEqual(
            {"fingerprint": fingerprint, "target": "https://office-b.example.test:9443/office"},
            pairing_comparison(fingerprint, "https://office-b.example.test:9443/office/"),
        )
        self.assertEqual({}, pairing_comparison("not-a-public-fingerprint", "https://office-b.example.test"))
        self.assertEqual({}, pairing_comparison(fingerprint, "https://office-b.example.test?mode=mobile"))
