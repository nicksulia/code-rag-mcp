## Why

The service, REST API, and MCP layers already fully support scoping a query to explicit repositories, one or more groups, and dependency expansion (`upstream`/`downstream`/`both`) with hop-decay ranking and provenance. The Web UI does not expose this consistently: the "Hybrid Search" tab has no scope controls of its own and silently reads hidden DOM elements that belong to the "RAG Studio" chat tab, and the existing group control is a single-select `<select>` even though the backend accepts an array of group names. Users cannot see or reliably set which repositories/groups/expansion apply to a hybrid search, and cannot combine multiple groups in either tab.

## What Changes

- Move the scope controls (repository checkboxes, group selection, expansion direction, expansion depth) out of the chat-only controls bar and into a single shared "Scope" control cluster in the persistent sidebar, next to the existing "Filter Codebases" repo list.
- Replace the single-select group `<select>` with a multi-select checkbox list, consistent in style with the existing repo filter list, so a query can be scoped to more than one group at once (matches the API's `groups: string[]`).
- Add a live, read-only "resolved scope" summary (e.g. "2 repos ∪ 1 group (4 total) + upstream deps (depth 1)") beneath the controls, updated whenever the selection changes.
- Wire both the RAG Studio (chat) tab and the Hybrid Search tab to read scope from this single shared state instead of tab-local/duplicated DOM elements; remove the now-redundant controls bar markup from the chat tab.
- No backend/API/MCP changes: `repo_ids`, `groups`, `expand`, `expand_depth` semantics and endpoints are unchanged.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `api-and-web-ui`: The "RAG Studio" web UI requirement changes from a chat-tab-local single-group selector to a shared, persistent, multi-group sidebar scope control that also governs the Hybrid Search tab and displays a resolved-scope summary.

## Impact

- `web/index.html`: remove `rag-controls-bar` from the chat tab; add scope controls (group checkboxes, expand/depth selects, scope summary) to `sidebar-filter-box`.
- `web/app.js`: introduce a single shared scope state/read path used by both `handleChatSubmit`/stream and `handleHybridSearch`; remove reliance on `chatGroupSelect`/`chatExpandSelect`/`chatExpandDepth` DOM ids from non-chat code paths; add scope-summary rendering (client-side computation, no new endpoint calls).
- `web/style.css`: minor additions for the multi-select checkbox list and scope-summary element in the sidebar.
- No changes to `src/server/api.py`, `src/mcp/server.py`, `src/service.py`, or `src/retriever/hybrid_retriever.py`.
