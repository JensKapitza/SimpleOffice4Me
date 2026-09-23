# V2 federation route policy

V2 federation routing is deny-first. A positive capability or trust relation
does not override a local block or a stricter local route constraint.

## Persistent local constraints

A target peer can have separate route constraints per federation scope:

- `direct_only`: the known route must contain exactly source and target;
- `max_hops`: limits the number of route edges;
- `allowed_relays`: every known intermediary/downstream peer other than the
  intended target must be in the local allowlist;
- `denied_relays`: named intermediary/downstream peers are forbidden for this
  target and scope even when other relays remain permitted.

A global (`all`) target constraint and a scope-specific constraint are both
evaluated. The stricter result therefore wins automatically.

Explicit peer blocks are evaluated before route constraints. This preserves the
`explicit deny wins` rule even when a route would otherwise satisfy an
allowlist or hop limit.

Peer blocks can additionally be bound to one or more logical object references.
An object-specific block applies only when the transfer contains one of those
objects and still obeys global/scope expiry rules. Evaluation order is:
global peer block, matching object block, scope peer block, then positive route
and trust rules. Object-specific blocks are persisted separately so a narrow
deny does not replace an existing whole-peer block.

When an object-specific block is created through the admin UI, effective local
capability grants involving that peer are revoked only when their object scope
intersects the blocked set. A mixed grant containing blocked and unblocked
objects is revoked as one grant; issuing a narrower replacement grant is a
separate authorization action.

## Signed confirmation requirements

A target can additionally require signed peer confirmations per scope. A local
rule stores a finite verifier set plus a quorum, for example:

- verifier set `{X}`, quorum `1`: send only when X currently confirms the target;
- verifier set `{X,Y,Z}`, quorum `2`: require at least two distinct valid confirmations;
- quorum equal to verifier count: require every configured verifier.

Confirmation evidence is read from the existing signed federation attestation
store. Before it satisfies policy, the stored signature is re-verified against
the verifier's currently known public key and expired or unverified attestations
are ignored. Missing keys, invalid signatures and insufficient evidence fail
closed.

Global and scope-specific confirmation requirements are both evaluated. A peer
that is locally blocked for the evaluated scope cannot contribute positive
confirmation evidence, so a positive attestation never overrides an explicit
deny.

## Blocked downstream capability paths

For an active local block, the evaluator also checks the local V2 capability
graph when that state is available. A transfer to target C is denied when C has
an effective capability grant to blocked peer B containing `relay` or
`delegate` rights for one of the objects being transferred.

The check follows capability revocation, expiry and parent-grant effectiveness.
An unrelated grant for another object does not block the transfer. A denial
identifies B as the blocked peer so the existing revocation path can invalidate
remaining local capabilities involving B.

This covers relationships represented in the local authoritative capability
store. It does not claim knowledge of undisclosed grants on an uncooperative
remote peer; stronger remote relationship assertions require separately signed
and current protocol evidence.

## Complete-route binding

The policy evaluator receives the intended target separately from the route.
This matters when the locally known path contains additional relay, storage,
delegation or downstream actors after the intended target.

A route is denied when:

- the intended target is missing from the known route,
- the route contains a repeated peer/cycle,
- a global or scope-specific peer block matches any participant,
- `direct_only` is violated,
- the maximum hop count is exceeded,
- a relay/downstream participant is explicitly present in the target denylist,
- a relay/downstream participant is not in the configured allowlist,
- stored policy state cannot be parsed safely.

Unknown/malformed policy state fails closed for transfer-job creation and for
the policy re-check immediately before job progress.

## Job lifecycle

`FederationJobService.create_transfer(...)` evaluates the route before
persisting the transfer.

Existing non-terminal jobs retain the complete known route, intended target and
policy scope. `enforce_policy(...)` re-evaluates that state before progress;
a newly forbidden job is failed. `stop_blocked_jobs(...)` can re-evaluate the
whole active queue after a policy change.

## Decision audit

Every policy evaluation performed for transfer creation and immediately before
job progress writes a tamper-evident V2 audit event. The event records the
target, evaluated scope, bounded route/object references, allow/deny result,
decision reason and blocked peer when present. Raw idempotency keys are not
stored; transfer-creation correlation uses a bounded SHA-256-derived identifier.

An allowed transfer is not created when its policy decision cannot be audited.
For an existing job, audit loss fails the job before progress with
`policy_denied_reason=audit_unavailable`. Invalid/corrupt policy state is
still denied and an audit attempt records `policy_invalid`.

This is only the locally enforceable route layer. Signed confirmation/quorum
rules, whole-peer blocks, object-specific blocks and local policy administration
are enforced here. Remote relationship assertions that are not represented in
the local capability graph remain separate follow-up work. They must not weaken
local explicit blocks, confirmation requirements or route constraints.
