# V3 Policy Facade

The policy facade does not introduce a new role model. It provides one fail-closed call shape, `can(principal, action, resource)`, while adapters continue to ask the existing domain stores and feature gates.

Unknown actions and resource types are denied. Adapter errors are denied without exposing internal details. Bulk visibility is bounded and checks each returned resource before it can become a search/context result.

The first concrete adapter mirrors ContactStore read/manage rules plus the existing `contacts` feature gate. Other domains register their own adapters as they migrate.

During rollout, `can_with_legacy` keeps the existing check authoritative while `v3.policy` is disabled. This makes a migrated caller reversible without changing user rights.
