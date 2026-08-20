## Context

`web/app.js` keeps a single global `state` object and an `elements` lookup of DOM ids. Repository filtering already lives in `state.selectedRepoIds` (a `Set`) rendered as checkboxes in `#repo-filter-list` inside the persistent sidebar (`sidebar-filter-box`). Group/expansion controls (`#chat-group-select`, `#chat-expand-select`, `#chat-expand-depth`) currently live only in the chat tab's `rag-controls-bar`, but `handleHybridSearch` (the Hybrid Search tab) already reads those same element ids directly — the two tabs are accidentally coupled today rather than intentionally sharing state. See `proposal.md` - Why for the full problem statement.

## Goals / Non-Goals

**Goals:**
- One source of truth (`state.scope`) for repo/group/expand/depth selection, read by both the chat (RAG Studio) and Hybrid Search request paths.
- Multi-group selection (checkbox list) instead of a single `<select>`.
- A cheap, always-current scope summary rendered from local state (no extra network round-trip per keystroke/toggle).
- Preserve all existing request payload shapes (`repo_ids`, `groups`, `expand`, `expand_depth`) sent to `/api/v1/search`, `/api/v1/rag/query`, `/api/v1/rag/stream` — this is a UI-only refactor.

**Non-Goals:**
- No changes to the Cross-Repo Graph tab; it stays scope-independent (out of scope per the proposal's Impact section).
- No backend/API/MCP changes.
- No server round-trip to `resolve_scope`/preview endpoints for the summary; the summary is computed client-side from already-loaded `state.groups`/`state.repos` data (fetched once on load, same as today).

## Decisions

**1. Move scope state into the sidebar, keyed by a single `state.scope` object** (`{ repoIds: Set, groupNames: Set, expand: 'none'|'upstream'|'downstream'|'both', expandDepth: number }`), replacing the ad hoc `state.selectedRepoIds` + scattered element reads.
- *Alternative considered*: keep per-tab local state and sync them on tab switch. Rejected — reintroduces the exact duplication/coupling bug being fixed, and risks the two tabs drifting out of sync again.

**2. Render group selection as a checkbox list** (`#group-filter-list`) styled like the existing `#repo-filter-list`, populated from `state.groups` (already fetched via `GET /api/v1/groups`).
- *Alternative considered*: `<select multiple>`. Rejected — worse UX/accessibility for toggling a handful of items and inconsistent with the adjacent repo checkbox list.

**3. Compute the scope summary purely client-side**: primary set = selected repo ids ∪ members of selected groups (deduped, from data already in `state.repos`/`state.groups`); expanded-set *count* is not derived (would require graph traversal client-side) — instead, when `expand !== 'none'`, show a qualitative note ("+ {direction} deps, depth {N} — resolved at query time") rather than a precise expanded count.
- *Alternative considered*: call `service.resolve_scope` (via a small preview endpoint) on every change for an exact count including expansion. Rejected for v1 — adds latency/debouncing complexity and a new API surface for a cosmetic summary; the authoritative resolved scope is already returned in real search/RAG responses (`res_obj["scope"]`) and shown per-result via existing "Expanded" badges. Revisit if users want a precise pre-query count.

**4. Delete the chat tab's `rag-controls-bar` markup entirely**; both `handleChatSubmit`/streaming and `handleHybridSearch` read `state.scope` directly instead of any tab-local DOM element.

**5. Persist `state.scope` to `localStorage`** (mirroring the existing `theme` persistence pattern) so scope survives page reloads, consistent with users expecting their working set to stick across sessions.
- *Alternative considered*: session-only (reset on reload). Rejected — repo checkboxes arguably should also persist, and starting every page load unscoped (implicitly "all repos") is a bigger surprise than remembering the last scope.

## Risks / Trade-offs

- **[Risk]** Removing per-tab controls could regress users who relied on independently scoping chat vs. search. → **Mitigation**: this was never a reliable, intentional feature (Hybrid Search silently inherited chat's hidden state already); call it out in the PR/commit description as an intentional simplification, and the shared model is strictly more discoverable.
- **[Risk]** Client-side scope summary can drift from the server's authoritative resolution once expansion is involved (e.g., a stale group member list). → **Mitigation**: summary text explicitly says expansion is "resolved at query time" rather than claiming a precise count; per-result "Expanded" badges remain the source of truth for what was actually used.
- **[Risk]** `localStorage`-persisted scope could reference a group/repo that was later deleted. → **Mitigation**: on load, filter `state.scope.repoIds`/`groupNames` against the freshly fetched `state.repos`/`state.groups` before rendering, dropping stale ids silently (same defensive pattern should already be considered for `selectedRepoIds` today).

## Migration Plan

- Single-release UI change; no data migration, no API versioning.
- Rollout: land `web/*` changes together (index.html + app.js + style.css) since the tab markup removal and shared-state read paths are interdependent.
- Rollback: revert the commit; no persisted server-side state changes to unwind. Any stale `state.scope` in a user's `localStorage` is harmless (filtered against live data per the risk mitigation above) and is naturally overwritten by future scope changes.
