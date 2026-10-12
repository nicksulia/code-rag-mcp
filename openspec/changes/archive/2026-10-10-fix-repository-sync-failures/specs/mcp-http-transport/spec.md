# Spec Delta

## MODIFIED Requirements

### Requirement: stdio transport unchanged

The existing stdio transport SHALL retain its line-delimited JSON-RPC framing, methods, stderr-only diagnostic logging, and behavior of unrelated tools. Protocol-version negotiation SHALL continue to follow the protocol-version-negotiation requirement.

Repository synchronization and updates SHALL use the corrected shared repository-operation success/failure contract on both stdio and HTTP transports. The `sync_repository` tool SHALL expose the additive optional boolean `local_only` argument, defaulting to false. Existing successful calls without this argument SHALL retain their payload shape; previously swallowed Git failures SHALL instead produce safe tool errors, and explicitly requested local-only indexing SHALL report its distinct result. These corrections SHALL NOT alter HTTP authentication, origin validation, protocol negotiation, or unrelated tool results.

#### Scenario: stdio initialize still works
- **WHEN** an MCP client spawns the stdio server and sends `initialize` requesting protocol version `2024-11-05`, followed by `tools/list`
- **THEN** the responses retain their existing framing and shape
- **AND** the existing tool catalog is retained with the additive `local_only` sync argument

#### Scenario: Corrected sync errors match both transports
- **WHEN** the same required Git operation fails for equivalent sync calls over stdio and HTTP
- **THEN** both return `isError: true` with equivalent safe repository-operation error information
- **AND** neither reports a successful sync from the cached checkout

#### Scenario: Existing successful tool calls remain compatible
- **WHEN** a client makes a previously supported successful sync call without `local_only`, or calls an unrelated tool
- **THEN** its successful result retains the existing payload shape and semantics
