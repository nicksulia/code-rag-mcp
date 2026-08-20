## Context

See `proposal.md` — Why. The constraints that shape this design:

- Repository metadata already lives in a single SQLite catalog (`data/catalog.db`) owned by `RepoManager`, which creates its tables idempotently with `CREATE TABLE IF NOT EXISTS` on construction. Existing installations have live catalogs that must keep working untouched.
- There is already a graph store (`GraphStore`), but it models *symbol-level* edges (CALLS, IMPORTS, CROSS_REPO_API) discovered by parsing. Repository relations are user-declared, repo-level, and must exist for repositories that have never been synced.
- `MultiRepoRAGService` is the single choke point where a caller's repository list is turned into concrete ids (`resolve_repo_ids`), and every surface — REST, MCP, CLI — goes through `search`, `query_rag`, or `stream_rag`. Scope resolution therefore has exactly one natural home.
- `HybridRetriever.search` accepts a flat `repo_ids: Optional[List[str]]` and passes it as a filter to both the vector store and the BM25 store. Fusion happens after both retrievals, so any per-repository weighting must be applied at fusion time, not at filter time.
- `SearchResult` is a dataclass with a `to_dict()` used verbatim by the REST and MCP layers, so added fields propagate to all surfaces automatically.

## Goals / Non-Goals

**Goals:**

- One relation store that answers both "which repos are in this group" and "what does this repo depend on" without a second persistence mechanism.
- Scope resolution as a pure, independently testable function: relation graph + request → (primary set, expanded set with direction and hop distance).
- Additive changes only — every existing call signature keeps working with its current defaults.

**Non-Goals:**

- Inferring dependency edges from manifests or imports (explicitly deferred; see proposal).
- Replacing or merging with the symbol-level `GraphStore`.
- Relation-aware chunking, embedding, or index partitioning — relations affect *retrieval scope and ranking* only, never how content is indexed.
- Access control or per-group permissions.
- Cross-machine synchronisation of the relation graph.

## Decisions

### D1: Store relations in the existing catalog DB, not the graph store

Three new tables in `data/catalog.db`, created by `RepoManager._init_db` alongside the existing ones:

- `repo_groups(name TEXT PRIMARY KEY, created_at REAL)`
- `repo_group_members(group_name TEXT, repo_id TEXT, PRIMARY KEY (group_name, repo_id))`
- `repo_dependencies(repo_id TEXT, depends_on_repo_id TEXT, created_at REAL, PRIMARY KEY (repo_id, depends_on_repo_id))`

Composite primary keys give idempotent declaration for free: `INSERT OR IGNORE` satisfies the idempotency requirement without a read-modify-write.

*Why here:* relations are repository metadata with the same lifecycle as the `repositories` table, and `RepoManager` already owns cascade-delete on repository removal. `CREATE TABLE IF NOT EXISTS` means old catalogs upgrade on first open with no migration step, satisfying the "no manual migration" requirement.

*Alternative rejected — extend `GraphStore`:* its edges are derived data, purged and rebuilt per file on every sync (`delete_file_data`). User-declared relations must be durable and must exist before any sync; co-locating them would risk their destruction on re-index.

*Alternative rejected — a JSON sidecar file:* loses referential queries and atomicity with repository deletion, and would need its own concurrency story.

### D2: Model both relation kinds explicitly rather than as one generic edge table

A single `relations(source, target, kind)` table would unify storage, but group membership is a repo↔name association while a dependency is a repo↔repo directed edge with cycle semantics. Separate tables let SQLite enforce the shape and make cycle detection query cleanly against `repo_dependencies` alone. The "unified model" the requirement asks for is a unified *API surface* (one relations concept, one set of operations, one scope resolver), which is preserved.

### D3: Cycle detection on write, via DFS over the existing edges

Before inserting `a -> b`, walk the `depends_on` graph from `b`; if `a` is reachable, reject. Graphs here are tiny (tens of repositories, hand-declared), so an in-memory DFS per write is cheaper and far simpler than a recursive CTE plus a maintained closure table.

*Why on write, not on read:* traversal at query time is on the hot search path and would either need a visited-set guard forever or risk unbounded expansion. Rejecting cycles at declaration keeps the read path a plain bounded BFS. The traversal still carries a visited set for safety, so a hand-edited database cannot hang a search.

### D4: Scope resolution lives in the service, returning a resolved-scope object

