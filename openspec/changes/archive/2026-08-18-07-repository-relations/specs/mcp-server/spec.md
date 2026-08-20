## ADDED Requirements

### Requirement: `manage_repository_relations` Tool

The MCP server SHALL expose a `manage_repository_relations` tool that lets an agent declare and remove repository relations.

Arguments:

- `action` (`string`, **required**): one of `create_group`, `delete_group`, `add_to_group`, `remove_from_group`, `add_dependency`, `remove_dependency`.
- `group` (`string`, *optional*): the group name, required for the group actions.
- `repos` (`string[]`, *optional*): repository identifiers or names to add to or remove from a group.
- `repo` (`string`, *optional*): the dependent repository, required for the dependency actions.
- `depends_on` (`string`, *optional*): the depended-upon repository, required for the dependency actions.

The tool SHALL resolve repository identifiers using the same alias resolution as the other repository tools. Integrity violations — unregistered repositories, self-dependencies, cycles, or duplicate group names — SHALL be returned as a structured tool error naming the offending group or repository, and SHALL leave stored relations unchanged.

#### Scenario: Creating a group through MCP

- **WHEN** the tool is called with `action: "create_group"`, `group: "platform"`, and `repos: ["service-a", "service-b"]`
- **THEN** the group is created with both repositories as members
- **AND** the tool returns the group and its members

#### Scenario: Declaring a dependency through MCP

- **WHEN** the tool is called with `action: "add_dependency"`, `repo: "service-a"`, and `depends_on: "shared-lib"`
- **THEN** the edge is stored
- **AND** the tool reports the created edge

#### Scenario: Cycle rejected through MCP

- **WHEN** the tool is called with a dependency that would introduce a cycle
- **THEN** the tool returns a structured error describing the cycle
- **AND** no edge is stored

### Requirement: `get_repository_relations` Tool

The MCP server SHALL expose a `get_repository_relations` tool that reports the relation graph so an agent can decide how to scope a subsequent search.

Arguments:

- `repo` (`string`, *optional*, default: `null` / whole graph): a repository identifier or name. When supplied, the tool returns that repository's group memberships, direct dependencies, and direct dependents. When omitted, the tool returns all groups with their members and all dependency edges.

#### Scenario: Inspecting one repository

- **WHEN** the tool is called with `repo: "service-a"`, which belongs to group `platform` and depends on `shared-lib`
- **THEN** the response lists `platform` under groups and `shared-lib` under dependencies

#### Scenario: Inspecting the whole graph

- **WHEN** the tool is called with no arguments
- **THEN** the response lists every group with its members and every dependency edge

#### Scenario: Repository with no relations

- **WHEN** the tool is called for a registered repository that has no relations
- **THEN** the response reports empty groups, dependencies, and dependents without an error

### Requirement: Relation Arguments On Search And RAG Tools

The `search_codebases` and `query_cross_repo_rag` tools SHALL accept optional relation arguments alongside their existing `repos` argument:

- `groups` (`string[]`, *optional*, default: `null`): group names whose members join the primary repository set.
- `expand` (`string`, *optional*, default: `"none"`): one of `none`, `upstream`, `downstream`, `both`.
- `expand_depth` (`integer`, *optional*, default: `1`): maximum traversal depth when expanding.

Tool responses SHALL report the resolved repository scope, distinguishing primary from expanded repositories, and results and citations drawn from expanded repositories SHALL be marked as such. When none of the relation arguments are supplied, both tools SHALL behave exactly as they did before this capability existed.

#### Scenario: Search scoped to a group

- **WHEN** `search_codebases` is called with `groups: ["platform"]` and no `repos`
- **THEN** results come only from the members of `platform`
- **AND** the response reports the resolved scope

#### Scenario: RAG query with downstream expansion

- **WHEN** `query_cross_repo_rag` is called with `repos: ["shared-lib"]`, `expand: "downstream"`, and `expand_depth: 1`
- **THEN** the answer may cite the repositories that depend on `shared-lib`
- **AND** those citations are marked as coming from expanded repositories

#### Scenario: Unknown group rejected

- **WHEN** `search_codebases` is called with a group name that does not exist
- **THEN** the tool returns a structured error naming the group and performs no retrieval

#### Scenario: Existing calls unaffected

- **WHEN** `search_codebases` is called with only `query`, `repos`, and `limit`
- **THEN** it returns the same results it returned before relation arguments existed
