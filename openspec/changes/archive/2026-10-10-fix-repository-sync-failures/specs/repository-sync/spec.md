# Spec Delta

## Purpose

Make repository synchronization report actual Git and indexing outcomes, preserve existing indexes when remote preparation fails, and support explicitly requested indexing of an existing checkout without contacting its remote.

## ADDED Requirements

### Requirement: Required Git operations determine synchronization success

Default synchronization of a Git repository SHALL succeed only after every required clone, fetch, checkout, and pull operation succeeds and indexing completes. A branch or remote update SHALL check every Git operation it performs, including remote configuration, branch fetching, and pulling. The first failed required operation SHALL stop dependent operations and indexing. A failure SHALL NOT automatically fall back to the existing checkout or another authentication identity.

Normal branch resolution SHALL retain support for creating a checked tracking branch when the requested branch is known to be absent locally after a successful fetch. This resolution SHALL NOT treat an unrelated checkout failure as permission to continue; failure to establish the requested checkout SHALL abort the operation.

#### Scenario: Fetch failure on an existing checkout
- **WHEN** default synchronization encounters a failed fetch
- **THEN** synchronization fails explicitly
- **AND** checkout, pull, and indexing are not attempted

#### Scenario: Checkout or pull failure
- **WHEN** fetch succeeds but a required checkout or pull fails
- **THEN** synchronization fails explicitly rather than indexing the current working tree

#### Scenario: Branch update fails during preparation
- **WHEN** changing a repository branch encounters a failed branch fetch
- **THEN** the update reports a failure and does not continue to checkout, pull, or automatic indexing

#### Scenario: Requested branch is available only remotely
- **WHEN** a branch fetch succeeds and the requested branch has no local tracking branch
- **THEN** the update can establish and check the tracking branch before continuing
- **AND** an unrelated checkout error does not trigger that resolution or permit indexing

#### Scenario: Initial clone fails
- **WHEN** a newly registered Git repository cannot be cloned
- **THEN** its operation reports failure rather than successful registration and indexing
- **AND** its saved-configuration outcome is reported separately

### Requirement: Git operations are bounded and noninteractive

Repository Git operations SHALL have finite execution deadlines: 120 seconds per clone, fetch, or pull, and 15 seconds per remote configuration, checkout, or local commit lookup. A timeout SHALL fail the operation explicitly. Server-managed operations SHALL NOT require interactive credentials, passphrases, or host-key confirmation, and SHALL NOT automatically accept unknown host keys or disable host-key verification.

In the absence of managed key assignment, Git authentication SHALL retain the running process's inherited SSH agent access and user Git/SSH configuration. Operation-specific execution settings SHALL NOT mutate the parent process environment or the user's configuration files.

#### Scenario: Remote operation exceeds its deadline
- **WHEN** a fetch has not completed within 120 seconds
- **THEN** it is terminated and synchronization reports a timeout
- **AND** indexing does not begin

#### Scenario: Agent-provided authentication
- **WHEN** the server inherits a working SSH agent containing the necessary unlocked key
- **THEN** ordinary SSH Git operations can authenticate through that agent without uploading a key or prompting the browser

#### Scenario: Interactive authentication would be needed
- **WHEN** the configured authentication cannot complete without interactive input
- **THEN** the operation fails or reaches its finite deadline without requesting browser or terminal input
- **AND** the error directs the user to configure authentication outside the sync operation

### Requirement: Remote-preparation failures preserve indexed state

If a Git operation fails before indexing begins, the system SHALL retain the repository's existing file hashes, vectors, lexical entries, symbol graph, provenance, indexed counts, recorded indexed commit, and last-successful-sync timestamp. It SHALL persist an error status and a safe error message for that repository. Previously indexed content SHALL remain searchable. A first-time failure SHALL leave a registered repository without indexed content.

This guarantee SHALL NOT imply rollback of Git working-tree or remote-configuration changes already performed, or atomic rollback of failures after indexing has begun.

#### Scenario: Previously indexed repository cannot pull
- **WHEN** a previously indexed Git repository fails remote preparation
- **THEN** its prior index and last-successful-sync timestamp remain unchanged after reopening the catalog
- **AND** the repository has an error status with a safe message
- **AND** searches can still use its prior indexed content

#### Scenario: Later retry succeeds
- **WHEN** a subsequent default synchronization completes successfully
- **THEN** the repository returns to its existing ready status, clears its error, and advances its successful-sync timestamp

### Requirement: Cached-checkout indexing is an explicit mode

The system SHALL accept an optional boolean `local_only`, defaulting to `false`, for repository synchronization. For a Git repository, `local_only: true` SHALL index only an existing usable checkout without cloning, fetching, pulling, checking out a configured branch, or modifying its remote. It SHALL fail before index mutation if no usable checkout exists.

Successful local-only Git indexing SHALL return `status: "indexed_local"` with the existing indexing metrics, rather than `status: "synced"`. It SHALL retain the last-successful-remote-sync timestamp, update indexed commit/counts/provenance to represent the actual indexed checkout, and explicitly state that no remote update was performed. An indexing failure SHALL still be reported as a failure.

