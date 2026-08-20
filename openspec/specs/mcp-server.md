# Specification: Model Context Protocol (MCP) Server

## Status: ACTIVE
## Domain: AI Tooling Integration
## Version: 2.3.0

---

## 1. Overview
The MCP Server implements the JSON-RPC 2.0 Model Context Protocol specification over `stdio` and HTTP, enabling AI coding assistants (Antigravity, Cursor, Windsurf, Claude Code, GitHub Copilot) to directly interact with multi-repository code intelligence, AST semantic search, symbol graph navigation, and dynamic branch switching as native agent tools.

---

## 2. Complete MCP Tool Specifications

### 2.1 `search_codebases`
- **Description**: Perform hybrid multi-vector semantic and BM25F keyword search across indexed codebases with graph boost and caller-callee relationship enrichment.
- **Arguments**:
  - `query` (`string`, **required**): The search query, code concept, identifier, symbol name, or architectural keyword (e.g. `'library content catalog'` or `'create_catalog'`).
  - `repos` (`string[]`, *optional*, default: `null` / all repos): Array of repository IDs or repository names to filter search results (e.g. `["update-api"]`).
  - `limit` (`integer`, *optional*, default: `8`): Maximum number of ranked code chunks to return (must be a positive integer).
  - `groups` (`string[]`, *optional*, default: `null`): Group names whose members join the primary repository set.
  - `expand` (`string`, *optional*, default: `"none"`): One of `none`, `upstream`, `downstream`, `both` — expands the search scope along declared dependency edges.
  - `expand_depth` (`integer`, *optional*, default: `1`): Maximum traversal depth when expanding.

  The response reports the resolved repository scope (primary vs. expanded), and results from expanded repositories are marked as such. An unknown group name returns a structured error naming the group and performs no retrieval. Calls using only `query`, `repos`, and `limit` behave exactly as before these arguments existed.
- **Example Input**:
  ```json
  {
    "query": "library content catalog",
    "repos": ["update-api"],
    "limit": 5
  }
  ```

---

### 2.2 `get_symbol_definition`
- **Description**: Locate where a symbol (function, class, interface, method) is defined, its enclosing source file, line range, docstring, and implementation code.
- **Arguments**:
  - `symbol_name` (`string`, **required**): The exact or partial identifier of the function, class, or method definition to locate (e.g. `'CatalogUpdateRequest'`).
  - `repo_id` (`string`, *optional*, default: `null` / all repos): Specific repository ID to narrow symbol search.
- **Example Input**:
  ```json
  {
    "symbol_name": "CatalogUpdateRequest",
    "repo_id": "update-api"
  }
  ```

---

### 2.3 `get_call_hierarchy`
- **Description**: Retrieve incoming callers and outgoing calls for a function or method across repositories via the code graph.
- **Arguments**:
  - `symbol_name` (`string`, **required**): The target function or method name to trace callers and callees for.
  - `repo_id` (`string`, *optional*, default: `null`): Optional repository ID to constrain search.
- **Example Input**:
  ```json
  {
    "symbol_name": "create_catalog"
  }
  ```

---

### 2.4 `get_cross_repo_api_links`
- **Description**: Discover cross-repository linkages where client applications invoke backend REST/API endpoints across all indexed codebases.
- **Arguments**: None (`{}`)
- **Example Input**: `{}`

---

### 2.5 `list_repositories`
- **Description**: List all registered and indexed code repositories with their file, chunk, and symbol counts, active Git branch, and sync status.
- **Arguments**: None (`{}`)
- **Example Input**: `{}`

---

### 2.6 `update_repository`
- **Description**: Update a repository's active Git branch (checks out and pulls), remote URL/path, or display name, and optionally re-syncs.
- **Arguments**:
  - `repo_id` (`string`, **required**): The unique repository ID to update (e.g. `'update-api'`).
  - `branch` (`string`, *optional*): Target Git branch to check out and track (e.g. `'main'`, `'develop'`, `'feature/pr-51'`).
  - `url` (`string`, *optional*): New Git remote clone URL or local directory filesystem path.
  - `name` (`string`, *optional*): New human-readable display name for the repository.
  - `auto_sync` (`boolean`, *optional*, default: `true`): Whether to immediately check out the branch, pull, and re-index modified files.
- **Example Input**:
  ```json
  {
    "repo_id": "update-api",
    "branch": "main",
    "auto_sync": true
  }
  ```

---

### 2.7 `sync_repository`
- **Description**: Trigger synchronization and incremental indexing for a specific repository.
- **Arguments**:
  - `repo_id` (`string`, **required**): The unique repository ID to synchronize.
  - `force` (`boolean`, *optional*, default: `false`): Force full re-indexing of all files ignoring cache.
- **Example Input**:
  ```json
  {
    "repo_id": "update-api",
    "force": false
  }
  ```

---

### 2.8 `manage_repository_relations`
- **Description**: Declare or remove repository groups and dependency edges (the `group`/`depends_on` relation model), used to scope subsequent searches and RAG queries.
- **Arguments**:
  - `action` (`string`, **required**): One of `create_group`, `delete_group`, `add_to_group`, `remove_from_group`, `add_dependency`, `remove_dependency`.
  - `group` (`string`, *optional*): The group name, required for the group actions.
  - `repos` (`string[]`, *optional*): Repository identifiers or names to add to or remove from a group.
  - `repo` (`string`, *optional*): The dependent repository, required for the dependency actions.
  - `depends_on` (`string`, *optional*): The depended-upon repository, required for the dependency actions.
- Repository identifiers are resolved using the same alias resolution as the other repository tools. Integrity violations — unregistered repositories, self-dependencies, cycles, or duplicate group names — are returned as a structured tool error naming the offending group or repository, leaving stored relations unchanged.
- **Example Input**:
  ```json
  {
    "action": "add_dependency",
    "repo": "service-a",
    "depends_on": "shared-lib"
  }
  ```

---

### 2.9 `get_repository_relations`
- **Description**: Report the repository relation graph (groups and dependency edges) so an agent can decide how to scope a subsequent search.
- **Arguments**:
  - `repo` (`string`, *optional*, default: `null` / whole graph): A repository identifier or name. When supplied, returns that repository's group memberships, direct dependencies, and direct dependents. When omitted, returns all groups with their members and all dependency edges.
- **Example Input**:
  ```json
  {
    "repo": "service-a"
  }
  ```

---

## 3. Protocol Guarantees
- **JSON-RPC 2.0 Compliance**: All methods return `jsonrpc: "2.0"` envelope with unique transaction IDs.
- **Safe Nullability**: Null, omitted, or empty dictionaries for `arguments` are gracefully accepted without runtime errors.
- **Error Formatting**: Failures return standard JSON-RPC error objects or `isError: true` tool call results with descriptive error text.
- **Stderr Logging**: All diagnostic logging writes to `stderr` to ensure `stdout` remains pure JSON-RPC for standard MCP clients.
