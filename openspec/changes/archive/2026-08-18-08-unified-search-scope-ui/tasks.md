## 1. Shared Scope State

- [x] 1.1 Add `state.scope = { repoIds: Set, groupNames: Set, expand: 'none', expandDepth: 1 }` to the `state` object in `web/app.js`, replacing `state.selectedRepoIds` usages with `state.scope.repoIds`.
- [x] 1.2 Load persisted scope from `localStorage` on init (mirroring the existing `theme` pattern); filter `repoIds`/`groupNames` against freshly fetched `state.repos`/`state.groups` to drop stale references.
- [x] 1.3 Persist `state.scope` to `localStorage` whenever it changes (repo checkbox toggle, group checkbox toggle, expand/depth select change).

## 2. Sidebar UI

- [x] 2.1 Add a "Groups" checkbox list section (`#group-filter-list`) to `sidebar-filter-box` in `web/index.html`, styled consistently with `#repo-filter-list`.
- [x] 2.2 Move the "Expansion" and "Depth" `<select>` controls from the chat tab's `rag-controls-bar` into the sidebar, near the new group list.
- [x] 2.3 Add a read-only scope summary element (e.g. `#scope-summary`) below the sidebar controls.
- [x] 2.4 Remove the `rag-controls-bar` block (`chat-group-select`, `chat-expand-select`, `chat-expand-depth`) from the chat tab markup in `web/index.html`.
- [x] 2.5 Add/adjust CSS in `web/style.css` for the group checkbox list and scope summary element.

## 3. App Logic Wiring

- [x] 3.1 Update `elements` lookup in `web/app.js`: remove `chatGroupSelect`/`chatExpandSelect`/`chatExpandDepth`, add `groupFilterList`, `expandSelect`, `expandDepthSelect`, `scopeSummary`.
- [x] 3.2 Implement `renderGroupFilterList()` to populate the sidebar group checkboxes from `state.groups`, wiring toggle handlers that update `state.scope.groupNames` and re-render the summary.
- [x] 3.3 Implement `renderScopeSummary()`: compute the primary set (selected repo ids ∪ members of selected groups, deduped) and render counts plus a qualitative expansion note when `expand !== 'none'`; call it whenever scope changes.
- [x] 3.4 Update `handleChatSubmit` (and the streaming send path) to build its request payload (`repo_ids`, `groups`, `expand`, `expand_depth`) from `state.scope` instead of `state.selectedRepoIds`/`elements.chatGroupSelect`/etc.
- [x] 3.5 Update `handleHybridSearch` to build its request payload from `state.scope` instead of the shared chat DOM elements it currently reads.
- [x] 3.6 Wire the sidebar's expand/depth selects and group checkboxes to call `renderScopeSummary()` (and persist to `localStorage`) on change, alongside the existing repo checkbox change handler.

## 4. Verification

- [x] 4.1 Manually verify: selecting a group + upstream expansion in the sidebar affects both a RAG Studio chat query and a Hybrid Search query identically, without reselecting anything when switching tabs.
- [x] 4.2 Manually verify: selecting two groups at once resolves a primary set that is the union of both groups' members (check via the scope summary and via actual result repo coverage).
- [x] 4.3 Manually verify: reloading the page restores the previously selected scope from `localStorage`, and a scope referencing a deleted group/repo is dropped without error.
- [x] 4.4 Manually verify: existing "Expanded" badges on citations/results still render correctly using the shared scope's `expand`/`expand_depth` values.
- [x] 4.5 Confirm no remaining references to `chat-group-select`, `chat-expand-select`, or `chat-expand-depth` ids in `web/index.html` or `web/app.js`.
