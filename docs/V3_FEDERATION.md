# Federation 3.0 transfer contract

The 3.0 federation contract is additive. The existing `/federation/v1` endpoints and legacy transfer jobs remain available during the compatibility window.

## Capability flag

The new HTTP facade is disabled by default:

```text
SIMPLEOFFICE_V3_FEDERATION_ENABLED=true
SIMPLEOFFICE_FEDERATION_PEER_ID=<stable local peer id>
```

Peer identity is authenticated with the existing SOFP peer-bound HMAC contract. The proof binds the configured peer ID, HTTP method and path, timestamp, nonce and request-body hash to that peer's unique credential. Identity-bearing operations such as `POST /receive` and `POST /peers/<peer_id>/capabilities` require this proof; the authenticated peer must match the envelope sender or path peer.

The global `SIMPLEOFFICE_FEDERATION_TOKEN` remains temporarily accepted only for identity-neutral compatibility calls such as `GET /capabilities`. It authenticates access to the server but **does not establish peer identity** and cannot authorize an envelope sender. Existing legacy catalog reads likewise keep the bearer compatibility path while preferring peer HMAC. Operators should configure a unique token per peer and migrate callers to HMAC before the legacy compatibility path is removed. Enabling the 3.0 capability does not grant trust and does not enable any data class automatically.

## Contract

`GET /federation/v3/capabilities` advertises envelope versions and supported object schema versions. The current envelope version is 1.

An envelope contains `message_id`, sender/recipient instance, object type/schema version, envelope version, timestamp, optional EntityRef, optional object/content references, optional SHA-256 integrity metadata, and a bounded JSON payload.

The receiver persists the message id and canonical envelope digest before any later domain-specific mutation. Repeating the identical message is idempotent. Reusing the same message id with different content is rejected.

## Trust and policy

Receiving requires: an enabled configured peer; a direct local trust edge other than `NONE`; explicit `receive: true` for the data class; and negotiated envelope/object versions.

A relation, recommendation or transitive claim does not grant transfer permission. The existing directed trust model remains authoritative.

## Discover and configure a peer

LAN/direct, country and email discovery and QR import identify a peer; they do not pair, enable it, or grant trust. New peers remain disabled. For an already configured peer, discovery updates identity and compatibility metadata only; it does not replace the configured endpoint, credentials, activation state or data policy. An address advertised by discovery is shown as a suggestion; inspect it and enter it in the existing peer editor if you intend to change the connection target. A QR import opens that editor with the identity prefilled. Configure the same peer-specific token on both instances using a trusted out-of-band channel. Do not reuse the global `SIMPLEOFFICE_FEDERATION_TOKEN`: it cannot establish a V3 peer identity. The saved peer token is encrypted at rest.

After checking the peer identity on both instances, each administrator must set the direct trust edge locally. Configure only the required data-class permissions separately; trust alone does not grant data access, and automatic acceptance remains off unless explicitly enabled. The UI does not yet provide an expiring one-time code or a cryptographic bilateral pairing handshake, so operators must perform these steps independently.

### Pairing code lifecycle foundation

The repository contains a bounded one-time-code store for the upcoming bilateral pairing flow. A code is generated with 60 bits of randomness, bound to exactly one expected peer, valid for 1 to 15 minutes (10 minutes by default), and persisted only as a keyed hash. Issuing a replacement revokes the earlier code for that peer; claiming is atomic and succeeds once even under concurrent requests. Status responses never expose the code.

The HTTP bootstrap exposes signed `POST /federation/v1/pairing/claim` and `POST /federation/v1/pairing/status` requests. The claimant signs the operation, persistent peer ID, public key and fingerprint, timestamp, replay nonce and (for a claim) the code with its persistent Ed25519 identity. The receiving instance accepts only an already discovered key for the peer to which the code was issued. Requests outside the five-minute clock window, modified requests and repeated nonces fail closed. Neither response contains the code or its stored digest.

Administrators can issue and revoke a code for an already discovered identity from the peer-discovery dashboard. Issuance requires the peer's stored public key and fingerprint. The plaintext code appears only in the immediate `Cache-Control: no-store` response; dashboard status exposes only `pending`, `claimed`, `expired`, or `missing`. Audit events contain the actor and expiry, never the code or digest.

On the claimant instance, an administrator selects the same known peer, confirms its fingerprint and target address, and submits the code. SimpleOffice re-fetches the public discovery profile over an IP-pinned connection and requires the peer ID, Ed25519 key, and fingerprint to match before it sends the signed claim. A read-only remote status check supports recovery after a process restart. The code is not persisted on the claimant and is never written to flash messages or audit details.

Issuing, claiming, checking, or revoking a code does not enable a connection, set a shared peer token, grant trust, or grant any data capability. Administrators must still configure connectivity, direct trust, and the minimum data permissions independently on both peers.

Peer policies may use:

```json
{
  "data_classes": {
    "documents": {"receive": true, "auto_accept": false}
  }
}
```

Without `auto_accept: true`, a supported transfer is stored as `pending` for later user/admin acceptance. Unsupported versions or non-negotiated object classes are quarantined. Disabled data classes are rejected.

## Object types

Documents, contacts, calendar, tasks and collaborative mail cases (`mail_cases`) advertise schema version 1. Relations and activity are advertised only when their corresponding 3.0 capabilities are enabled. Mail-case transfer additionally requires the peer-scoped mail-case policy and explicit remote-to-local user mapping described in `MAIL_VORGAENGE.md`.

## Compatibility

Legacy peers can continue using the previous federation protocol. No existing SOFP/V1 route, trust record or transfer table is removed or rewritten. The new persistence tables live alongside the existing federation state.

## Public HTTP contract checks

Run the V3 route contract tests with:

```text
.venv/bin/python -m unittest tests.test_v3_federation_two_peer_http tests.test_v3_federation_peer_auth_http tests.test_v3_federation -v
```

`test_v3_federation_two_peer_http` sends signed requests through two independent
Flask test clients with separate data roots. It verifies that an untrusted peer
is rejected, a directly trusted peer without negotiated document capability is
quarantined, and a trusted, compatible transfer remains pending when automatic
acceptance is not enabled. This route-level fixture does not establish discovery,
QR/manual pairing, real network reachability, process restart recovery, or the
separate Compose test-network acceptance tracked in #679.

The isolated two-container smoke sequence exercises public discovery, V3
capability exchange, peer-bound HMAC in both directions, default rejection
before trust, pending receipt after independent local approval at each test
peer, and idempotency after restarting a peer. Run it using the commands in
[`deploy/docker/README.md`](../deploy/docker/README.md). Test-only seeding is
limited to disposable volumes; it does not exercise the browser QR/manual
pairing workflow or real LAN discovery. The Ubuntu Docker-host CI job executes
this sequence with fresh volumes, restarts one peer, waits for it to become
healthy again, and verifies persisted duplicate detection before cleaning up.
