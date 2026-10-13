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
    args = parser.parse_args()

    peer_id = os.environ.get("SIMPLEOFFICE_FEDERATION_TEST_REMOTE_PEER_ID", "").strip()
    base_url = os.environ.get("SIMPLEOFFICE_FEDERATION_TEST_REMOTE_PEER_URL", "").strip()
    token = os.environ.get("SIMPLEOFFICE_FEDERATION_TEST_REMOTE_PEER_TOKEN", "")
    if not peer_id or not base_url or not token:
        raise SystemExit("remote test peer configuration is incomplete")

    with app.app_context():
        root = app.config["DOCUMENT_ROOT"]
        FederationStore(root).save_peer(
            peer_id,
            peer_id,
            base_url,
            token,
            policy={"data_classes": {"documents": {"receive": True}}},
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


if __name__ == "__main__":
    main()
