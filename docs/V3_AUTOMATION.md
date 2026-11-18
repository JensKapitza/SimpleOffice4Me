# V3 Automation

The automation layer is declarative and optional. Rules contain an owner, scope, a typed trigger, bounded typed conditions and registered actions. Free Python, JavaScript and shell execution are deliberately unsupported.

Supported trigger contracts are \`event\`, \`schedule\` and \`check\`. The engine can be called synchronously from existing hooks; a later worker integration may call the same API without changing rule storage.

Conditions use a fixed operator set (\`eq\`, \`ne\`, \`in\`, \`contains\`, \`gt\`, \`gte\`, \`lt\`, \`lte\`, \`exists\`). Actions are resolved through an allow-listed registry and are authorized for the rule owner before execution. The first production action is \`task.create\`, backed by the existing TodoStore and its permission model.

Execution records contain no action result payloads or secrets. Correlation/idempotency keys prevent repeated mutation of the same rule version, recursion is bounded, and actions stop after the first failure. Dry-run validates matching, permissions and the planned action list without mutation.

The feature is disabled by default through \`v3.automation\`. Disabling it leaves rules and domain data intact.

## Confirmed manual check runs

An owner can preview and explicitly confirm a live run for an active \`check\` rule with exactly one \`task.create\` action. The submitted JSON is the check context: this manual flow does not query current invoice or other domain data. The preview evaluates the rule and task-list create permission without creating a task. A successful preview is stored server-side for 15 minutes, bound to its owner, rule version, and exact event JSON; the JSON is lazily purged after expiration. The confirmation form contains only an opaque token and must be checked explicitly. The preview page is private and not cacheable. Editing or deactivating the rule invalidates its preview. Login, browser CSRF protection, the \`v3.automation\` capability, the existing \`projects\` feature gate, owner scoping, and TodoStore list permissions remain enforced.

The manual UI accepts only task title, description, due date, priority and an optional task-list ID. It does not accept hidden task links, ownership, assignment, completion, source-document or project metadata.

Confirmation retries reuse the same server-bound run token. \`task.create\` derives an internal operation ID from that run and action position; TodoStore persists its receipt atomically with the task under its write lock. A retry therefore returns the same stored task after a concurrent request or after a task write whose automation log write failed. The receipt is retained with task data and list create permission is checked again on every retry. A new preview creates a new intentional run. The existing general dry-run endpoint remains mutation-free and does not prepare a live run.

The rule's active/version check and execution are serialized with rule updates under a per-rule file lock, so a concurrent disable waits until the confirmed action finishes. Event data is temporarily retained only in the 15-minute preview confirmation; execution logs store a digest rather than submitted event identifiers or fields. This is a synchronous manual action; no scheduler or trusted check-data provider is introduced.
