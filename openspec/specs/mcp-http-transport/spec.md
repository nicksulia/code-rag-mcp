# mcp-http-transport Specification

## Purpose

Exposes the MCP JSON-RPC tool surface over HTTP from the existing `serve` process, so clients that cannot spawn stdio processes (web IDEs, hosted agents, HTTP-only tooling) can use the code-intelligence tools, while keeping stdio behavior and tool semantics unchanged.

## Requirements

### Requirement: JSON-RPC message handling over HTTP POST

The system SHALL expose an `/mcp` endpoint on the `serve` HTTP server that accepts `POST` requests with an `application/json` body containing exactly one JSON-RPC 2.0 message object. When the message is a request (carries an `id`), the endpoint SHALL respond `200` with `Content-Type: application/json` and the JSON-RPC response as the body. The set of methods, tools, arguments, and result payloads available over HTTP SHALL be identical to those available over the stdio transport. A body that is not valid JSON SHALL be rejected with a JSON-RPC parse error (`code: -32700`).

#### Scenario: Tool call over HTTP matches stdio behavior

- **WHEN** a client POSTs a `tools/call` request for `list_repositories` to `/mcp`
- **THEN** the response is `200 application/json` with a JSON-RPC result whose content is identical in shape and data to the same call over stdio

#### Scenario: Initialize over HTTP

- **WHEN** a client POSTs an `initialize` request to `/mcp`
- **THEN** the response is `200 application/json` containing `serverInfo` and a `capabilities` object with tool support

#### Scenario: Malformed JSON body

- **WHEN** a client POSTs a body that is not valid JSON to `/mcp`
- **THEN** the response is a JSON-RPC error object with code `-32700`

### Requirement: Notifications and responses receive no body

When a POST to `/mcp` contains only JSON-RPC notifications or responses (messages without an `id` expecting a reply), the endpoint SHALL respond `202 Accepted` with an empty body.

#### Scenario: Initialized notification

- **WHEN** a client POSTs a `notifications/initialized` message to `/mcp`
- **THEN** the response is `202 Accepted` with an empty body

### Requirement: Batch requests are rejected

A POST to `/mcp` whose body is a JSON array (JSON-RPC batch) SHALL be rejected with HTTP `400`. Batching is not part of the Streamable HTTP transport as of protocol version `2025-03-26`.

#### Scenario: Batch array rejected

- **WHEN** a client POSTs a JSON array of two or more JSON-RPC messages to `/mcp`
- **THEN** the response is `400` and no tool is executed

### Requirement: Stateless operation

The HTTP transport SHALL process every request independently and SHALL NOT require a session identifier. Clients SHALL be able to call tools without a preceding `initialize` on the same connection, and the server SHALL NOT emit or require the `Mcp-Session-Id` header.

#### Scenario: Tool call without session handshake

- **WHEN** a client POSTs a `tools/call` request to `/mcp` without any prior `initialize` and without a session header
- **THEN** the tool executes and returns a normal JSON-RPC result

### Requirement: Unsupported HTTP methods on the MCP endpoint

The server SHALL respond `405 Method Not Allowed` to `GET` and `DELETE` requests on `/mcp`, as this iteration provides neither SSE streams nor session termination.

#### Scenario: GET returns 405

- **WHEN** a client sends `GET /mcp`
- **THEN** the response is `405 Method Not Allowed`

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

### Requirement: Protocol version negotiation

The server SHALL support at least protocol versions `2024-11-05` and `2025-03-26`. In the `initialize` response, the server SHALL return the client's requested protocol version when it is supported, and otherwise SHALL return its preferred supported version so the client can decide whether to proceed. HTTP clients sending the `MCP-Protocol-Version` header with a supported version SHALL be answered consistently with that version.

#### Scenario: Supported version echoed

- **WHEN** a client sends `initialize` requesting protocol version `2025-03-26`
- **THEN** the initialize result contains `protocolVersion: "2025-03-26"`

#### Scenario: Unsupported version falls back to a supported one

- **WHEN** a client sends `initialize` requesting a protocol version the server does not support
- **THEN** the initialize result contains a protocol version the server does support

### Requirement: Optional bearer authentication

When an MCP auth token is configured (via CLI flag or environment variable), every `/mcp` request SHALL carry a valid `Authorization: Bearer <token>` header; requests with a missing or invalid token SHALL be rejected with `401`. When no token is configured, the endpoint SHALL NOT require authentication. Default configuration (localhost bind, no token) SHALL remain zero-config for local development.

#### Scenario: Missing token rejected when configured

- **WHEN** the server is configured with an MCP auth token and a request to `/mcp` arrives without an `Authorization` header
- **THEN** the response is `401`

#### Scenario: Valid token accepted

- **WHEN** the server is configured with an MCP auth token and a request carries `Authorization: Bearer <token>` with the correct value
- **THEN** the request is processed normally

#### Scenario: No token configured means no auth

- **WHEN** the server is configured without an MCP auth token
- **THEN** requests to `/mcp` are processed without any `Authorization` header

### Requirement: Concurrency safety with shared model residency

The server SHALL process concurrent `/mcp` requests safely on the threaded HTTP server. Overlapping tool calls SHALL share a single embedding-model load through the existing reference-counted residency mechanism, and a tool call arriving while an embedding-model rebuild is in progress SHALL fail with the same structured error the REST search endpoint returns in that state.

#### Scenario: Concurrent tool calls both succeed

- **WHEN** two clients POST `tools/call` requests for `search_codebases` concurrently
- **THEN** both receive correct results and the embedding model is loaded at most once for the overlapping window

#### Scenario: Tool call during reindex reports busy

- **WHEN** a `tools/call` request that requires the embedding model arrives while an automatic re-embedding is in progress
- **THEN** the JSON-RPC result carries `isError: true` with a message indicating reindexing is in progress

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
