## MODIFIED Requirements

### Requirement: REST API Endpoints

The system SHALL expose the following REST API endpoints:

- `GET /api/v1/repos`: List all registered repositories with file/chunk metrics.
- `POST /api/v1/repos`: Register a new repository (`repo_id`, `name`, `source_type`, `url_or_path`, `branch`).
- `PUT /api/v1/repos/{repo_id}`: Update repository metadata (`url_or_path`, `branch`, `name`, `auto_sync`).
- `POST /api/v1/repos/{repo_id}/sync`: Trigger asynchronous or synchronous repository indexing.
- `POST /api/v1/search`: Hybrid search across codebases with dense + lexical ranking. Accepts optional `groups` (string array), `expand` (`none`|`upstream`|`downstream`|`both`, default `none`), and `expand_depth` (integer, default `1`) alongside the existing `repo_ids`; the response reports the resolved repository scope (primary vs. expanded) and marks individual results with their expansion provenance. Requests that omit all relation parameters behave exactly as before.
- `GET /api/v1/graph/links`: List cross-repository API contract linkages.
- `GET /api/v1/symbols/{name}`: Get symbol definitions, callers, and callees.
- `GET /api/v1/groups`: List all repository groups with their member repository identifiers.
- `POST /api/v1/groups`: Create a group (`name`, optional initial `repo_ids`).
- `DELETE /api/v1/groups/{name}`: Delete a group without deleting its member repositories.
- `POST /api/v1/groups/{name}/members`: Add one or more repositories to a group.
- `DELETE /api/v1/groups/{name}/members/{repo_id}`: Remove a repository from a group (`404` if not a member).
- `GET /api/v1/repos/{repo_id}/relations`: Return the repository's group memberships, direct dependencies, and direct dependents.
- `POST /api/v1/repos/{repo_id}/dependencies`: Declare that this repository depends on a target repository (`depends_on`).
- `DELETE /api/v1/repos/{repo_id}/dependencies/{target_repo_id}`: Remove a dependency edge.

Requests that violate relation integrity — an unregistered repository, a self-dependency, a cycle, an unknown group, or a duplicate group name — SHALL return a `4xx` status naming the offending group or repository. Deleting a group, membership, or edge that does not exist SHALL return `404`. The system SHALL NOT expose `POST /api/v1/rag/query` or `POST /api/v1/rag/stream`; grounded generation over search results is the responsibility of external cloud LLM clients.

#### Scenario: Search endpoint preserved

- **WHEN** a client sends `POST /api/v1/search` with a query and optional `repo_ids`, `groups`, `expand`, and `expand_depth`
- **THEN** the API returns ranked code chunks with resolved repository scope and expansion provenance, unchanged from before this change

#### Scenario: RAG endpoints no longer exist

- **WHEN** a client sends `POST /api/v1/rag/query` or `POST /api/v1/rag/stream`
- **THEN** the API returns a `404 Not Found` response because these endpoints have been removed

### Requirement: Web UI Dashboard

The Web UI SHALL provide the following views, with retrieval-oriented search as the primary query experience:

- **Sidebar Scope Controls**: A persistent scope control cluster in the sidebar — repository checkboxes, a multi-select group checkbox list, and dependency-expansion direction/depth selectors — used by the Hybrid Search view. A live, read-only summary of the resolved scope (named repositories, group membership, and expansion state) updates whenever the selection changes.
- **Hybrid Search**: Ranked vector + lexical (RRF) search results view, and the Web UI's primary query experience. Uses the sidebar scope controls when issuing searches, and visually marks results that came from expanded repositories.
- **Repo Hub**: Fleet view of all registered codebases, indexed chunks, active branch indicators, "Edit / Switch Branch" modal dialog, and one-click sync buttons. Displays each repository's group memberships and declared dependencies, with controls to create/delete groups, manage membership, and add/remove dependency edges.
- **Cross-Repo Graph Explorer**: Visual view of cross-repository API endpoints and model dependencies.
- **Code Drawer**: Flyout slide-over drawer to inspect full file source code with highlighted line ranges.

The Web UI SHALL NOT provide a RAG Studio view or any interactive chat/streaming answer interface; retrieval-oriented search is the primary query experience.

#### Scenario: Hybrid Search is the primary query view

- **WHEN** a user opens the Web UI to query the indexed codebases
- **THEN** they are presented with the Hybrid Search view using the shared sidebar scope controls, with no RAG Studio or chat interface available

#### Scenario: RAG Studio removed

- **WHEN** a user navigates the Web UI
- **THEN** no RAG Studio tab, streaming chat pane, or inline citation inspector is present
