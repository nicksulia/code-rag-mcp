## MODIFIED Requirements

### Requirement: Repository Relations In The Web UI

The Repo Hub view SHALL display each repository's group memberships and its declared dependencies, and SHALL provide controls to create and delete groups, manage group membership, and add and remove dependency edges.

The web UI SHALL provide a single, persistent scope control cluster (repository selection, group selection, dependency-expansion direction, and expansion depth) in the sidebar, shared across the RAG Studio (chat) view and the Hybrid Search view, rather than duplicating or tab-locally scoping these controls. Group selection SHALL support choosing more than one group at a time. The sidebar SHALL display a live, read-only summary of the currently resolved scope (named repositories, group membership, and expansion state) that updates whenever the selection changes. Both the RAG Studio and Hybrid Search views SHALL use this shared scope when issuing search and query requests, and SHALL visually mark results/citations that came from expanded repositories.

#### Scenario: Managing relations from Repo Hub

- **WHEN** the user assigns a repository to a group from the Repo Hub view
- **THEN** the repository's card shows the group
- **AND** the membership is reflected by the relations endpoint

#### Scenario: Scoping a query by group in RAG Studio

- **WHEN** the user selects the group `platform` and enables upstream expansion in the sidebar scope control before submitting a question in RAG Studio
- **THEN** the answer is grounded only in repositories from the resolved scope
- **AND** citations originating from expanded repositories are visually marked as such

#### Scenario: Scoping a query by group in Hybrid Search

- **WHEN** the user selects the group `platform` and enables upstream expansion in the sidebar scope control, then runs a query from the Hybrid Search view
- **THEN** the search results are limited to the resolved scope
- **AND** results originating from expanded repositories are visually marked as such

#### Scenario: Scope shared across views

- **WHEN** the user sets a scope (repositories, groups, expansion) in the sidebar and switches from the RAG Studio view to the Hybrid Search view without changing the scope controls
- **THEN** the Hybrid Search view uses the same resolved scope that was active in RAG Studio, without requiring the user to reselect it

#### Scenario: Selecting multiple groups

- **WHEN** the user selects both the `platform` and `frontend-apps` groups in the sidebar scope control
- **THEN** the resolved primary set includes the members of both groups
- **AND** the scope summary reflects the combined repository count

#### Scenario: Live scope summary

- **WHEN** the user changes any part of the sidebar scope control (repository checkboxes, group selection, expansion direction, or depth)
- **THEN** the displayed scope summary updates to reflect the new selection without requiring a query to be submitted
