# Spec Delta

## MODIFIED Requirements

### Requirement: Origin validation against DNS rebinding

For every `/mcp` request carrying an `Origin` header, the server SHALL validate the origin against its deployment allowlist and SHALL reject disallowed origins with `403`. On loopback listeners, the request SHALL additionally satisfy the local-credential HTTP boundary: a valid local Host with the actual listener port, exact same-origin scheme/hostname/port when Origin is present, and no hostile browser fetch metadata. Valid local non-browser requests without Origin SHALL remain accepted subject to other route guards.

On non-loopback listeners, the existing hostname allowlist covering the configured concrete bind address and localhost variants SHALL remain in effect for permitted capabilities. The local-only SSH credential restriction SHALL still apply independently. Origin validation SHALL apply regardless of whether bearer authentication is enabled.

#### Scenario: Disallowed browser origin rejected
- **WHEN** a request to `/mcp` arrives with `Origin: https://attacker.example`
- **THEN** the response is `403` and the message is not processed

#### Scenario: Localhost origin accepted
- **WHEN** a request to the loopback listener on port 8000 carries `Host: localhost:8000` and `Origin: http://localhost:8000`
- **THEN** it passes Host/Origin validation and is processed subject to the other route guards

#### Scenario: Non-browser client without Origin accepted
- **WHEN** a local request to `/mcp` carries a valid Host and no Origin or hostile fetch metadata
- **THEN** it passes provenance validation and is processed subject to the other route guards

#### Scenario: Cross-port localhost origin is rejected
- **WHEN** the listener on port 8000 receives `Origin: http://localhost:9000`
- **THEN** the request is rejected with `403` before tool execution

### Requirement: stdio transport unchanged

The existing stdio transport SHALL retain its line-delimited JSON-RPC framing, methods, stderr-only diagnostic logging, and behavior of unrelated tools. Protocol-version negotiation SHALL continue to follow the protocol-version-negotiation requirement.

Repository synchronization and updates SHALL retain the corrected shared repository-operation success/failure contract introduced by the prerequisite sync-reliability change. The `sync_repository` tool SHALL retain optional boolean `local_only`, defaulting to false. The `update_repository` tool SHALL add optional nullable `ssh_key_id`, with omission preserving the assignment and null explicitly clearing it, and repository-list payloads SHALL add nullable `ssh_key_id` without private-key paths or material.

Both transports SHALL use consistent assignment, identity selection, and safe operation-result semantics, subject to the HTTP-specific credential boundary. Successful existing sync/update calls without new options SHALL retain their existing fields; unrelated tool results SHALL not change. These additions SHALL not change JSON-RPC framing, protocol negotiation, configured bearer authentication, or private-file not-found protections.

#### Scenario: stdio initialize still works
- **WHEN** a client sends `initialize` requesting `2024-11-05`, followed by `tools/list`
- **THEN** the responses retain their existing framing and shape
- **AND** the tool catalog retains `local_only` and advertises the additive nullable update assignment

#### Scenario: Corrected sync errors match both transports
- **WHEN** equivalent permitted sync calls fail a required Git operation over stdio and loopback HTTP
- **THEN** both return `isError: true` with equivalent safe operation information
- **AND** neither silently retries cached content or a different SSH identity

#### Scenario: Existing successful tool calls remain compatible
- **WHEN** a client makes a previously supported successful sync/update call without new options or invokes an unrelated tool
- **THEN** existing successful fields and unrelated-tool payloads retain their semantics

#### Scenario: Assignment metadata contains no private path
- **WHEN** either transport lists repositories
- **THEN** it includes only additive nullable key IDs for associations
- **AND** it does not return profile private paths, private-key contents, or passphrases
