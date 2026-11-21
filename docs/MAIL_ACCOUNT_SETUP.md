# Mail account setup and fetch controls

## New accounts

Use the e-mail address, display name and password in the basic account form.
Provider discovery fills the IMAP/SMTP defaults; host names, ports, TLS mode,
authentication overrides, folders, Sieve and UI preferences remain available
under **Advanced settings**. The server still validates the final configuration
and accepts only TLS or STARTTLS.

New accounts require at least one IMAP or SMTP credential (a password or an
administrator-bound environment-variable reference). IMAP access requires an
IMAP credential; SMTP-only accounts can send without an IMAP password. The
account form defaults to storing entered credentials using the installation-key
encrypted mail secret store. Unchecking the storage option uses entered secrets
only for the current configuration request; the success message says they must
be re-entered for later connections. Empty
password fields when editing an account preserve saved credentials. Saved
passwords are never returned to the browser. Clearing a stored IMAP or SMTP
secret requires its explicit delete checkbox; an empty password field alone
never removes an existing secret. Environment-variable names are not treated
as authorization: the owner, account, protocol and server binding are checked
when a connection is attempted.

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

## Überführen: Mail/Anhang als Aufgabe oder Termin (Issue #1136, slice 5)

The reader offers handover boxes in the live and archived preview
(`POST /documents/mail/reader/handover/task|followup`). A handover creates
a `TodoStore` task (optionally with due date, surfaced as CalDAV VTODO) or
a `CalendarStore` follow-up event from mail metadata only. References are
stable text (Message-ID, SHA-512, account/folder/UID or archive id, plus
attachment name/type/size, never payload bytes), so no copy flood occurs.
Rights are enforced server-side: live mail requires account ownership,
archived mail requires archive permission, tasks require list create
rights. Handover never mutates IMAP state and works in read-only mode.
