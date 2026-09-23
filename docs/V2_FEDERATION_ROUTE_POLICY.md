# V2 federation route policy

V2 federation routing is deny-first. A positive capability or trust relation
does not override a local block or a stricter local route constraint.

## Persistent local constraints

A target peer can have separate route constraints per federation scope:

- `direct_only`: the known route must contain exactly source and target;
- `max_hops`: limits the number of route edges;
- `allowed_relays`: every known intermediary/downstream peer other than the
  intended target must be in the local allowlist.

A global (`all`) target constraint and a scope-specific constraint are both
evaluated. The stricter result therefore wins automatically.

Explicit peer blocks are evaluated before route constraints. This preserves the
`explicit deny wins` rule even when a route would otherwise satisfy an
allowlist or hop limit.

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

This is only the locally enforceable route layer. Signed peer relationship
claims, confirmation/quorum rules and policy-builder UI remain separate follow-up
work. They must not weaken local explicit blocks or these route constraints.
