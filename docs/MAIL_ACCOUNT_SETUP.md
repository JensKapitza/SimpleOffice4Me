# Mail account setup and fetch controls

## New accounts

Use the e-mail address, display name and password in the basic account form.
Provider discovery fills the IMAP/SMTP defaults; host names, ports, TLS mode,
authentication overrides, folders, Sieve and UI preferences remain available
under **Advanced settings**. The server still validates the final configuration
and accepts only TLS or STARTTLS.

New accounts require either a password or an administrator-bound IMAP password
environment variable. The account form defaults to storing entered credentials
using the installation-key encrypted mail secret store. Unchecking the storage
option uses entered secrets only for the current configuration request; the
success message says they must be re-entered for later connections. Empty
password fields when editing an account preserve saved credentials. Saved
passwords are never returned to the browser. Environment-variable names are
not treated as authorization: the owner, account, protocol and server binding
are checked when a connection is attempted.

## Automatic and manual IMAP access

Each account stores an **Automatic IMAP fetch** setting. When disabled, opening
that account's remote inbox does not connect to IMAP; the local archive remains
available. The inbox page offers an explicit one-time fetch. Existing manual
archive, test-login and compose actions are unchanged. Account edits and the
fetch toggle serialize writes to the account store, and the setting survives
restarts.

The current application has no scheduled IMAP polling worker. This control
governs the implicit remote inbox load in the web reader and is enforced on the
server; it does not delete archived messages or credentials. A remote request
already in progress when the setting changes may finish; subsequent implicit
loads are skipped.
