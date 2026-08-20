# Specification: Repository Relations

## Status: ACTIVE
## Domain: Repository Management & Retrieval

---

## 1. Overview
Defines how repositories are related to one another — as named groups and as directed dependency edges — and how those relations are used to resolve the set of repositories a search or RAG query runs against.

---

## 2. Requirements

### 2.1 Unified Relation Model
- The system maintains repository relations under a single model with exactly two relation kinds: `group` (undirected membership of a repository in a named collection) and `depends_on` (a directed edge from a dependent repository to a repository it consumes).
- A repository may belong to any number of groups and participate in any number of dependency edges.
- Relations are persisted alongside repository metadata and survive process restarts.
- A catalog created before this capability existed is usable without a manual migration step; relation storage is initialised on demand, and an absent relation store is treated as "no relations declared" rather than an error.

### 2.2 Manual Relation Declaration
- Relations are declared, inspected, and removed explicitly by the user. Dependency edges are never inferred automatically from repository contents, manifests, or import statements.
- Supported operations:
  - create a group with a unique name, and delete a group
  - add a repository to a group, and remove a repository from a group
  - add a `depends_on` edge between two repositories, and remove such an edge
  - list all groups, list the members of a group, and list the relations of a single repository
- Deleting a group does not delete its member repositories; they remain registered and indexed.
- Deleting a repository removes it from every group and removes every dependency edge that references it, in the same operation as the repository's deletion.

### 2.3 Relation Integrity
- The system rejects relation declarations that would leave the relation graph inconsistent, reporting a clear error naming the offending repository, group, or edge:
  - a group membership or dependency edge referencing a repository that is not registered
  - a self-referential dependency edge (a repository depending on itself)
  - a dependency edge that would introduce a cycle in the `depends_on` graph (detected via graph traversal before the edge is written; pre-existing edges are left untouched on rejection)
  - creation of a group whose name is already in use
- Declaring a relation that already exists is idempotent: it succeeds without creating a duplicate.

### 2.4 Relation-Aware Scope Resolution
- Search and retrieval callers can describe their target repositories using group names and dependency expansion in addition to explicit repository identifiers. The system resolves such a request into a concrete set of registered repository identifiers before retrieval runs.
- Resolution behaviour:
  - Explicitly named repositories and the members of every named group form the **primary set**.
  - Dependency expansion is opt-in and supports a direction of `upstream` (repositories the primary set depends on), `downstream` (repositories that depend on the primary set), or `both`.
  - Expansion traverses the `depends_on` graph transitively up to a caller-supplied maximum depth, defaulting to `1`. A depth of `0` disables expansion. Each expanded repository records the shortest hop distance that reached it.
  - Repositories reached only through expansion form the **expanded set**; a repository in the primary set is never also counted as expanded.
  - The resolved set contains no duplicates.
  - When no repositories, groups, or expansion options are supplied, the system searches all registered repositories, preserving prior behaviour.
  - A request naming a group that does not exist is rejected with an error naming the group.
  - A request whose resolved set is empty (e.g., an existing but empty group) returns no results rather than silently searching all repositories.

### 2.5 Relevance Treatment Of Expanded Repositories
- Results originating from repositories in the expanded set are ranked below comparably-scoring results from the primary set (via a multiplicative hop-decay penalty applied after fusion/boost scoring), so that dependency expansion increases recall without displacing matches in the repositories the user named.
- Each returned result indicates whether its repository was part of the primary set or was reached through expansion; expanded results also report the relation direction and hop distance that brought them in. The same provenance marker propagates onto citations.
