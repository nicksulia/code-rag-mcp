# Spec Delta

## Purpose

Let users back up and share registered repository configurations, groups, and declared dependencies through versioned JSON files, then import additions safely from the web UI without transferring code or indexes.

## ADDED Requirements

### Requirement: Versioned project configuration document

The system SHALL use a JSON object with exactly the top-level fields `version`, `repositories`, `groups`, and `dependencies`. Version 1 SHALL use integer `version: 1` and the following arrays:

- `repositories`: objects containing exactly `repo_id`, `name`, `source_type`, `url_or_path`, and `branch`. The first four values SHALL be nonempty strings; `source_type` SHALL be `local` or `git`, and `branch` SHALL be a nonempty string or `null`.
- `groups`: objects containing exactly a nonempty string `name` and a `members` array of repository IDs. Empty groups SHALL be supported.
- `dependencies`: objects containing exactly `repo_id` and `depends_on_repo_id`, each a repository ID.

Repository IDs SHALL match `[A-Za-z0-9][A-Za-z0-9._-]*`. String fields SHALL be trimmed before validation and comparison, except that a nonempty local path SHALL retain its path spelling. Local paths SHALL NOT be resolved against or checked for existence on the destination machine during import. Configuration SHALL NOT include source contents, index data, commit hashes, sync status, counts, timestamps, error messages, browser search scope, or theme.

#### Scenario: Configuration contains local and Git repositories

- **WHEN** a version 1 configuration contains a local repository with an absolute path, a Git repository with a branch, an empty group, and a dependency
- **THEN** all configuration fields are represented using the documented shapes
- **AND** no runtime or indexed content is represented

#### Scenario: Empty configuration

- **WHEN** a version 1 configuration has empty `repositories`, `groups`, and `dependencies` arrays
- **THEN** it is valid and importing it changes nothing

### Requirement: Complete and deterministic configuration export

The system SHALL export every registered repository, every group and its memberships, and every declared dependency from one consistent catalog snapshot. Repository and dependency arrays SHALL be ordered by repository ID and dependency endpoint IDs respectively; groups and members SHALL be ordered by name and ID respectively. Repeated exports of unchanged configuration SHALL produce identical JSON bytes, even if runtime statistics change. The downloaded file SHALL be UTF-8 JSON named `projects.json`.

The system SHALL reject export of a catalog that cannot be represented by the version 1 contract or exceeds the 1,048,576-byte file limit, identifying the relevant field or limit without silently omitting entries.

#### Scenario: Export preserves relations and ignores runtime changes

- **WHEN** the user exports a catalog containing grouped repositories and declared dependencies, then exports again after only sync statistics change
- **THEN** both documents contain the same complete configuration
- **AND** their JSON bytes are identical

#### Scenario: Existing metadata cannot be exported

- **WHEN** a registered repository has an ID outside the version 1 ID format
- **THEN** export fails with an error naming that repository's invalid field
- **AND** no incomplete download is produced

### Requirement: Credential-free transfer

The system SHALL support Git addresses using HTTP(S), SSH, `git://`, or ordinary scp-style syntax and SHALL reject other forms, including remote-helper syntax. It SHALL reject transfer of HTTP(S) Git URLs containing user information, query strings, or fragments, and SSH URLs containing passwords. It SHALL accept ordinary credential-free HTTPS URLs, SSH URLs with usernames, and scp-style Git addresses such as `git@example.com:org/repo.git`. Credentials SHALL be supplied through the destination machine's Git authentication configuration, not the transfer file.

On export, a credential-bearing or unsupported Git address SHALL block the download rather than be silently altered. On import, it SHALL block confirmation. Error responses and UI messages SHALL name the offending field or repository without echoing the address or credential value.

#### Scenario: Stored credentials block export

- **WHEN** a registered Git repository has an HTTPS address containing URL user information
- **THEN** export fails without returning that address or its user information
- **AND** the UI explains that the repository address must be made credential-free

#### Scenario: Normal SSH address is transferable

- **WHEN** a configuration contains `git@example.com:org/repo.git`
- **THEN** the address is accepted and preserved without treating `git` as a secret

### Requirement: Strict import validation

The system SHALL reject malformed JSON, duplicate JSON object keys, unsupported versions, missing or unknown fields, wrong field types, empty required strings, unsafe repository IDs, duplicate repository IDs, duplicate group names, repeated group members, and repeated dependency edges. Booleans SHALL NOT be accepted as an integer version. The browser SHALL reject files larger than 1,048,576 bytes before reading or uploading them, and the import endpoint SHALL reject HTTP request bodies larger than 2,097,152 bytes before reading the body.

