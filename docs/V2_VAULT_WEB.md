# Password Vault Web/UI boundary

## Purpose

The password vault web layer exposes the existing encrypted `PasswordVault` and
V2 `VaultService` to authenticated SimpleOffice users without placing the master
password or decrypted vault key in the browser session.

## Unlock model

- The master password is submitted only to the unlock/setup operation.
- The decrypted 256-bit vault key is cached only in process memory.
- The signed browser session contains only an opaque random unlock token.
- Unlock lifetime defaults to 300 seconds and is bounded to 60-1800 seconds.
- Each successful vault operation refreshes the short timeout.
- Explicit lock removes the token and the corresponding in-memory key.
- Process restart or routing to another worker naturally locks the vault again.
- No unlock key is written to the document tree, database, log or cookie.

This process-local model is deliberately conservative. A future multi-worker shared
unlock service must preserve the same rule: no plaintext vault key in browser cookies
or normal persistent storage.

## Search and detail separation

The normal list/search API uses `VaultService.search()`. Its result contains only
non-secret projections such as name, username, e-mail address, service domain, tags,
folder and favorite state.

Password, TOTP secret and notes are not part of the search response. Full decrypted
credential data is available only after an explicit credential detail request while
the vault is unlocked. Secret-bearing responses use `Cache-Control: private, no-store`.

## Browser API

The first browser API is intentionally same-origin/session based:

- `GET /vault/api/v1/search` - non-secret search projection
- `POST /vault/api/v1/credentials/<id>/reveal` - explicit full credential read
- `GET /vault/api/v1/credentials/<id>/mail` - whitelisted mail references
- `GET /vault/api/v1/generate-password` - generated password

State-changing/reveal POST requests remain under the application's existing CSRF
protection. This is not yet an extension token protocol and does not claim third-party
site autofill.

## Import and export

Browser CSV import is preview-first. Existing entries with the same normalized URL
and username are counted as conflicts and never silently overwritten. Confirmation
requires re-uploading the CSV, so plaintext candidate passwords are not stored in the
signed session or a server-side preview cache.

Supported export paths:

- encrypted SimpleOffice vault backup
- explicit browser-compatible plaintext CSV

Plaintext CSV requires both re-entering the current master password and typing
`EXPORT` in the UI. It is returned as a transient no-store download and is not
written into the managed document tree.

Encrypted backup replacement requires re-entering the current master password and
typing `REPLACE`. After replacement, any existing in-memory unlock is discarded and
the imported vault must be unlocked again.

## Mail references

The credential detail view reuses the existing `MailStore` and `MailSearchIndex`
through the V2 `VaultService` ports. Only whitelisted locator/header metadata is
returned. Mail bodies, indexed search text and content hashes are not copied into the
vault response.

## Remaining #310 boundary

This web/UI package does not by itself finish every item in #310. In particular:

- encrypted credential payloads still use the dedicated local vault database rather
  than the final V2 object-store payload boundary;
- no external browser-extension token/scoping protocol is introduced;
- automatic management/deletion of one-time-code mails remains opt-in future work;
- credential sharing remains disabled by default and is not added here.

Those boundaries should be completed or explicitly re-scoped before closing #310.
