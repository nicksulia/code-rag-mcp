## 1. Data Model & Persistence

- [x] 1.1 Add relation dataclasses and enums to `src/models/schema.py`: `RepoGroup` (name, created_at, members), `RepoDependency` (repo_id, depends_on_repo_id, created_at), `RelationDirection` enum (`upstream`/`downstream`/`both`/`none`), and a `ResolvedScope` dataclass carrying `primary`, `expanded` (repo_id → direction + hops), and `all_repo_ids`, each with `to_dict()`.
- [x] 1.2 Add optional provenance fields to `SearchResult` (`repo_relation` defaulting to `"primary"`, `relation_direction`, `relation_hops`) and a matching relation marker on `Citation`, keeping every existing construction site valid and including the new fields in `to_dict()`.
- [x] 1.3 Extend `RepoManager._init_db` with idempotent `CREATE TABLE IF NOT EXISTS` statements for `repo_groups`, `repo_group_members`, and `repo_dependencies` using the composite primary keys from design D1; verify an existing `data/catalog.db` opens unchanged and gains the three empty tables.
- [x] 1.4 Define typed relation errors in `src/ingestion/repo_manager.py` (unknown repository, unknown group, duplicate group, self-dependency, cycle) so all three surfaces map the same failures consistently.

## 2. Relation Management In RepoManager

- [x] 2.1 Implement group operations: `create_group`, `delete_group`, `add_repos_to_group`, `remove_repo_from_group`, `list_groups`, `get_group_members` — rejecting duplicate group names and unregistered repositories, and making repeated declarations idempotent via `INSERT OR IGNORE`.
- [x] 2.2 Implement dependency operations: `add_dependency`, `remove_dependency`, `get_dependencies`, `get_dependents` — rejecting unregistered repositories and self-dependencies.
- [x] 2.3 Implement write-time cycle detection (design D3): before inserting `a -> b`, DFS the `depends_on` graph from `b` and reject if `a` is reachable, leaving pre-existing edges untouched.
- [x] 2.4 Implement `get_repo_relations(repo_id)` returning group memberships, direct dependencies, and direct dependents, returning empty collections (not an error) for a repository with no relations.
- [x] 2.5 Extend `delete_repo` so group memberships and dependency edges referencing the repository are removed in the same transaction as the repository row.
- [x] 2.6 Write `tests/test_repository_relations.py` covering persistence across a reopened catalog, multi-group membership, idempotent re-declaration, unknown-repo/self/cycle/duplicate-group rejection, cascade delete, and the empty-relations case.

## 3. Scope Resolution

- [x] 3.1 Implement `resolve_scope(repo_ids, groups, expand, expand_depth) -> ResolvedScope` on `MultiRepoRAGService`: run existing `resolve_repo_ids` alias resolution first, union explicit repos with group members to form the primary set, and raise the unknown-group error for a group that does not exist.
- [x] 3.2 Implement bounded BFS expansion over `repo_dependencies` — forward for `upstream`, backward for `downstream`, both directions for `both` — recording the shortest hop distance per repository, honouring `expand_depth` (default `1`, `0` disables), carrying a visited set, and never marking a primary repository as expanded.
- [x] 3.3 Make `resolve_scope` distinguish "no scope supplied" (all repositories) from "resolved to nothing" (short-circuit to zero results), and skip ids that no longer resolve to a registered repository.
- [x] 3.4 Add `groups`, `expand`, and `expand_depth` keyword arguments (defaults `None`/`"none"`/`1`) to `MultiRepoRAGService.search`, `query_rag`, and `stream_rag`, routing them through `resolve_scope` and short-circuiting when the resolved scope is empty.
- [x] 3.5 Add scope-resolution tests covering group expansion, upstream/downstream/both at depths 0, 1 and 2, deduplication of combined explicit repos and groups, unknown group rejection, empty group returning nothing, and unchanged all-repository default behaviour.

## 4. Relation-Aware Retrieval & Ranking