Local-directory repositories SHALL retain their existing indexing behavior and successful response shape with either value of `local_only`. The existing boolean `force` SHALL remain independent of remote access: `force: true` without `local_only: true` SHALL still require remote preparation.

#### Scenario: Explicit offline indexing succeeds
- **WHEN** a Git repository with an existing usable checkout is indexed with `local_only: true`
- **THEN** no remote Git operation or branch switch occurs
- **AND** the response reports `indexed_local` with indexing metrics
- **AND** its last-successful-remote-sync timestamp remains unchanged

#### Scenario: No checkout is available
- **WHEN** local-only indexing is requested for a Git repository with no usable checkout
- **THEN** the operation fails explicitly without cloning or modifying any existing index

#### Scenario: Force does not enable an implicit fallback
- **WHEN** a force sync with the default remote mode encounters an authentication failure
- **THEN** it fails rather than treating force as permission to index the cached checkout

### Requirement: Errors are actionable and credential-safe

Repository-operation failures SHALL expose a bounded safe message, a stable error code, and the failed operation phase when known. Git authentication failures, host-key verification failures, timeouts, connection failures, unavailable Git, and otherwise unclassified Git-command failures SHALL have distinct supported codes where evidence identifies the cause. Unclassified errors SHALL remain explicit failures rather than inventing a more specific cause.

Raw Git arguments, exception representations, stdout/stderr, credential-bearing addresses, authentication tokens, and private-key contents SHALL NOT be included in public errors, persisted repository errors, or application logs. Public messages SHALL be at most 512 characters. Logs SHALL identify the repository, phase, supported code, and exit outcome without raw process output.

#### Scenario: Git output contains sensitive data
- **WHEN** a failed Git command's arguments or output contain a credential-bearing URL or token
- **THEN** repository error fields in listings, operation error responses, CLI diagnostics, MCP errors, and application logs omit those values
- **AND** the user still receives a safe failure code and remediation message

#### Scenario: Unknown Git diagnostic
- **WHEN** a Git failure does not match a supported diagnostic category
- **THEN** it reports a generic Git-command failure with the phase and safe guidance
- **AND** it does not report success or expose the raw diagnostic

### Requirement: Configuration persistence is distinct from operation success

Add and edit operations that save repository configuration before a Git or indexing failure SHALL report that the configuration was saved but the requested operation failed. Known repository-operation errors SHALL include `repo_id` and a boolean `config_saved` reflecting actual persistence. A save failure SHALL NOT be represented as `config_saved: true`. Standalone sync failures SHALL omit `config_saved` because they are not configuration-save operations.

Requested configuration already persisted on a failed update SHALL remain available for correction or retry; successful configuration persistence SHALL NOT imply that the requested branch or remote update was applied successfully. Successful add/edit response shapes SHALL remain unchanged.

#### Scenario: Add saves configuration but cannot clone
- **WHEN** registration succeeds but its automatic clone fails
- **THEN** the response reports a failed operation with `config_saved: true` and the repository ID
- **AND** the user can find and edit the registered repository rather than being instructed to recreate it

#### Scenario: Edit saves configuration but cannot sync
- **WHEN** edited configuration is persisted and its Git update or automatic indexing subsequently fails
- **THEN** the response identifies saved configuration and failed execution separately
- **AND** the prior index remains intact for failures before indexing

### Requirement: Sync HTTP requests and errors have explicit contracts

`POST /api/v1/repos/{repo_id}/sync` SHALL accept an empty body or a JSON object containing only optional boolean `force` and `local_only`, both defaulting to `false`. It SHALL reject malformed JSON, duplicate JSON keys, non-object bodies, unknown fields, and nonboolean option values with HTTP `400` before invoking synchronization.

The endpoint SHALL accept request bodies up to 4,096 bytes and reject larger bodies with HTTP `413` before reading the body or invoking synchronization.

Successful operations SHALL return HTTP `200` with the existing metrics and the appropriate `synced` or `indexed_local` status. Unknown repositories SHALL return `404`, invalid options or unavailable cached checkouts SHALL return `400`, Git timeouts SHALL return `504`, unavailable Git executables SHALL return `503`, and other Git-operation failures SHALL return `500`. Repository-operation error bodies SHALL retain the existing `error` and numeric `status` fields and add supported `code`, `phase`, and `repo_id` fields, plus `config_saved` only for known add/edit persistence outcomes. Unexpected errors SHALL be logged safely and return a generic failure without raw exception text.

#### Scenario: Existing empty-body sync request
- **WHEN** a client posts an empty body to the sync endpoint
- **THEN** it performs default remote synchronization with both options set to false

#### Scenario: Invalid option prevents work
- **WHEN** a client supplies `"local_only": "true"`, duplicate option keys, malformed JSON, or an unknown field
- **THEN** HTTP `400` reports the invalid request
- **AND** no repository operation is invoked

#### Scenario: Git timeout is not HTTP success
- **WHEN** a required Git command exceeds its deadline
- **THEN** HTTP `504` returns a safe timeout error rather than indexing metrics or a successful status

