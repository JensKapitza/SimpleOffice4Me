# V2 delegation and trusted relay

This phase defines explicit capability grants for delegated federation work. It
builds on the persistent federation jobs introduced earlier and does not replace
transport authentication.

## Rights

Capabilities use explicit rights:

- `read`: decrypt/read authorization at the application layer
- `store`: retain ciphertext or referenced objects
- `relay`: forward data for an authorized transfer
- `metadata`: access specifically authorized metadata
- `delegate`: create a narrower child capability

Rights are independent. In particular, `relay` or `store` never imply
`read`.

## Scope and expiry

Each grant has an explicit subject, object-reference scope and expiry. A child
grant must be a strict subset or equal subset of the parent's rights and object
scope, and it may not outlive the parent.

Only the current grant subject may delegate from that grant.

## Re-delegation

Delegation is not automatically transitive. A child may receive the
`delegate` right only when re-delegation is explicitly enabled during child
creation.

## Revocation

A grant is effective only while every grant in its parent chain is present,
unexpired and not revoked. Revoking a parent therefore invalidates all
descendants without rewriting them.

## Trusted relay semantics

A relay peer may receive `relay` and/or `store` rights while having no
`read` right. The relay may transport opaque ciphertext and verify transport or
storage integrity metadata, but those rights do not authorize plaintext access.

Future protocol integration must validate:

1. the authenticated peer identity,
2. that the referenced capability is effective,
3. the requested object is within scope,
4. the requested operation has an explicit matching right,
5. transfer/job expiry and capability expiry,
6. no right inferred from another right.

## Security boundaries

Capability records contain grant identifiers, subjects, rights, object
references, expiry and revocation state. They must not contain content-encryption
keys, passwords, recovery keys, bearer tokens or plaintext secrets.

Authorization decisions belong in the authorization layer; storage peers and
relay transports must not infer permissions from physical blob possession,
content hashes, network topology or previous successful transfers.

## Integration status

The capability model is integrated with V2 federation job authorization,
verification and deny-first route policy. Effective grants are re-checked for
scope, expiry/revocation and blocked relay/delegation paths before job progress.
Descriptor-scoped encrypted recovery uses the same explicit authorization
boundary without granting plaintext access.

Legacy federation transport/state remains side-by-side only for compatibility
with older peers. Its removal belongs to the post-V2 compatibility cleanup in
#471 and must not weaken V2 capability or route-policy checks.
