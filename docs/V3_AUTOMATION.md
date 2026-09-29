# V3 Automation

The automation layer is declarative and optional. Rules contain an owner, scope, a typed trigger, bounded typed conditions and registered actions. Free Python, JavaScript and shell execution are deliberately unsupported.

Supported trigger contracts are \`event\`, \`schedule\` and \`check\`. The engine can be called synchronously from existing hooks; a later worker integration may call the same API without changing rule storage.

Conditions use a fixed operator set (\`eq\`, \`ne\`, \`in\`, \`contains\`, \`gt\`, \`gte\`, \`lt\`, \`lte\`, \`exists\`). Actions are resolved through an allow-listed registry and are authorized for the rule owner before execution. The first production action is \`task.create\`, backed by the existing TodoStore and its permission model.

Execution records contain no action result payloads or secrets. Correlation/idempotency keys prevent repeated mutation of the same rule version, recursion is bounded, and actions stop after the first failure. Dry-run validates matching, permissions and the planned action list without mutation.

The feature is disabled by default through \`v3.automation\`. Disabling it leaves rules and domain data intact.
