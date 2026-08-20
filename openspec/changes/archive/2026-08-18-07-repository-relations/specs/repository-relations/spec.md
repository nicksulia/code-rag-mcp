## Purpose

Defines how repositories are related to one another — as named groups and as directed dependency edges — and how those relations are used to resolve the set of repositories a search or RAG query runs against.

## ADDED Requirements

### Requirement: Unified Relation Model

The system SHALL maintain repository relations under a single model with exactly two relation kinds: `group` (undirected membership of a repository in a named collection) and `depends_on` (a directed edge from a dependent repository to a repository it consumes). A repository MAY belong to any number of groups and MAY participate in any number of dependency edges.

Relations SHALL be persisted alongside repository metadata and SHALL survive process restarts. A catalog created before this capability existed SHALL be usable without a manual migration step; relation storage is initialised on demand and an absent relation store SHALL be treated as "no relations declared" rather than an error.

#### Scenario: Repository belongs to multiple groups

- **WHEN** a repository is added to the groups `platform` and `billing`
- **THEN** inspecting that repository's relations reports membership in both groups
- **AND** listing the members of either group includes that repository

#### Scenario: Relations persist across restarts

- **WHEN** a group and a dependency edge are declared and the system is restarted
- **THEN** both the group membership and the dependency edge are still reported

#### Scenario: Existing catalog without relations

- **WHEN** relations are queried against a catalog that has never had a relation declared
- **THEN** the system reports an empty relation set and does not raise an error

### Requirement: Manual Relation Declaration

The system SHALL allow relations to be declared, inspected, and removed explicitly by the user. The system SHALL NOT infer dependency edges automatically from repository contents, manifests, or import statements.

The system SHALL support these operations:

- create a group with a unique name, and delete a group
- add a repository to a group, and remove a repository from a group
- add a `depends_on` edge between two repositories, and remove such an edge
- list all groups, list the members of a group, and list the relations of a single repository

#### Scenario: Declaring a dependency edge

- **WHEN** the user declares that repository `service-a` depends on repository `shared-lib`
- **THEN** the relations of `service-a` list `shared-lib` as a dependency
- **AND** the relations of `shared-lib` list `service-a` as a dependent

#### Scenario: Removing a group membership

- **WHEN** the user removes repository `service-a` from group `platform`
- **THEN** `service-a` is no longer reported as a member of `platform`
- **AND** the group `platform` continues to exist with its remaining members

#### Scenario: Deleting a group does not delete repositories

- **WHEN** the user deletes the group `platform`
- **THEN** the group no longer exists and is not returned when listing groups
- **AND** every repository that was a member remains registered and indexed

#### Scenario: No automatic inference

- **WHEN** a repository containing manifest files that reference another registered repository is synchronised
- **THEN** no dependency edge is created for that repository

### Requirement: Relation Integrity

The system SHALL reject relation declarations that would leave the relation graph inconsistent, and SHALL report a clear error naming the offending repository, group, or edge.

The system SHALL reject:

- a group membership or dependency edge that references a repository which is not registered
- a self-referential dependency edge (a repository depending on itself)
- a dependency edge that would introduce a cycle in the `depends_on` graph
- the creation of a group whose name is already in use

Declaring a relation that already exists SHALL be idempotent: it SHALL succeed without creating a duplicate.

#### Scenario: Unknown repository rejected

- **WHEN** the user declares a dependency on a repository identifier that is not registered
- **THEN** the system rejects the declaration with an error naming the unknown identifier
- **AND** no edge is stored

#### Scenario: Self-dependency rejected

- **WHEN** the user declares that repository `service-a` depends on `service-a`
- **THEN** the system rejects the declaration with an error
- **AND** no edge is stored

#### Scenario: Cycle rejected

- **WHEN** `core-utils` is declared to depend on `service-a`, given that `service-a` already depends on `shared-lib` and `shared-lib` already depends on `core-utils`
- **THEN** the system rejects the declaration with a cycle error
- **AND** the pre-existing edges are unchanged

#### Scenario: Duplicate declaration is idempotent

- **WHEN** the user declares an already-existing group membership a second time
- **THEN** the operation succeeds
- **AND** the repository is reported as a member exactly once

#### Scenario: Deleting a repository removes its relations

- **WHEN** a repository that is a group member and has dependency edges is deleted
- **THEN** it is no longer reported as a member of any group
- **AND** no dependency edge referencing it remains