Each group member and dependency endpoint SHALL refer to a repository declared in the document or already present in the destination catalog. The resulting dependency graph, including preexisting edges, SHALL contain no self-dependency or cycle. Validation SHALL apply to the entire document before any catalog mutation. Errors SHALL identify the relevant field, repository, group, or edge without exposing credentials.

#### Scenario: Invalid document does not partially import

- **WHEN** an import contains valid new repositories followed by a group referring to an unknown repository
- **THEN** it is rejected with an error identifying the unknown reference
- **AND** none of its repositories, groups, or edges are persisted

#### Scenario: Unsupported or incorrectly typed version

- **WHEN** the version is `2`, `"1"`, or `true`
- **THEN** import is rejected with a version error
- **AND** the catalog is unchanged

#### Scenario: Duplicate definitions are rejected

- **WHEN** a document repeats a repository ID or JSON object key
- **THEN** import is rejected rather than choosing one definition

#### Scenario: Existing and incoming edges create a cycle

- **WHEN** an imported dependency closes a cycle through an existing catalog dependency
- **THEN** import is rejected with an error identifying the offending edge
- **AND** existing and incoming relations are unchanged

#### Scenario: Size limits are enforced

- **WHEN** a selected file is larger than 1,048,576 bytes or a direct import request body is larger than 2,097,152 bytes
- **THEN** the respective browser or HTTP boundary rejects it with an explicit size-limit error
- **AND** no import is attempted

### Requirement: Non-destructive and idempotent merge

The system SHALL add repositories whose IDs do not exist and treat an existing repository with identical configuration as unchanged. An existing ID with different `name`, `source_type`, `url_or_path`, or `branch` SHALL be a conflict that blocks the entire import. Repository comparison SHALL use only normalized configuration fields, not runtime or indexed state.

New groups SHALL be created, and existing groups SHALL receive the union of existing and imported members. Existing members and dependencies SHALL never be removed by import. Already-present memberships and dependency edges SHALL be no-ops. Unrelated repositories, groups, indexes, and runtime metadata SHALL remain untouched.

#### Scenario: Import adds configuration without replacing existing data

- **WHEN** a configuration includes one new repository, one identical existing repository, an additional member for an existing group, and one new dependency
- **THEN** only the new repository, membership, and dependency are added
- **AND** existing repository statistics, indexes, group members, and unrelated data remain intact

#### Scenario: Conflicting repository blocks all changes

- **WHEN** a document includes a new repository and an existing repository ID with a different branch
- **THEN** the entire import is rejected as a conflict
- **AND** neither repository nor any relation is changed

#### Scenario: Reimport is a no-op

- **WHEN** the same configuration is imported successfully a second time
- **THEN** no duplicate repositories, groups, memberships, or edges are created
- **AND** the result reports zero additions

### Requirement: Preview and atomic confirmation

Before confirmation, the system SHALL provide a read-only preview identifying repository additions and unchanged entries, group additions, membership additions, dependency additions, repository conflicts, and local-path portability warnings. The preview SHALL provide counts and entry identifiers and SHALL state whether import can proceed.

Confirmation SHALL revalidate against the current catalog and persist all additions as one atomic operation. A newly introduced conflict, invalid relation, or persistence failure SHALL leave the catalog unchanged and produce an explicit error. A preview SHALL NOT reserve entries or cause cloning, indexing, or embedding calls.

#### Scenario: Preview and cancellation leave state untouched

- **WHEN** the user selects a valid file, views its preview, and cancels
- **THEN** the catalog and indexes are unchanged
- **AND** no repository is cloned or indexed

#### Scenario: Catalog changes after preview

- **WHEN** another operation registers a conflicting repository after preview but before confirmation
- **THEN** confirmation rejects the conflict against the current catalog
- **AND** none of the proposed additions are persisted

#### Scenario: Persistence fails partway through confirmation

- **WHEN** persistence fails after inserting a repository but before inserting all relations
- **THEN** every addition from that confirmation is rolled back
- **AND** the user receives a failure rather than a success summary

### Requirement: JSON transfer HTTP endpoints

