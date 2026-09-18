# GEMINI.md

## Project
SimpleOffice4Me is a self-hosted Flask application. Work incrementally and preserve existing behavior unless an issue explicitly requires a change.

## Priorities
1. Security and data integrity.
2. Correctness and backwards compatibility.
3. Tests and CI stability.
4. Maintainability and consistent architecture.
5. Documentation and usability.

## Working rules
- Read the relevant issue, existing implementation, tests and documentation before editing.
- Prefer root-cause fixes over local workarounds.
- Do not invent cryptographic primitives or weaken certificate/TLS validation.
- Never commit secrets, credentials, private keys or local configuration.
- Keep migrations and stored data backwards compatible unless the task explicitly defines a migration.
- Reuse existing project abstractions before adding parallel implementations.
- For federation, storage and security changes, preserve explicit authorization and least privilege.
- Do not push directly to main. Work on a branch and use a pull request.
- Keep commits focused and explain non-obvious architectural decisions.

## Validation
Before declaring work complete:
- Install the project in editable mode with development/test dependencies when available.
- Run the relevant tests first, then the broader test suite when practical.
- Run security/static checks already configured in the repository.
- Report tests that could not be run and why.
- Do not hide failing tests.

## Useful setup
Python >= 3.10 is required. The Codespace installs the package in editable mode with the security and SFTP optional dependencies.

## Current architecture work
For P2P object storage, VFS, cryptography, disaster recovery and federation-transfer work, treat GitHub issue #306 as the requirements source. Analyze existing code and relevant PRs before implementing that architecture.
