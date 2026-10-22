# SimpleOffice4Me security threat model

## Scope and trust boundaries
SimpleOffice4Me is a Python-based office and document management application. Treat all HTTP requests, uploads, email content, OCR input, external storage and banking integration responses as untrusted. Assess authenticated and unauthenticated users, privilege boundaries, and multi-user data isolation.

## Priorities
1. Authentication, session security, CSRF, authorization, IDOR and cross-user data access.
2. Filesystem and document storage: path traversal, archive extraction, uploads, downloads, symlinks, file disclosure and deletion.
3. Email processing: MIME attachments, malicious HTML, SSRF, parser abuse and credentials.
4. OCR and document/image/PDF processing: malicious input, subprocess invocation, resource exhaustion.
5. Banking/FinTS and external service integrations: secret handling, transaction integrity, SSRF, authentication and replay.
6. SQL injection, template injection, XSS, command injection, unsafe deserialization, dependency vulnerabilities.
7. API and MCP endpoints: permissions, untrusted tool inputs and privilege escalation.

## Exercise
Source code is under /src. Python dependencies are installed in the scanner image. Inspect application routes, tests and security-related code. Run targeted pytest suites where feasible; tests may require fixtures and external services.

## Severity
Critical: unauthenticated remote code execution, authentication bypass exposing sensitive records, or compromise of stored credentials.
High: privilege escalation, cross-user confidential data access, exploitable arbitrary file read/write, or high-impact SSRF.
Medium: scoped information leaks, CSRF with meaningful impact, stored XSS, or resource exhaustion.
Low: limited hardening issues without demonstrated security impact.

## Restrictions
Use local test fixtures only. Do not interact with production services, send real email, access real banking accounts, or exfiltrate data.