A new `resolve_scope(repo_ids, groups, expand, expand_depth) -> ResolvedScope` on `MultiRepoRAGService`, where `ResolvedScope` carries `primary: List[str]`, `expanded: Dict[str, Tuple[direction, hops]]`, and `all_repo_ids: List[str]`.

Expansion is a BFS from the primary set over `repo_dependencies` — following edges forward for `upstream`, backward for `downstream`, both for `both` — recording the first (shortest) hop distance at which each repository is reached. Membership in the primary set always wins, so a repository is never both primary and expanded.

Existing repository-id resolution (`resolve_repo_ids`, which already tolerates slugs, names, and URLs) runs first, so groups and dependency endpoints accept the same loose identifiers users already type. `search`, `query_rag`, and `stream_rag` gain `groups`, `expand`, and `expand_depth` keyword arguments defaulting to `None`/`"none"`/`1`; when all are at their defaults, resolution collapses to today's behaviour and returns the same list `resolve_repo_ids` returns now.

*Distinguishing "no scope" from "empty scope":* today `None` means "all repositories". After resolution, `None` still means all, while an empty list means "scope resolved to nothing" and short-circuits to zero results. This is why resolution returns an object rather than a bare list — a bare list cannot express the difference without reusing `None` for two meanings.

### D5: Down-weight expanded repositories as a multiplicative penalty at fusion time

`HybridRetriever.search` gains an optional `expanded_repos: Dict[str, Tuple[str, int]]` parameter. After RRF fusion and the existing symbol/centrality boosts, a chunk whose `repo_id` is in that map has its fused score multiplied by a hop-decaying factor (`0.85 ** hops`, one constant on the retriever).

*Why multiplicative after fusion, not a filter or a pre-retrieval weight:* both stores are queried with the union of repositories in one call, keeping retrieval cost flat and preserving RRF's rank semantics. A multiplicative penalty guarantees the ordering the spec requires — an equally-scoring primary chunk always outranks an expanded one — while leaving a strong expanded-only match able to surface, which a hard filter or an additive penalty floor would not.

*Alternative rejected — separate searches per tier, then merge:* doubles embedding and BM25 work and makes `top_k` semantics ambiguous across tiers.

### D6: Provenance as new optional fields on `SearchResult` and `Citation`

`SearchResult` gains `repo_relation: str` (`"primary"` or `"expanded"`), `relation_direction: Optional[str]`, and `relation_hops: Optional[int]`, all defaulted so existing construction sites compile unchanged. `Citation` gains the same relation marker so the UI can flag expanded citations. Because both surfaces serialise through `to_dict()`, REST and MCP responses pick the fields up without per-surface work.

### D7: Surfaces are thin pass-throughs

REST endpoints, MCP tools, and CLI commands validate and forward to service methods; all integrity checks and error messages originate in `RepoManager` so the three surfaces cannot drift. `RepoManager` raises typed errors (unknown repository, unknown group, self-dependency, cycle, duplicate group); the REST layer maps them to `4xx`, MCP to structured tool errors, and the CLI to non-zero exits.

## Risks / Trade-offs

- **Expansion silently inflates the searched corpus, diluting `top_k`.** → Expansion is opt-in and defaults to depth 1; responses always report the resolved scope so a user can see exactly which repositories were searched and why.
- **The 0.85-per-hop penalty is an unvalidated constant.** → It lives as a single named attribute on `HybridRetriever` and only ever reorders results within an already-retrieved candidate set, so tuning it is a one-line change with no index impact.
- **Cycle rejection can block a legitimately cyclic real-world dependency** (two services that genuinely consume each other). → Groups cover that case without ordering semantics. If demand appears, the constraint can be relaxed later to bounded traversal, since the read path already carries a visited set.
- **Relation writes and repository deletion could race, leaving an edge pointing at a removed repository.** → Cascade delete runs inside the same SQLite transaction as the repository row removal; resolution additionally skips ids that no longer resolve to a registered repository, so a stale row degrades to a no-op rather than an error.
- **Users may expect relations to improve indexing, not just search.** → Documented as a non-goal; relations never influence chunking or embedding, so enabling them cannot invalidate an existing index and no re-sync is required.

## Migration Plan

No data migration. New tables are created on the next `RepoManager` construction; an existing catalog gains three empty tables and behaves identically until a relation is declared. Rollback is reverting the code — the added tables are inert to prior versions, which never query them.