### Requirement: Relation-Aware Scope Resolution

Search and retrieval callers SHALL be able to describe their target repositories using group names and dependency expansion in addition to explicit repository identifiers. The system SHALL resolve such a request into a concrete set of registered repository identifiers before retrieval runs.

Resolution SHALL behave as follows:

- Explicitly named repositories and the members of every named group form the **primary set**.
- Dependency expansion is opt-in and SHALL support a direction of `upstream` (the repositories the primary set depends on), `downstream` (the repositories that depend on the primary set), or `both`.
- Expansion SHALL traverse the `depends_on` graph transitively up to a caller-supplied maximum depth, defaulting to `1`. A depth of `0` SHALL disable expansion.
- Repositories reached only through expansion form the **expanded set**; a repository that is in the primary set SHALL NOT also be counted as expanded.
- The resolved set SHALL contain no duplicates.
- When no repositories, groups, or expansion options are supplied, the system SHALL search all registered repositories, preserving existing behaviour.
- A request naming a group that does not exist SHALL be rejected with an error naming the group.
- A request whose resolved set is empty (for example, an existing but empty group) SHALL return no results rather than silently searching all repositories.

#### Scenario: Group name expands to members

- **WHEN** a search targets group `platform`, which contains `service-a` and `service-b`, with expansion disabled
- **THEN** retrieval runs against exactly `service-a` and `service-b`

#### Scenario: Upstream expansion at default depth

- **WHEN** a search targets `service-a` with upstream expansion at the default depth, given that `service-a` depends on `shared-lib` and `shared-lib` depends on `core-utils`
- **THEN** retrieval runs against `service-a` and `shared-lib`
- **AND** `core-utils` is not included

#### Scenario: Transitive upstream expansion

- **WHEN** a search targets `service-a` with upstream expansion at depth `2`, given that `service-a` depends on `shared-lib` and `shared-lib` depends on `core-utils`
- **THEN** retrieval runs against `service-a`, `shared-lib`, and `core-utils`

#### Scenario: Downstream expansion

- **WHEN** a search targets `shared-lib` with downstream expansion at depth `1`, given that `service-a` and `service-b` both depend on `shared-lib`
- **THEN** retrieval runs against `shared-lib`, `service-a`, and `service-b`

#### Scenario: Combined explicit repositories and group

- **WHEN** a search targets group `platform` (containing `service-a` and `service-b`) and additionally names `service-b` and `tools-cli`
- **THEN** retrieval runs against `service-a`, `service-b`, and `tools-cli`, each exactly once

#### Scenario: Unchanged default behaviour

- **WHEN** a search supplies no repositories, no groups, and no expansion options
- **THEN** retrieval runs against all registered repositories

#### Scenario: Unknown group rejected

- **WHEN** a search targets a group name that does not exist
- **THEN** the request is rejected with an error naming the group
- **AND** no retrieval is performed

#### Scenario: Empty resolved scope returns nothing

- **WHEN** a search targets group `platform`, which exists but has no members
- **THEN** the system returns no results
- **AND** does not fall back to searching all repositories

### Requirement: Relevance Treatment Of Expanded Repositories

Results originating from repositories in the expanded set SHALL be ranked below comparably-scoring results from the primary set, so that dependency expansion increases recall without displacing matches in the repositories the user named.

Each returned result SHALL indicate whether its repository was part of the primary set or was reached through expansion, and results reached through expansion SHALL also report the relation direction and hop distance that brought them in.

#### Scenario: Primary results outrank equally-scoring expanded results

- **WHEN** a search over `service-a` with upstream expansion reaches `shared-lib`, and a chunk in `service-a` and a chunk in `shared-lib` receive the same pre-adjustment relevance score
- **THEN** the `service-a` chunk is ranked above the `shared-lib` chunk

#### Scenario: Expansion provenance is reported

- **WHEN** a result from `shared-lib` is returned by a search over `service-a` with upstream expansion at depth `1`
- **THEN** the result identifies its repository as expanded, with direction `upstream` and hop distance `1`

#### Scenario: Strong expanded match still surfaces

- **WHEN** a search over `service-a` with upstream expansion reaches `shared-lib` and the only chunks matching the query live in `shared-lib`
- **THEN** those chunks are returned in the results
