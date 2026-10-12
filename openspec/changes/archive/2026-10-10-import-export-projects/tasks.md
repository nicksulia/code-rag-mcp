# Tasks

## 1. Configuration format and snapshot export

- [x] 1.1 Implement the version 1 configuration projection, normalization, exact field/type validation, safe IDs, supported credential-free Git addresses, and typed transfer summaries near `RepoManager` (using `src/models/schema.py` only where shared types are needed); add `tests/test_project_transfer.py` cases proving valid local/Git/null-branch/empty inputs and rejection of unsafe IDs, invalid versions/types, duplicates, unknown fields, and credential-bearing or unsupported addresses.
- [x] 1.2 Implement export from one catalog read transaction with deterministic ordering, canonical UTF-8 serialization, a trailing newline, and the 1 MiB export limit; verify catalog tests cover complete groups/dependencies, byte-identical exports after runtime-only changes, Unicode names, empty catalogs, and explicit failures for unsupported legacy metadata or dangling relations.

## 2. Read-only planning and atomic additive import

- [x] 2.1 Implement the shared import planner over document and catalog snapshots, including repository equality/conflicts, group unions, missing memberships/edges, destination-only references, and local-path warnings; verify catalog tests assert exact summary counts/identifiers, read-only previews, idempotency, preservation of unrelated data, and conflicts blocking all changes.
- [x] 2.2 Reuse or extract small shared relation-integrity helpers to validate the combined graph without separately committed public mutations; verify unknown endpoints, self-dependencies, incoming-only cycles, cycles through destination edges, and ordinary relation operations with `python3 -m unittest discover -s tests -p 'test_repository_relations.py'`.
- [x] 2.3 Implement confirmation on one SQLite connection with `BEGIN IMMEDIATE`, current-catalog replanning, insert-only repository persistence, and one commit/rollback boundary; verify tests force a failure after an insertion, reopen the catalog to prove rollback, and introduce a conflicting registration after preview to prove revalidation.
- [x] 2.4 Prove metadata-only behavior with catalog tests mocking source/filesystem discovery, clone, sync, and embedding operations; verify none are called, unavailable paths/servers do not block import, new entries have the expected empty runtime fields, and identical indexed entries retain their runtime metadata and indexes after reopening.

## 3. Transfer service and HTTP endpoints

- [x] 3.1 Add metadata-only delegation methods in `src/service.py` and `GET /api/v1/projects/export` in `src/server/api.py`; add `tests/test_api_project_transfer.py` using the existing in-memory handler pattern and verify canonical response bytes, JSON content type, attachment filename, complete export, and credential-safe export errors.
- [x] 3.2 Add dedicated import request parsing before the existing permissive POST parser, preserving duplicate-key detection and enforcing the 2 MiB raw request limit before reading; verify HTTP tests cover malformed JSON, invalid UTF-8, duplicate keys, non-object bodies/envelopes, unknown keys, invalid `dry_run` types, required `document`, and size limits at and above the threshold.
- [x] 3.3 Wire preview/default `dry_run: true` and explicit confirmation to the planner/transaction methods; verify HTTP tests assert response fields and exact counts, nonmutating conflicting previews, `400` validation errors, `409` confirmation conflicts, `413` oversized requests, `500` injected persistence failures, and no credential values or success-shaped errors.

## 4. Browser import/export and metadata-safe rendering

- [x] 4.1 Add Repositories hub transfer controls and the import modal in `web/index.html`/`web/style.css`, plus checked-response Blob download and object-URL cleanup in `web/app.js`; verify both controls work with an empty catalog, successful export downloads `projects.json`, HTTP errors do not download, and `node --check web/app.js` passes.
- [x] 4.2 Implement JSON file selection with the 1 MiB pre-read limit, raw-text-preserving preview requests, per-category previews/conflicts/warnings, and explicit confirmation; verify browser checks cover valid input, duplicate JSON keys reaching server validation, oversized files, invalid documents, disabled conflict confirmation, and offline local-path warnings.
- [x] 4.3 Add selection-generation guards, cancellation/reset handling, in-flight protection, and visible request errors; verify replacing/canceling a file during preview cannot enable stale confirmation, double clicks cannot commit twice, and failed requests restore usable controls.
- [x] 4.4 Make imported names/paths/groups inert across preview, cards, filters, group membership management, and dependency controls by using text/attribute-safe rendering and bound listeners rather than interpolated dynamic handlers; verify quotes, HTML-like text, Unicode, and valid IDs such as `constructor`/`toString` display and operate correctly without script execution or prototype-key collisions.
- [x] 4.5 Refresh groups, repositories/relations, statistics, and scope after success while preserving valid selections/theme, and propagate transfer refresh failures instead of silently swallowing them; verify actual import counts appear, imported repositories are not indexed, and an injected post-commit refresh failure reports that persistence succeeded rather than claiming import failed.

## 5. Cross-surface integration verification

- [x] 5.1 Run the combined targeted regression suite with `PYTHONPATH=tests python3 -m unittest test_project_transfer test_api_project_transfer test_repository_relations test_api_relations test_repo_update test_scope_resolution test_mcp_server test_mcp_http_transport`; verify transfer, existing relations/search scopes, updates, and unchanged REST/MCP behavior pass together.
- [x] 5.2 Export a representative local/Git catalog with groups/dependencies, import it into a fresh temporary data directory, then reimport and test a conflicting document; verify the first import survives restart, reimport adds nothing, conflicts leave data unchanged, no source/network/model operations occur, and browser selections/theme remain intact.
- [x] 5.3 Verify `node --check web/app.js` and `openspec validate import-export-projects --strict --json`, review the final diff for only related changes, and confirm the version 1 JSON contract agrees with the implemented HTTP/download shapes and `README.md` remains unchanged.