#### Scenario: Sync request body limit is enforced
- **WHEN** a sync request body is 4,097 bytes
- **THEN** HTTP `413` rejects it before the body is read or synchronization is invoked
- **AND** an otherwise valid body of exactly 4,096 bytes remains within the accepted limit

### Requirement: Browser sync results reflect server outcomes

Single-repository Sync SHALL check HTTP success and the operation result, visibly report a safe error on failure, and refresh repository status even after a server-reported failure. The repository hub SHALL display persisted repository errors using text-safe rendering. For Git repositories, any displayed successful-sync timestamp SHALL be labeled as remote sync, not inferred from local-only indexing.

The hub SHALL offer a separate **Index existing checkout** action for Git repositories that explains no remote update occurs. It SHALL send `local_only: true` only for that explicitly selected action and display a local-index outcome rather than a remote-sync success.

All affected controls SHALL be restored after request, parse, or refresh failure. Post-operation refresh errors SHALL be shown separately from the known operation outcome. Unconfirmed network outcomes SHALL NOT be presented as known success, known configuration rollback, or advice to blindly repeat registration. Refresh SHALL preserve valid search scope and theme.

#### Scenario: Single sync returns an HTTP error
- **WHEN** Sync receives a non-success HTTP response
- **THEN** the UI displays its safe failure rather than a completion message
- **AND** it attempts to refresh the repository's error status and restores controls

#### Scenario: Local indexing is explicitly requested
- **WHEN** the user chooses Index existing checkout for a Git repository
- **THEN** the request includes `local_only: true`
- **AND** the result says the checkout was indexed without remote synchronization

#### Scenario: Refresh fails after a known success
- **WHEN** indexing succeeds but refreshing the repository list fails
- **THEN** the UI reports successful indexing and a separate refresh failure
- **AND** it does not recast the completed operation as failed or reset scope/theme

#### Scenario: Save succeeds but automatic sync fails
- **WHEN** Add or Edit receives an operation error with `config_saved: true`
- **THEN** its modal explains that configuration was saved and execution failed
- **AND** it refreshes the saved repository and allows correction or retry

### Requirement: Bulk synchronization reports every attempted outcome

Browser Sync All SHALL attempt default synchronization for each repository in a snapshot of the registered list, continue after individual failures, and report per-repository results and aggregate counts. Server-confirmed failures and unconfirmed network/invalid-response outcomes SHALL be distinguished. It SHALL NOT describe a partial failure as all repositories synchronized. Refresh failures SHALL not erase completed per-repository results or prevent controls from being restored.

#### Scenario: One repository fails in a bulk run
- **WHEN** two repositories synchronize successfully and one returns a server error
- **THEN** all three are attempted and the UI reports two successes and one failure with repository identifiers

#### Scenario: Bulk request loses its connection
- **WHEN** one bulk request ends without a valid response and later requests succeed
- **THEN** the UI distinguishes the unconfirmed result from server-confirmed successes and failures
- **AND** it attempts the remaining repositories and restores bulk controls

### Requirement: CLI outcomes support reliable automation

CLI sync SHALL expose `--local-only`, retain `--force` semantics, and distinguish local-checkout indexing from remote synchronization in its output. It SHALL attempt remaining selected repositories after an individual failure, print an accurate aggregate result, and exit nonzero if any selected operation fails or a requested repository does not exist. Fully successful operations SHALL exit zero.

CLI add/update SHALL show known saved-configuration context on subsequent Git/indexing failure and exit nonzero rather than print a successful operation. Git-derived diagnostics and printed source addresses in these affected operation paths SHALL not disclose credential-bearing source values.

#### Scenario: Partial CLI bulk failure
- **WHEN** CLI sync selects three repositories and the second fails
- **THEN** the third is still attempted
- **AND** output identifies the failure and aggregate counts and the process exits nonzero

#### Scenario: CLI cached indexing
- **WHEN** CLI sync is invoked with `--local-only` for an existing Git checkout
- **THEN** it indexes without remote operations and reports local indexing rather than Sync Complete

### Requirement: MCP synchronization shares the service contract

The MCP `sync_repository` tool SHALL accept optional boolean `local_only` in addition to its existing arguments, defaulting to false. Both MCP transports SHALL validate the sync option types, share the same successful payloads and safe repository-operation error data, and return `isError: true` for failed sync/update tool calls using existing tool-error framing. A tool error SHALL NOT be replaced by an ordinary success-shaped payload.

#### Scenario: Default MCP sync fails remotely
- **WHEN** an MCP client requests default sync and required remote preparation fails
- **THEN** the tool result carries `isError: true` and safe failure information
- **AND** it does not report successful cached indexing

#### Scenario: MCP requests local-only indexing
- **WHEN** either transport calls `sync_repository` with boolean `local_only: true`
- **THEN** it returns the same `indexed_local` result and metrics without remote operations

#### Scenario: MCP provides an invalid option type
- **WHEN** an MCP client supplies a string instead of boolean `local_only` or `force`
- **THEN** the tool returns a validation error before invoking synchronization
