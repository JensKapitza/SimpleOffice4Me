# V3 entity references and relations

The relation layer stores links, not domain objects. Existing Contact, Project, Document, Task, Calendar and Finance stores remain authoritative.

`EntityRef(type, id, instance)` is the only cross-domain identity used by the core relation service. Each type is registered with a resolver and an authorization adapter. Unknown types fail closed.

Relations are additive SQLite records with bounded metadata. Undirected types are canonicalized to prevent duplicates. Directed types can reject cycles. Missing/deleted targets remain readable as orphaned relations instead of producing server errors.

Consumers must never bypass the owning domain service merely because an EntityRef exists. A relation does not grant access: list/get/remove checks visibility on both endpoints.
