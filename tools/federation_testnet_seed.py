"""Seed one disposable Compose peer with its remote test identity and optional local approval."""
from __future__ import annotations

import argparse
import os


def main() -> None:
    os.environ.setdefault("SIMPLEOFFICE_LOG_STDERR_ONLY", "1")
    from app import app
    from app.federation_store import FederationStore
    from app.federation_trust_store import FederationTrustStore

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm-local-peer",
        action="store_true",
        help="record this test operator's explicit local approval of the remote peer",
    )
    parser.add_argument(
        "--enable-test-relay",
        action="store_true",
        help="opt in to the dedicated relay in this disposable test instance",
    )
    parser.add_argument(
        "--configure-relay-node",
        action="store_true",
        help="configure both disposable peers on the dedicated relay node",
    )
    args = parser.parse_args()

    if args.configure_relay_node:
        clients = (
            (
                os.environ.get("SIMPLEOFFICE_FEDERATION_TEST_PEER_A_ID", "").strip(),
                "http://peer-a:8080",
                os.environ.get("SIMPLEOFFICE_FEDERATION_TEST_PEER_A_RELAY_TOKEN", ""),
            ),
            (
                os.environ.get("SIMPLEOFFICE_FEDERATION_TEST_PEER_B_ID", "").strip(),
                "http://peer-b:8080",
                os.environ.get("SIMPLEOFFICE_FEDERATION_TEST_PEER_B_RELAY_TOKEN", ""),
            ),
        )
        if not all(peer_id and token for peer_id, _base_url, token in clients):
            raise SystemExit("relay test client configuration is incomplete")
        with app.app_context():
            store = FederationStore(app.config["DOCUMENT_ROOT"])
            for peer_id, base_url, token in clients:
                store.save_peer(peer_id, peer_id, base_url, token, policy={})
        print("PASS: disposable relay clients configured")
        return

    peer_id = os.environ.get("SIMPLEOFFICE_FEDERATION_TEST_REMOTE_PEER_ID", "").strip()
    base_url = os.environ.get("SIMPLEOFFICE_FEDERATION_TEST_REMOTE_PEER_URL", "").strip()
    token = os.environ.get("SIMPLEOFFICE_FEDERATION_TEST_REMOTE_PEER_TOKEN", "")
    relay_id = os.environ.get("SIMPLEOFFICE_FEDERATION_TEST_RELAY_PEER_ID", "").strip()
    relay_url = os.environ.get("SIMPLEOFFICE_FEDERATION_TEST_RELAY_URL", "").strip()
    relay_token = os.environ.get("SIMPLEOFFICE_FEDERATION_TEST_RELAY_TOKEN", "")
    if not all((peer_id, base_url, token, relay_id, relay_url, relay_token)):
        raise SystemExit("remote test peer configuration is incomplete")

    with app.app_context():
        root = app.config["DOCUMENT_ROOT"]
        store = FederationStore(root)
        store.save_peer(
            peer_id,
            peer_id,
            base_url,
            token,
            policy={"data_classes": {"documents": {"receive": True}}},
        )
        store.save_peer(
            relay_id,
            relay_id,
            relay_url,
            relay_token,
            policy={"rendezvous_relay": {"enabled": True}}
            if args.enable_test_relay
            else {},
        )
        if args.confirm_local_peer:
            FederationTrustStore(root).set_trust(
                peer_id,
                trust_level="NORMAL",
                verification="VERIFIED_ADMIN",
                propagation="DIRECT_ONLY",
                metadata={"actor": "federation-testnet operator"},
            )

    print("PASS: disposable peer configuration saved")
    if args.confirm_local_peer:
        print("PASS: this instance recorded its independent approval")
    if args.enable_test_relay:
        print("PASS: this instance explicitly enabled the dedicated relay")


if __name__ == "__main__":
    main()