- [x] 4.1 Add an optional `expanded_repos` parameter to `HybridRetriever.search` and a named hop-decay attribute (default `0.85`), applying the multiplicative penalty after RRF fusion and the existing symbol/centrality boosts (design D5).
- [x] 4.2 Populate `repo_relation`, `relation_direction`, and `relation_hops` on each `SearchResult`, and propagate the relation marker onto `Citation` objects produced by the packager and RAG generator.
- [x] 4.3 Add retrieval tests asserting a primary chunk outranks an equally-scoring expanded chunk, that provenance fields report the correct direction and hop distance, and that an expanded-only match still surfaces in the results.
- [x] 4.4 Run the existing retrieval and RAG test suites to confirm searches without relation arguments return identical results.

## 5. REST API & Web UI

- [x] 5.1 Add group endpoints to `src/server/api.py`: `GET/POST /api/v1/groups`, `DELETE /api/v1/groups/{name}`, `POST /api/v1/groups/{name}/members`, `DELETE /api/v1/groups/{name}/members/{repo_id}`.
- [x] 5.2 Add relation endpoints: `GET /api/v1/repos/{repo_id}/relations`, `POST /api/v1/repos/{repo_id}/dependencies`, `DELETE /api/v1/repos/{repo_id}/dependencies/{target_repo_id}`.
- [x] 5.3 Map the typed relation errors to `4xx` responses naming the offending group or repository, and return `404` when deleting a group, membership, or edge that does not exist.
- [x] 5.4 Add optional `groups`, `expand`, and `expand_depth` parameters to `POST /api/v1/search`, `/api/v1/rag/query`, and `/api/v1/rag/stream`, and include the resolved scope (primary vs expanded) in their responses.
- [x] 5.5 Extend the Repo Hub view in `web/` to display group memberships and dependencies per repository, with controls to create/delete groups, manage membership, and add/remove dependency edges.
- [x] 5.6 Extend RAG Studio with a group selector plus expansion direction and depth controls, and visually mark citations that came from expanded repositories.
- [x] 5.7 Add API tests for group and dependency CRUD, `4xx` on cycle and unknown group, `404` on missing membership, and byte-identical search responses for requests that omit all relation parameters.

## 6. MCP Server

- [x] 6.1 Implement the `manage_repository_relations` tool in `src/mcp/server.py` with the `action`/`group`/`repos`/`repo`/`depends_on` arguments, alias resolution, and structured tool errors on integrity violations.
- [x] 6.2 Implement the `get_repository_relations` tool returning a single repository's relations when `repo` is supplied and the whole graph when it is omitted.
- [x] 6.3 Add `groups`, `expand`, and `expand_depth` arguments to `search_codebases` and `query_cross_repo_rag`, report the resolved scope in their responses, and mark expanded results and citations.
- [x] 6.4 Extend `tests/test_mcp_server.py` with coverage for the two new tools, cycle rejection, unknown group rejection, group-scoped search, downstream-expanded RAG, and unchanged behaviour for calls using only the pre-existing arguments.

## 7. CLI & Documentation

- [x] 7.1 Add relation commands to `src/cli/main.py` for creating/deleting groups, managing membership, adding/removing dependencies, and showing a repository's relations, exiting non-zero on integrity errors.
- [x] 7.2 Add `--group`, `--expand`, and `--expand-depth` options to the CLI search and query commands, printing the resolved scope and marking expanded results.
- [x] 7.3 Update `README.md` with the relation model, the new REST endpoints, MCP tools, and CLI commands, and a worked group + dependency-expansion example.

## 8. Verification

- [x] 8.1 Run the full test suite and confirm every pre-existing test passes unchanged.
- [x] 8.2 Verify against a pre-existing `data/catalog.db` that the server starts, the three relation tables are created, and searches issued without relation parameters behave exactly as before.
- [x] 8.3 Run `openspec validate --changes 07-repository-relations --strict` and confirm the change passes.
