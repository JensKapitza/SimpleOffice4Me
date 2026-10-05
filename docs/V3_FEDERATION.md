# Federation 3.0 transfer contract

The 3.0 federation contract is additive. The existing `/federation/v1` endpoints and legacy transfer jobs remain available during the compatibility window.

## Capability flag

The new HTTP facade is disabled by default:

```text
SIMPLEOFFICE_V3_FEDERATION_ENABLED=true
SIMPLEOFFICE_FEDERATION_PEER_ID=<stable local peer id>
```

Authentication continues to use the configured federation bearer token (`SIMPLEOFFICE_FEDERATION_TOKEN`). Enabling the 3.0 capability does not grant trust and does not enable any data class automatically.

## Contract

`GET /federation/v3/capabilities` advertises envelope versions and supported object schema versions. The current envelope version is 1.

An envelope contains `message_id`, sender/recipient instance, object type/schema version, envelope version, timestamp, optional EntityRef, optional object/content references, optional SHA-256 integrity metadata, and a bounded JSON payload.

The receiver persists the message id and canonical envelope digest before any later domain-specific mutation. Repeating the identical message is idempotent. Reusing the same message id with different content is rejected.

## Trust and policy

Receiving requires: an enabled configured peer; a direct local trust edge other than `NONE`; explicit `receive: true` for the data class; and negotiated envelope/object versions.

A relation, recommendation or transitive claim does not grant transfer permission. The existing directed trust model remains authoritative.

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