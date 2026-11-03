"""Seed one disposable Compose peer with its testnet identities.

Besides the remote test peers this also writes the container's own local
identity (peer id, public URL, shared token) into the disposable disk store.
The application deliberately reads no ``SIMPLEOFFICE_FEDERATION_*`` identity
environment variables at runtime (No-Env rule); the isolated Compose network
in ``deploy/docker/compose.federation-test.yaml`` is the only place whose
test-only values this tool accepts, and it writes them to the container-local
disk store on the disposable test volume. Production master identity stays in
``app/build_master.py`` (build time) and is never touched here.
"""
from __future__ import annotations

import argparse
import os


# The only local identities this tool may write. Keys are the exact
# SIMPLEOFFICE_FEDERATION_PEER_ID values from the isolated testnet Compose
# file, values the matching SIMPLEOFFICE_FEDERATION_PUBLIC_URL.
TESTNET_LOCAL_IDENTITIES = {
    "federation-test-peer-a": "http://peer-a:8080",
    "federation-test-peer-b": "http://peer-b:8080",
    "federation-test-relay": "http://relay:8080",
}


def _seed_local_testnet_identity(root) -> None:
    """Persist this container's disposable local identity (testnet only).

    Reads the isolated testnet values from the environment and writes them
    to the local disk store (app config store plus SettingsStore). Refuses
    any non-testnet identity fail-closed so this tool can never provision a
    production identity. Existing profile fields and the directory token are
    preserved.
    """
    from app import federation_local_identity as local_identity
    from app.settings_store import SettingsStore

    peer_id = os.environ.get("SIMPLEOFFICE_FEDERATION_PEER_ID", "").strip()
    base_url = os.environ.get("SIMPLEOFFICE_FEDERATION_PUBLIC_URL", "").strip()
    token = os.environ.get("SIMPLEOFFICE_FEDERATION_TOKEN", "")
    expected_url = TESTNET_LOCAL_IDENTITIES.get(peer_id)
    if not peer_id or not base_url or not token:
        raise SystemExit("local testnet identity is incomplete")
    if expected_url is None or base_url.rstrip("/") != expected_url:
        raise SystemExit("local testnet identity is not an isolated testnet identity")
    current = local_identity.status()
    local_identity.save({
        "peer_id": peer_id,
        "label": current.get("label", ""),
        "country": current.get("country", ""),
        "public_url": base_url,
        "public_key": current.get("public_key", ""),
        "fingerprint": current.get("fingerprint", ""),
        "token": token,
        "directory_token": local_identity.directory_token(),
    })
    SettingsStore(root).save(
        {"integrations": {"federation_public_url": base_url}},
        actor="federation-testnet-seed",
    )
    # The isolated Compose network documents the V3 intent via
    # SIMPLEOFFICE_V3_FEDERATION_ENABLED, but the runtime reads only the disk
    # store (No-Env rule), so the seed translates it into the persisted flag.
    SettingsStore(root).save(
        {"v3": {"v3.federation": True}},
        actor="federation-testnet-seed",
    )
    print("PASS: disposable local identity configured")


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

    with app.app_context():
        _seed_local_testnet_identity(app.config["DOCUMENT_ROOT"])

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
