"""Safety checks before the browser P2P test changes local peer identity."""

import unittest

from tests.browser.peer_to_peer_safety import (
    validate_identity_form_destination,
    validate_identity_mutation_targets,
    validate_local_browser_target,
    validate_same_origin_target,
    validate_testnet_peer_targets,
)


class PeerIdentityMutationSafetyTest(unittest.TestCase):
    def test_allows_browser_smoke_only_on_numeric_loopback(self):
        validate_local_browser_target(base_url="http://127.0.0.1:8080")

    def test_rejects_non_loopback_browser_smoke_target(self):
        for value in ("https://demo.example", "http://localhost:8080", "http://peer-a:8080"):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "numeric loopback"):
                validate_local_browser_target(base_url=value)

    def test_allows_explicit_test_configuration_on_loopback(self):
        validate_identity_mutation_targets(
            base_url="http://127.0.0.1:8080",
            peer_base_url="http://[::1]:8081",
            shared_token="test-only-shared-token",
            explicitly_allowed=True,
        )

    def test_requires_explicit_mutation_opt_in(self):
        with self.assertRaisesRegex(RuntimeError, "ALLOW_IDENTITY_MUTATION=1"):
            validate_identity_mutation_targets(
                base_url="http://127.0.0.1:8080",
                peer_base_url="http://127.0.0.1:8081",
                shared_token="test-only-shared-token",
                explicitly_allowed=False,
            )

    def test_requires_explicit_shared_token(self):
        with self.assertRaisesRegex(RuntimeError, "BROWSER_PEER_SHARED_TOKEN"):
            validate_identity_mutation_targets(
                base_url="http://127.0.0.1:8080",
                peer_base_url="http://127.0.0.1:8081",
                shared_token="",
                explicitly_allowed=True,
            )

    def test_rejects_external_and_dns_targets(self):
        for value in ("https://office.example", "http://localhost:8080", "http://peer-b:8080"):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "numeric loopback"):
                validate_identity_mutation_targets(
                    base_url=value,
                    peer_base_url="http://127.0.0.1:8081",
                    shared_token="test-only-shared-token",
                    explicitly_allowed=True,
                )

    def test_rejects_urls_with_credentials_or_non_origin_parts(self):
        for value in (
            "http://user:password@127.0.0.1:8080",
            "http://127.0.0.1:8080/path",
            "http://127.0.0.1:8080/?next=remote",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "numeric loopback"):
                validate_identity_mutation_targets(
                    base_url="http://127.0.0.1:8080",
                    peer_base_url=value,
                    shared_token="test-only-shared-token",
                    explicitly_allowed=True,
                )

    def test_rejects_redirect_before_identity_form_submission(self):
        with self.assertRaisesRegex(RuntimeError, "after a redirect"):
            validate_identity_form_destination(
                base_url="http://127.0.0.1:8080",
                final_url="http://127.0.0.1:8080/admin/federation/peer-discovery",
                was_redirected=True,
            )

    def test_rejects_external_final_form_destination(self):
        with self.assertRaises(RuntimeError):
            validate_identity_form_destination(
                base_url="http://127.0.0.1:8080",
                final_url="https://office.example/admin/federation/peer-discovery",
                was_redirected=False,
            )

    def test_allows_same_origin_identity_form_without_redirect(self):
        validate_identity_form_destination(
            base_url="http://127.0.0.1:8080",
            final_url="http://127.0.0.1:8080/admin/federation/peer-discovery",
            was_redirected=False,
        )

    def test_rejects_external_login_form_action(self):
        with self.assertRaises(RuntimeError):
            validate_same_origin_target(
                base_url="https://demo.example",
                target_url="https://collector.example/register",
            )

    def test_allows_configured_login_origin(self):
        validate_same_origin_target(
            base_url="https://demo.example:8443",
            target_url="https://demo.example:8443/auth/login",
        )

    def test_rejects_external_form_action(self):
        with self.assertRaises(RuntimeError):
            validate_identity_form_destination(
                base_url="http://127.0.0.1:8080",
                final_url="https://office.example/federation/identity",
                was_redirected=False,
            )

    def test_allows_only_isolated_docker_peer_targets(self):
        validate_testnet_peer_targets(
            peer_a_url="http://peer-a:8080",
            peer_b_url="http://peer-b:8080",
            peer_a_ip="172.18.0.2",
            peer_b_ip="172.18.0.3",
            link_token="explicit-test-link-token",
        )

    def test_rejects_external_peer_target(self):
        with self.assertRaisesRegex(RuntimeError, "isolated peer-b:8080"):
            validate_testnet_peer_targets(
                peer_a_url="http://peer-a:8080",
                peer_b_url="https://office.example",
                peer_a_ip="",
                peer_b_ip="",
                link_token="explicit-test-link-token",
            )

    def test_rejects_non_docker_peer_ip(self):
        with self.assertRaisesRegex(RuntimeError, "Docker bridge range"):
            validate_testnet_peer_targets(
                peer_a_url="http://peer-a:8080",
                peer_b_url="http://peer-b:8080",
                peer_a_ip="198.51.100.2",
                peer_b_ip="",
                link_token="explicit-test-link-token",
            )

    def test_requires_explicit_peer_link_token(self):
        with self.assertRaisesRegex(RuntimeError, "BROWSER_PEER_LINK_TOKEN"):
            validate_testnet_peer_targets(
                peer_a_url="http://peer-a:8080",
                peer_b_url="http://peer-b:8080",
                peer_a_ip="",
                peer_b_ip="",
                link_token="",
            )


if __name__ == "__main__":
    unittest.main()
