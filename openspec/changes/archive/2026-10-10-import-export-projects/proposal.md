# Proposal

## Why

Users currently register repositories and recreate their groups and dependencies individually in the web UI. A portable JSON configuration lets them back up or share that setup and restore it without copying machine-specific indexes or accidentally overwriting an existing catalog.

## What Changes

- Add **Import JSON** and **Export JSON** actions to the Repositories hub. "Projects" means the existing registered repositories; this change does not introduce a separate project or workspace entity.
- Export the complete repository configuration, groups, and declared dependencies in a versioned JSON document. Exclude code, embeddings, derived graphs, runtime statistics, browser preferences, and credentials.
- Preview imports before confirmation, including new repositories, unchanged repositories, additional group memberships, dependencies, conflicts, and local-path portability warnings.
- Merge additions atomically: identical repository definitions are no-ops, conflicting repository definitions are rejected, and existing groups gain memberships without losing current members. Invalid documents leave the catalog unchanged.
- Register imported repositories without cloning or indexing. Users adjust local paths with the existing edit UI and invoke existing sync controls separately.
- Keep existing repository management, search, REST, MCP, and CLI behavior unchanged.

## Capabilities

### New Capabilities

- `project-transfer`: Versioned configuration export, validated non-destructive import and preview, atomic catalog persistence, and the corresponding browser controls.

### Modified Capabilities

None. The durable spec inventory contains MCP tools, MCP HTTP transport, and retrieval quality; none of their requirements change. Archived repository-relation requirements remain applicable, including rejection of missing endpoints, self-dependencies, and cycles.

## Impact

- `src/ingestion/repo_manager.py`: Snapshot export and transaction-scoped import planning/persistence in the existing SQLite catalog, avoiding the current `INSERT OR REPLACE` registration path for imports.
- `src/service.py` and `src/server/api.py`: Thin service methods and new JSON transfer endpoints used by the browser.
- `web/index.html`, `web/app.js`, and `web/style.css`: Download, file selection, preview/confirmation, error states, and safe rendering of imported metadata throughout the affected repository/group UI.
- Focused catalog and HTTP tests following the existing `unittest` patterns and browser verification of the complete transfer flow.
- `README.md` changes are explicitly out of scope; necessary user guidance belongs in the transfer UI.
- No new runtime dependencies, database migration, CLI command, or MCP tool.
