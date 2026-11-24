"""Focused administrator UI coverage for preserving peer policies."""

import unittest

from app.federation_store import FederationStore
from tests.federation_admin_ui_fixture import FederationAdminUiFixture


class FederationAdminPolicyUiTests(FederationAdminUiFixture, unittest.TestCase):
    def test_peer_form_maps_document_permissions_without_losing_advanced_policy(self):
        response = self.client.post(
            "/admin/federation/peers",
            data={
                "peer_id": "office-b",
                "label": "Office B",
                "base_url": "http://10.0.0.2:8080",
                "enabled": "1",
                "_documents_policy_form": "1",
                "documents_send": "1",
                "documents_seed": "1",
                "policy_json": '{"chat":{"send":true,"receive":false}}',
            },
            follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        self.assertIn("view=peers", response.headers["Location"])

        peer = FederationStore(self.root).get_peer("office-b")
        self.assertIsNotNone(peer)
        policy = peer["policy"]
        self.assertEqual(
            {"send": True, "receive": False, "seed": True},
            policy["documents"],
        )
        self.assertEqual({"send": True, "receive": False}, policy["chat"])

    def test_peer_form_maps_mail_case_policy_without_losing_advanced_policy(self):
        response = self.client.post(
            "/admin/federation/peers",
            data={
                "peer_id": "mail-peer",
                "label": "Mail Peer",
                "base_url": "https://mail-peer.example.test",
                "enabled": "1",
                "_mail_cases_policy_form": "1",
                "mail_cases_send": "1",
                "mail_cases_receive": "1",
                "mail_cases_auto_accept": "1",
                "policy_json": '{"chat":{"send":true,"receive":false},"data_classes":{"mail_cases":{"future_option":"keep"}}}',
            },
            follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        peer = FederationStore(self.root).get_peer("mail-peer")
        self.assertEqual(
            {
                "future_option": "keep",
                "send": True,
                "receive": True,
                "auto_accept": True,
            },
            peer["policy"]["data_classes"]["mail_cases"],
        )
        self.assertEqual(
            {"send": True, "receive": False},
            peer["policy"]["chat"],
        )

        response = self.client.get(
            "/admin/federation?view=peers&edit_peer=mail-peer"
        )
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("Mail-Vorgang-Federation", body)
        self.assertIn('name="mail_cases_send" value="1" checked', body)
        self.assertIn('name="mail_cases_receive" value="1" checked', body)
        self.assertIn('name="mail_cases_auto_accept" value="1" checked', body)

    def test_rendezvous_relay_can_be_revoked_without_losing_policy(self):
        response = self.client.post(
            "/admin/federation/peers",
            data={
                "peer_id": "relay-peer",
                "label": "Relay Peer",
                "base_url": "https://relay.example.test",
                "enabled": "1",
                "_rendezvous_relay_policy_form": "1",
                "policy_json": '{"chat":{"send":true},"rendezvous_relay":{"enabled":true,"future_option":"keep"}}',
            },
            follow_redirects=False,
        )

        self.assertEqual(302, response.status_code)
        peer = FederationStore(self.root).get_peer("relay-peer")
        self.assertEqual({"send": True}, peer["policy"]["chat"])
        self.assertEqual(
            {"enabled": False, "future_option": "keep"},
            peer["policy"]["rendezvous_relay"],
        )

        body = self.client.get("/admin/federation?view=peers").get_data(as_text=True)
        self.assertIn("Relay aus", body)
        self.assertNotIn('name="rendezvous_relay_enabled" value="1" checked', body)
