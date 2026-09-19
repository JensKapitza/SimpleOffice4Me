# Federation V2 persistent jobs

Federation V2 models transfer work as a persistent desired-state job.

A controller may create a job saying that selected authorized object references
must become `verified_present` on a target peer. Once accepted, the job is
stored locally and no longer depends on the controller's live HTTP session.

## Properties

- persistent SQLite state below `.simpleoffice-v2`
- unique idempotency key
- restart-safe worker leases
- explicit attempts and job states
- source and target peer remain distinct
- authorization is a reference/scope, not an embedded long-lived secret
- object scope cannot expand during progress updates
- completion depends on verified target state
- arrival through another authorized route may satisfy the same job

This means A may authorize B -> C and then go offline; the persisted job remains
processable. If C already receives an expected object through an allowed local
import or another authorized path, that verified fact can satisfy the same
desired state without requiring duplicate transport.

## Legacy federation

The existing `federation_transfer` table remains untouched in this PR. It
contains transport-oriented legacy fields including blob hashes. New V2 code is
introduced side-by-side so protocol migration can happen after job semantics are
stable and tested.

## Secrets

V2 job payloads contain references and authorization identifiers only. Transfer
capabilities, keys and tokens are not embedded in normal job JSON or logs.
