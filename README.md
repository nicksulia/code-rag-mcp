# ⚡ Multi-Repository Code Search Engine

A production-grade code retrieval and search system engineered for querying and navigating multiple source code repositories simultaneously, designed and planned using the **[OpenSpec](https://github.com/Fission-AI/openspec)** Spec-Driven Development framework. Ranked results (with repository, file, line, symbol, and graph metadata) are the integration boundary for external cloud LLM clients, which perform generation in their own environment.

---

## 🌟 Key Features

1. **Multi-Repository Ingestion & Incremental Sync**:
   - Manages local codebase directories and remote Git repositories.
   - Respects `.gitignore` rules and excludes binaries/lockfiles automatically.
   - SHA-256 hash tracking and Git commit detection for instantaneous incremental updates.

2. **AST-Aware Semantic Code Chunking**:
   - Language-aware structural parsing for Python, TypeScript/JavaScript, Go, Rust, Java, C/C++, HTML/CSS, SQL, and Markdown.
   - Preserves function, method, class, and interface boundaries.
   - Injects scope headers (`// [Context] Repository | File | Scope | Imports | Doc`).

3. **Hybrid Dense + Lexical Indexing**:
   - **Dense Vector Search**: Semantic subword feature vectors with cosine similarity + support for external embeddings (Gemini, OpenAI, Voyage AI, Ollama). Local Ollama embeddings default to `qwen3-embedding:0.6b`, loaded on demand and released when idle.
   - **Sparse BM25 Search**: Code-tailored tokenizer splitting `camelCase` and `snake_case` tokens with symbol boosting.
   - **Reciprocal Rank Fusion (RRF)**: Merges dense and sparse rankings with exact identifier boosts.

4. **Symbol Graph & Cross-Repository Dependency Linkage**:
   - Extracts symbol definitions, callers, callees, and imports in SQLite.
   - Automatically maps **frontend client API calls** (e.g. `apiClient.post('/api/v1/auth/login')`) to **backend API route handlers** across different repositories.

5. **Interfaces**:
   - **Modern Web UI**: Hybrid Search as the primary query experience, repository manager, cross-repo API contract map, and code inspector drawer.
   - **Model Context Protocol (MCP) Server**: Exposes stdio tools (`search_codebases`, `get_symbol_definition`, `get_call_hierarchy`, `list_repositories`) to AI coding assistants (Antigravity, Cursor, Claude Code, Windsurf).
   - **CLI**: Fast terminal commands for indexing and searching.
   - **REST API**: `POST /api/v1/search` returns ranked code chunks for external cloud LLM consumers.

---

## 📂 OpenSpec Spec-Driven Planning

All specifications, architectural contracts, and task breakdowns are maintained under `openspec/`:

```
openspec/
├── config.json                     # OpenSpec project configuration
├── specs/                          # Living System Specifications (Source of Truth)
│   ├── repository-management.md    # Repo ingestion & git tracking
│   ├── ast-code-chunking.md        # AST semantic parsing & context injection
│   ├── hybrid-indexing.md          # Dense vector + BM25 lexical index
│   ├── symbol-graph-retrieval.md   # Call graph & cross-repo API linkage
│   ├── context-fusion-reranking.md # RRF fusion & citation packaging
│   ├── rag-generation.md           # LLM prompting & grounded citations
│   ├── mcp-server.md               # Model Context Protocol tools
│   └── api-and-web-ui.md           # REST & Web UI specifications
└── changes/
    └── 01-foundation-and-core-rag/ # Phase 1 Change Proposal
        ├── proposal.md             # Goals, scope, and motivation
        ├── design.md               # Technical architecture & contracts
        └── tasks.md                # Implementation checklist (Completed)
```

---

## 🚀 Quick Start

### 1. Register & Index Repositories

```bash
# Add a local repository
python3 main.py add auth-service ./fixtures/repo_auth_service

# Add another repository
python3 main.py add web-client ./fixtures/repo_web_client

# List all indexed repositories
python3 main.py list
```

### 2. Manage Repository Groups & Dependency Relations

```bash
# Create a repository group
python3 main.py group create platform --repos auth-service shared-schemas

# Declare a dependency edge: web-client depends on auth-service
python3 main.py relation add web-client auth-service

# Inspect relations for a repository
python3 main.py relation show web-client

# Search with group scoping and upstream dependency expansion
python3 main.py search "jwt token" --group platform --expand upstream --expand-depth 1
```

### 3. Search Across Repositories (CLI)

```bash
# Hybrid search across all codebases
python3 main.py search "login user authenticate"

# Search scoped to a group with upstream dependency expansion
python3 main.py search "How does authentication flow between web-client and auth-service?" --group platform --expand upstream
```

### 4. Launch the Interactive Web UI

```bash
python3 main.py serve --host 127.0.0.1 --port 8000
```
Open **[http://localhost:8000](http://localhost:8000)** in your browser.

### 5. Connect to AI IDEs via MCP (Model Context Protocol)

Add this MCP server entry to your AI IDE configuration (Antigravity / Cursor / Claude Code):

```json
{
  "mcpServers": {
    "multi-repo-code-rag": {
      "command": "python3",
      "args": ["/Users/nick-work-pc/.gemini/antigravity/scratch/multi-repo-code-rag/main.py", "mcp"]
    }
  }
}
```

---

## 🧠 Embedding Model Runtime

The engine runs as a **single instance per data directory** and keeps the local embedding model resident **only while it is working**.

- **Default model**: `qwen3-embedding:0.6b` (install once with `ollama pull qwen3-embedding:0.6b`). Override with `--embedding-model` or `$OLLAMA_EMBEDDING_MODEL`.
- **On-demand residency**: the model is never loaded at startup. It loads on the first embedding of an indexing run or search, and is released once the last in-flight operation finishes and the idle grace elapses. Overlapping requests share one load and produce one release.
- **Residency policy** via `--keep-alive` or `$EMBEDDING_KEEP_ALIVE`:

  | Value | Behavior |
  |---|---|
  | *(unset)* | Release after 30s of inactivity (default) |
  | `0` | Release immediately after the last operation |
  | `45s`, `5m` | Release after that idle grace |
  | `always` | Keep the model resident for the process lifetime |

- **Single instance**: startup takes an exclusive lock on `<data-dir>/.rag-instance.lock`. A second instance fails fast with the owning pid; pass `--allow-multi-instance` to downgrade this to a warning.
- **Inspect / release manually**: `GET /api/v1/models/status` reports residency, active operations, policy, and index provenance. `POST /api/v1/models/unload` (or `python3 main.py unload`) releases the model, returning `409 busy` while an operation is in flight.

### Automatic reindex on model change

The dense index records the provider, model, and vector dimension that produced its vectors (`<data-dir>/index_meta.json`). When the configured embedding model changes — for example on upgrade from `qwen3-embedding:4b` (2560 dims) to the `qwen3-embedding:0.6b` default (1024 dims) — the affected repositories are **automatically re-embedded** before search results are served:

- chunk text, symbol graph, and BM25 lexical index are preserved (embedding-only pass, not a re-parse);
- progress is reported through the normal indexing progress output;
- provenance is written per repository, so an interrupted rebuild resumes with the repositories still outstanding;
- searches arriving during a rebuild get `503 reindexing` instead of being scored against vectors from another model.

**Rollback to the previous behavior**: `OLLAMA_EMBEDDING_MODEL=qwen3-embedding:4b EMBEDDING_KEEP_ALIVE=always` restores the old model and always-resident policy; the provenance check then rebuilds back into the 4b vector space with no code change.

---

## 🏷️ Repository Groups & Dependency Relations Architecture

### Topology & Domain Rules
- **Named Repository Groups**: Flat collections of repositories (e.g. `core`, `platform`, `billing`). Deleting a group never deletes underlying repositories.
- **Directed Dependency DAG**: Explicit dependency edges `A -> depends on -> B`. Adding an edge runs write-time cycle detection (raising `DependencyCycleError` on cycles).
- **Scope Resolution**: Combines explicit repository IDs and group members into a primary set, then expands along the graph in `upstream` (dependencies), `downstream` (dependents), or `both` directions up to `expand_depth`.
- **Hop-Decay Ranking**: Chunks retrieved from expanded repositories receive a score multiplier penalty `(0.85 ** hops)` to ensure primary repositories rank first.
- **Provenance Metadata**: Results originating from expanded repositories carry metadata (`repo_relation='expanded'`, `relation_direction`, `relation_hops`) and are visually badged in the UI.

### REST API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/groups` | List all repository groups and their members |
| `POST` | `/api/v1/groups` | Create a new repository group `{"name": "...", "repo_ids": [...]}` |
| `DELETE` | `/api/v1/groups/{name}` | Delete a repository group |
| `POST` | `/api/v1/groups/{name}/members` | Add members to group `{"repo_ids": [...]}` |
| `DELETE` | `/api/v1/groups/{name}/members/{repo_id}` | Remove a member from a group |
| `GET` | `/api/v1/models/status` | Embedding model residency, policy, and dense index provenance |
| `POST` | `/api/v1/models/unload` | Release models now (`409` while an operation is in flight) |
| `GET` | `/api/v1/repos/{repo_id}/relations` | Get repository groups, direct dependencies, and direct dependents |
| `POST` | `/api/v1/repos/{repo_id}/dependencies` | Add dependency edge `{"depends_on": "..."}` |
| `DELETE` | `/api/v1/repos/{repo_id}/dependencies/{target_id}` | Remove a dependency edge |
| `POST` | `/api/v1/search` | Search with optional `groups`, `expand`, and `expand_depth` |

### MCP Tools

- `manage_repository_relations`: Actions `create_group`, `delete_group`, `add_to_group`, `remove_from_group`, `add_dependency`, `remove_dependency`.
- `get_repository_relations`: Returns relations for a single repository or the entire relation graph.
- `search_codebases`: Extended with optional `groups`, `expand`, and `expand_depth` arguments.

---

## 🧪 Running Tests

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```
All unit and integration test suites pass verifying AST chunking, symbol extraction, cross-repo API detection, repository relation DAG & cycle detection, scope resolution & hop-decay retrieval, REST API handlers, MCP protocol, and end-to-end hybrid search retrieval.
