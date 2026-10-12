# Proposal

## Why

Users need to choose the SSH identity used to clone and update each Git repository without maintaining separate shell configurations or sharing private keys through project configuration files. A dedicated key-management tab and optional manual associations provide this control while preserving inherited system SSH behavior for unassigned repositories.

## What Changes

- Add an **SSH Keys** tab for registering named references to existing server-side OpenSSH private-key files, viewing public fingerprints and associated repositories, renaming references, and removing unused references. First-version scope is POSIX/OpenSSH; uploads, pasted key material, key generation, and stored passphrases are excluded.
- Add **SSH authentication** selectors to Add/Edit Repository: **System SSH (default)** or a registered key. Persist optional machine-local associations before any initial clone, branch switch, fetch, or pull.
- Apply the selected identity consistently through UI, CLI, and MCP operations. Unassigned repositories retain the server's inherited SSH agent and configuration, including keys previously loaded with `ssh-add`. A failed assigned key never retries using system identities.
- Isolate the assigned identity from other agent/config identities while retaining effective host aliases, user/port, proxy routing, and known-host verification. Encrypted keys require the matching key already unlocked in the inherited agent; there are no in-app unlock prompts.
- Store references and public metadata, not private-key contents. Validate paths, ownership, permissions, key format, and fingerprint; revalidate before remote use. Removing a reference never deletes the user's key file, and deletion is blocked while associated.
- Keep profiles and associations out of version 1 project export/import. Ordinary repository saves and indexing do not erase associations; new imports use system SSH, while identical existing imports preserve their machine-local association.
- **BREAKING**: HTTP SSH credential use is local-only. Refuse non-loopback `serve` when the catalog contains key profiles; deny key management and SSH Git network operations on non-loopback HTTP servers, including inherited-agent use. Guard the entire loopback REST/MCP surface against hostile Host/Origin requests, remove wildcard loopback REST CORS, and constrain static-file serving to the web root. Direct trusted CLI/stdio operation remains supported.
- Extend the applied sync-failure contract with safe SSH-specific errors and useful UI guidance. Preserve its explicit local-only indexing, saved-configuration failure context, and truthful browser outcomes; local-only indexing does not require an assigned key to be available.
- Build on the implemented `fix-repository-sync-failures` baseline, whose 26 tasks are checked off. Recheck its behavioral coverage before SSH implementation rather than reimplementing it; its transport delta is not yet synchronized into the durable spec and must be synchronized/archived before this change's transport delta. This change does not revise the prerequisite's artifacts.

## Capabilities

### New Capabilities

- `repository-ssh-keys`: Server-side key references, safe lifecycle, manual repository association, explicit identity isolation, inherited SSH fallback, transfer exclusion, and UI/CLI/MCP integration.
- `local-credential-http-boundary`: Loopback-only managed credentials, whole-surface Host/Origin enforcement, bounded protected endpoints, and file-serving containment.

### Modified Capabilities

- `mcp-http-transport`: Strengthen the loopback origin boundary and extend the prerequisite's compatibility amendment for optional `ssh_key_id` updates and additive repository-list metadata, preserving protocol framing, configured bearer authentication, and unrelated tools.

## Impact

- `src/ingestion/repo_manager.py`, a focused SSH helper beside it, and `src/models/schema.py`: Additive catalog tables, safe metadata projection, assignment-safe repository persistence, validation, and operation-scoped OpenSSH execution.
- `src/service.py`: Extend the existing save-before-Git flow with atomic configuration/association changes and a shared identity snapshot across update preparation and automatic sync; add key-file read/index protection while preserving the applied error/state contract.
- `src/server/api.py` and `src/cli/main.py`: Local key endpoints, listener/request guards, static containment, and CLI assignment flags; no remote-authentication platform.
- `src/mcp/server.py`: Optional nullable update assignment and key-ID metadata, without key-provisioning tools or private-path exposure through MCP.
- `web/index.html`, `web/app.js`, `web/style.css`, and `README.md`: Dedicated tab and selectors integrated with existing checked repository requests, strict refresh, and saved-add retry state; visible errors/deployment guidance and inherited-agent explanation.
- Focused catalog/OpenSSH/API/security/CLI/MCP/UI tests. No new Python package dependency, no private-key vault, and no changes to the project-transfer document format.
