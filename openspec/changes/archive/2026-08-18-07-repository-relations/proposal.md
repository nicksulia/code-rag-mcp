## Why

The system indexes many repositories but treats each one as an isolated island: the only way to narrow or widen a search is to pass an explicit list of `repo_ids`. Users who work on a service that consumes a shared library, or who own a bounded set of repos ("platform team"), must re-type the same repo lists on every query and still miss relevant code in upstream repos they forgot to name. Modelling explicit relations between repositories lets the retriever scope and expand searches automatically, improving both recall (via dependency traversal) and precision (via group scoping).

## What Changes

- Introduce a **unified repository relation model** persisted in the existing SQLite catalog. A single relation table stores two relation kinds:
  - `group` — undirected membership of a repository in a named collection. A repo may belong to many groups.
  - `depends_on` — a directed edge from a dependent repository to the repository it consumes.
- Relations are **declared manually** by the user through CLI, REST, and MCP surfaces. No manifest parsing or automatic dependency inference is in scope for this change.
- Add relation management operations: create/delete a group, add/remove repos to/from a group, add/remove a dependency edge, and list/inspect relations for a repository.
- Extend search/RAG scope resolution so callers can target repositories by:
  - **group name** — expands to all member repos.
  - **dependency expansion** — an opt-in traversal that widens the target set to a repo's transitive dependencies (upstream), dependents (downstream), or both, up to a configurable depth.
- Rank results from expanded repositories slightly below directly requested repositories so dependency expansion increases recall without drowning out the primary repo.
- Expose the above through new REST endpoints, new MCP tools, and new CLI commands.
- Guard against cycles and self-edges in the dependency graph; group membership and dependency edges are validated against registered repositories.
- Deleting a repository removes its relations; no orphaned edges remain.
- No **BREAKING** changes: existing `repo_ids` behaviour is preserved when no group or expansion option is supplied.

## Capabilities

### New Capabilities
- `repository-relations`: The relation model (groups and directed dependencies), its persistence and integrity rules, the scope-resolution algorithm that turns a caller's request into a concrete repository set, and the relevance treatment of expanded repositories.

### Modified Capabilities
- `api-and-web-ui`: Adds REST endpoints for managing groups and dependency edges, and adds group/expansion parameters to the search and query endpoints.
- `mcp-server`: Adds MCP tools for managing and inspecting repository relations, and adds group/expansion parameters to the existing search and RAG tools.

## Impact

- **Data**: New tables in `data/catalog.db` (`repo_groups`, `repo_group_members`, `repo_dependencies`), created idempotently by `RepoManager._init_db`. Existing tables are untouched, so existing catalogs upgrade in place with no migration step.
- **Code**:
  - `src/models/schema.py` — new dataclasses/enums for groups and dependency edges.
  - `src/ingestion/repo_manager.py` — relation CRUD, integrity checks, cascade delete.
  - `src/service.py` — relation-aware scope resolution feeding `search`, `query_rag`, and `stream_rag`.
  - `src/retriever/hybrid_retriever.py` — down-weighting of chunks from expansion-only repositories.
  - `src/server/api.py`, `src/mcp/server.py`, `src/cli/main.py` — new surface operations and parameters.
- **Tests**: New `tests/test_repository_relations.py`; existing retrieval and MCP tests must continue to pass unchanged.
- **Dependencies**: None added — the relation graph uses the existing `sqlite3` catalog.
