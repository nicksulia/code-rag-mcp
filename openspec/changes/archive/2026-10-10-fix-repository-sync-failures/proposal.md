# Proposal

## Why

Existing Git checkouts can report a successful sync after fetch, checkout, or pull fails, while the browser treats HTTP failures as completed syncs. Fix this independently of SSH-key management so users and future authentication features can rely on truthful outcomes without losing existing indexes.

## What Changes

- **BREAKING**: Default Git sync and branch/remote updates fail explicitly when a required Git command fails; they no longer silently index a stale checkout. Failed remote preparation leaves existing indexes and the last-successful-sync timestamp unchanged.
- Execute Git operations with checked results, finite timeouts, and noninteractive authentication, preserving inherited Git/SSH configuration and agent access. Share error handling across sync and repository updates.
- Provide explicit cached-checkout indexing through a `local_only` sync option, CLI `--local-only`, and a distinct **Index existing checkout** UI action. This never accesses the remote and never reports remote-sync success.
- Propagate bounded, credential-safe errors through repository status, REST, CLI, and both MCP transports. Distinguish configuration successfully saved from a subsequent Git/indexing failure.
- Check browser HTTP responses; display repository errors and per-repository Sync All results, continue after individual failures, and restore controls even when requests or refreshes fail.
- Make CLI sync failures produce a nonzero exit status, including partial bulk failures, while still attempting remaining repositories.
- **BREAKING**: Sync HTTP requests reject malformed or unsupported options instead of silently ignoring them; existing empty-body requests remain supported.
- Document the corrected behavior and explicit offline option. Preserve successful default sync response fields, local-directory indexing, search scope, theme, and import/export behavior.
- Exclude SSH-key registration/assignment/storage, REST authentication or CORS changes, remote-deployment policy, background jobs, and transactional rollback of Git working-tree changes. The later SSH-key change depends on this reliability contract.

## Capabilities

### New Capabilities

- `repository-sync`: Checked remote preparation, truthful sync/index outcomes, explicit cached-checkout indexing, safe failure reporting, persistence semantics, and browser/CLI/MCP integration.

### Modified Capabilities

- `mcp-http-transport`: Narrowly amend the historical "stdio transport unchanged" requirement to permit corrected repository-operation failures and the additive `local_only` sync argument on both transports, without changing framing, protocol negotiation, authentication, or unrelated tools.

## Impact

- `src/ingestion/repo_manager.py`: Shared checked Git execution and safe domain errors; remove swallowed failures and unchecked fetch/pull results from both Git paths.
- `src/service.py`: Explicit local-only indexing, failure/state propagation, and saved-configuration context without changing the existing index pipeline.
- `src/server/api.py`: Strict parsing for sync options and repository-operation error responses, retaining existing successful response shapes and deployment security behavior.
- `src/cli/main.py` and `src/mcp/server.py`: Wire the optional indexing mode, surface safe errors, and correct CLI exit outcomes.
- `web/app.js`, `web/index.html`, and `web/style.css`: Checked sync requests, visible outcomes/status, explicit cached-index action, and saved-versus-failed modal feedback using existing vanilla JavaScript patterns.
- `README.md` and focused repository/service/API/CLI/MCP tests: User guidance and regression coverage. No new runtime dependencies or database schema changes.
