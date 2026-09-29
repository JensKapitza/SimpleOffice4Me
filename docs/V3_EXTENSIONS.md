# Extension API 3.0

SimpleOffice extensions are data- and protocol-based. The application does not import modules from extension manifests, execute manifest-provided shell commands or install packages.

## Contract version

Current API version: 1.

An external manifest contains id, version, api_version, extension_points, capabilities and an HTTP transport. Unknown or newer API versions are rejected before registration.

Supported extension points and required capabilities:

- search_provider -> search.read
- command -> command.invoke
- inbox_source -> inbox.write
- export_provider -> export.read
- activity_consumer -> activity.consume
- automation_action -> automation.execute
- health_check -> health.read

Optional UI descriptors require the separate ui.register capability.

## Two-step authorization

Capabilities requested by the manifest are not permissions. After registration an external extension is disabled and has zero locally approved capabilities. An administrator must explicitly approve a subset of the requested capabilities and enable the extension. The manifest cannot self-grant access.

Disabling an extension removes it from invocation immediately. No Flask route, background thread or imported module is owned by the extension, so disabling it cannot leave a dangling code registration.

## HTTP transport

External extensions use POST with a JSON envelope containing api_version, extension_id, extension_version, point, operation and payload.

Production endpoints must use HTTPS. Plain HTTP is accepted only on loopback for development. Non-loopback hosts also require an explicit local hostname allowlist through SIMPLEOFFICE_EXTENSION_HTTP_ALLOWLIST.

Requests are capped at 256 KiB, responses at 1 MiB and timeouts at 10 seconds. Redirects are not followed. Repeated transport failures open an in-process circuit breaker. invoke_all isolates one failing integration from the others.

## Secrets

Secret values are forbidden in manifests. auth_ref is an opaque reference only. The HTTP transport accepts an injected SecretResolver port; no default resolver reads a plaintext token from the manifest or logs it. Deployments may bind that port to an approved server-side credential/vault mechanism.

## Built-ins

Built-in extensions are registered explicitly with ExtensionRegistry.register_builtin and concrete callables. There is no dynamic module name, importlib path or eval/exec mechanism. Built-ins are subject to the same declared/approved capability checks.

## Global search integration

The existing V3 search registry has one extensions provider. When v3.extensions is disabled it returns no results. When enabled it invokes only enabled extensions with approved search.read. The external search payload contains query, limit and an authenticated boolean; the local username is not transmitted.

Returned search results are accepted only when title is present and the navigation URL is either an application-relative path or HTTPS.

## Example

examples/extensions/read_only_search.json is a loopback manifest. examples/extensions/read_only_search_server.py is a separate demo process using only the Python standard library. SimpleOffice never imports or starts it.

Run the example process manually, paste the JSON manifest into Administration -> Extensions 3.0, approve search.read and health.read, then enable it. With v3.extensions and v3.search enabled, its read-only result can appear in the global search.

## Compatibility and deprecation

API v1 is additive. New optional response fields may be introduced without changing api_version. Breaking envelope, authorization or semantic changes require a new API version. Old versions remain explicitly accepted or explicitly rejected; there is no silent coercion.

Extension failures must not become startup dependencies. The registry is only consulted on the related extension point and all current V3 extension capabilities are disabled by default.
