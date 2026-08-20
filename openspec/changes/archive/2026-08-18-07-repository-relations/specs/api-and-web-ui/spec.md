## ADDED Requirements

### Requirement: Repository Relation REST Endpoints

The REST API SHALL expose endpoints for managing repository groups and dependency edges:

- `GET /api/v1/groups`: List all groups with their member repository identifiers.
- `POST /api/v1/groups`: Create a group (`name`, optional initial `repo_ids`).
- `DELETE /api/v1/groups/{name}`: Delete a group without deleting its member repositories.
- `POST /api/v1/groups/{name}/members`: Add one or more repositories to the group.
- `DELETE /api/v1/groups/{name}/members/{repo_id}`: Remove a repository from the group.
- `GET /api/v1/repos/{repo_id}/relations`: Return the repository's group memberships, its direct dependencies, and its direct dependents.
- `POST /api/v1/repos/{repo_id}/dependencies`: Declare that this repository depends on a target repository (`depends_on`).
- `DELETE /api/v1/repos/{repo_id}/dependencies/{target_repo_id}`: Remove a dependency edge.

Requests that violate relation integrity — an unregistered repository, a self-dependency, a cycle, or a duplicate group name — SHALL return a `4xx` status with an error message naming the offending group or repository. Deleting a group or edge that does not exist SHALL return `404`.

#### Scenario: Creating a group

- **WHEN** a client posts a new group named `platform` with members `service-a` and `service-b`
- **THEN** the response reports the created group with both members
- **AND** a subsequent `GET /api/v1/groups` includes `platform` with those members

#### Scenario: Inspecting a repository's relations

- **WHEN** a client requests the relations of `service-a`, which is in group `platform` and depends on `shared-lib`
- **THEN** the response lists `platform` under its groups and `shared-lib` under its dependencies

#### Scenario: Cycle rejected over REST

- **WHEN** a client declares a dependency edge that would introduce a cycle
- **THEN** the API responds with a `4xx` status and an error message identifying the cycle
- **AND** the stored relations are unchanged

#### Scenario: Removing a nonexistent membership

- **WHEN** a client deletes a member from a group the repository does not belong to
- **THEN** the API responds with `404`

### Requirement: Relation-Aware Search And Query Parameters

The `POST /api/v1/search`, `POST /api/v1/rag/query`, and `POST /api/v1/rag/stream` endpoints SHALL accept optional relation parameters in addition to the existing `repo_ids`:

- `groups` (string array): group names whose members join the primary repository set.
- `expand` (string): one of `none`, `upstream`, `downstream`, or `both`; defaults to `none`.
- `expand_depth` (integer): maximum traversal depth for expansion; defaults to `1`.

Responses SHALL report the resolved repository scope, distinguishing repositories in the primary set from those reached through expansion. Individual search results SHALL carry their expansion provenance. Requests that omit all relation parameters SHALL behave exactly as before this capability existed.

#### Scenario: Searching by group

- **WHEN** a client posts a search with `groups: ["platform"]` and no `repo_ids`
- **THEN** the results come only from repositories in the `platform` group
- **AND** the response reports the resolved scope as those repositories

#### Scenario: Searching with upstream expansion

- **WHEN** a client posts a search for `repo_ids: ["service-a"]` with `expand: "upstream"` and `expand_depth: 1`, where `service-a` depends on `shared-lib`
- **THEN** the response reports `service-a` as primary and `shared-lib` as expanded
- **AND** any result drawn from `shared-lib` is marked as expanded

#### Scenario: Unknown group rejected

- **WHEN** a client posts a search naming a group that does not exist
- **THEN** the API responds with a `4xx` status and an error naming the group

#### Scenario: Backwards-compatible request

- **WHEN** a client posts a search with only `query`, `repo_ids`, and `top_k`
- **THEN** the endpoint returns the same results it returned before relation parameters existed

### Requirement: Repository Relations In The Web UI

The Repo Hub view SHALL display each repository's group memberships and its declared dependencies, and SHALL provide controls to create and delete groups, manage group membership, and add and remove dependency edges. The RAG Studio SHALL let the user scope a query by group and enable dependency expansion with a direction and depth, and SHALL visually mark citations that came from expanded repositories.

#### Scenario: Managing relations from Repo Hub

- **WHEN** the user assigns a repository to a group from the Repo Hub view
- **THEN** the repository's card shows the group
- **AND** the membership is reflected by the relations endpoint

#### Scenario: Scoping a query by group in RAG Studio

- **WHEN** the user selects the group `platform` and enables upstream expansion before submitting a question
- **THEN** the answer is grounded only in repositories from the resolved scope
- **AND** citations originating from expanded repositories are visually marked as such