The system SHALL expose `GET /api/v1/projects/export` returning the version 1 document with HTTP `200`, JSON content type, and attachment filename `projects.json`. It SHALL expose `POST /api/v1/projects/import` accepting exactly `{"document": <configuration>, "dry_run": <boolean>}`, with `dry_run` defaulting to `true` when omitted.

Valid previews SHALL return `200` with `dry_run: true`, `can_import`, per-category addition counts and identifiers, unchanged repository IDs, conflicts, and warnings. A valid preview with repository conflicts SHALL return `can_import: false` without mutation. Successful confirmations SHALL return `200` with `dry_run: false`, `success: true`, and the actual addition/unchanged summary. Invalid requests or configuration SHALL return `400`, conflicting confirmations SHALL return `409`, oversized request bodies SHALL return `413`, and unexpected persistence failures SHALL return `500`. Error responses SHALL include an explicit `error` message and the HTTP `status`; no failure SHALL be represented as successful import.

These endpoints SHALL follow the existing REST deployment model. This change SHALL NOT add CLI commands, MCP tools, or alter existing endpoint behavior.

#### Scenario: Missing dry-run flag is safe

- **WHEN** a client posts a valid `document` without `dry_run`
- **THEN** the response is a read-only preview with `dry_run: true`
- **AND** the catalog is unchanged

#### Scenario: Conflicts are visible in a preview

- **WHEN** a client previews a valid configuration containing a conflicting existing repository ID
- **THEN** HTTP `200` reports `can_import: false` and the conflicting ID and field names
- **AND** no configuration values containing credentials are echoed

#### Scenario: Malformed JSON is not treated as an empty import

- **WHEN** a client sends malformed JSON to the import endpoint
- **THEN** HTTP `400` includes an explicit parse error
- **AND** no preview or successful import result is returned

### Requirement: Browser transfer workflow and safe rendering

The Repositories hub SHALL provide **Export JSON** and **Import JSON** controls, including when the catalog is empty. Import SHALL select a local JSON file, display the server-validated preview, and require an explicit **Import** confirmation. Confirmation SHALL be disabled for invalid or conflicting documents and during an in-flight operation. Canceling, replacing the file, or reopening the workflow SHALL discard any prior confirmation state.

Export and import failures SHALL be visibly reported. After successful import, the UI SHALL refresh repositories, groups, relations, statistics, and the scope summary while preserving existing valid search selections and theme. Imported strings SHALL be displayed as text throughout preview, repository cards, filters, group management, and dependency controls; they SHALL NOT create markup or executable event handlers.

#### Scenario: Export downloads JSON

- **WHEN** the user clicks **Export JSON** on the Repositories hub
- **THEN** the browser downloads `projects.json`
- **AND** the UI does not treat an HTTP error body as an exported configuration

#### Scenario: Import refreshes the hub without resetting preferences

- **WHEN** the user confirms a valid preview and import succeeds
- **THEN** new repositories and relations appear in the hub and filters
- **AND** the existing theme and valid search selections remain unchanged

#### Scenario: Untrusted display metadata is inert

- **WHEN** a repository display name or group name contains quotes or HTML-like text
- **THEN** the preview, cards, filters, and group/dependency controls display it literally
- **AND** no script executes and controls still address the intended repository or group

#### Scenario: Network or server failure is visible

- **WHEN** preview, export, confirmation, or post-import refresh fails
- **THEN** the UI displays the relevant failure and restores usable controls
- **AND** a refresh failure after persistence is distinguished from an import failure

### Requirement: Import does not synchronize source code

New imported repositories SHALL be registered with zero indexed counts, no commit hash, and no last-sync timestamp, using the existing registered repository status convention. Import SHALL NOT access repository files, clone Git repositories, switch branches, synchronize indexes, or load an embedding model. Local repository entries SHALL receive a warning that their paths are machine-specific and must be reviewed before synchronization; missing paths SHALL NOT block configuration import.

The success UI SHALL explain that importing configuration does not index repositories and direct users to the existing edit and sync controls. Existing repository edits and synchronization SHALL keep their current behavior.

#### Scenario: Offline configuration import

- **WHEN** a valid configuration references unavailable Git servers and nonexistent local paths
- **THEN** it can be previewed and imported without contacting those servers or reading those paths
- **AND** the UI warns about local paths and explains that indexing is a separate action

#### Scenario: Existing indexed repository remains indexed

- **WHEN** an identical existing indexed repository is included in an import
- **THEN** its index, statistics, commit hash, and last-sync timestamp remain unchanged
