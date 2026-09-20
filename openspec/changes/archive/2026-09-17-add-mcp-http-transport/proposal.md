# Proposal

## Why

The MCP server is reachable only over `stdio`: every consumer must be a local process able to spawn `python3 main.py mcp`. Web-based IDEs, hosted agents, and any client that can only speak HTTP cannot use the code-intelligence tools at all. Additionally, the living spec `openspec/specs/mcp-server.md` already advertises the protocol "over `stdio` and HTTP", but no HTTP transport exists in code — the spec overpromises, and this change closes that gap. A single always-on HTTP endpoint also matches how the REST API is already deployed (one `serve` process holding the exclusive data-dir instance lock and the shared embedding-model lifecycle), avoiding a second process that would double model residency and fight the lock.

## What Changes

- Add a `/mcp` endpoint to the existing `serve` HTTP server implementing the MCP **Streamable HTTP** transport in stateless, JSON-response mode:
  - `POST /mcp` accepts a single JSON-RPC message and returns `200 application/json` for requests, `202 Accepted` (empty body) for notifications/responses, and `400` for JSON-RPC batch arrays (batches were removed from the MCP spec in 2025-03-26).
  - `GET /mcp` and `DELETE /mcp` return `405` — no SSE streams and no sessions in this iteration (both are optional under the spec).
  - The endpoint delegates to the existing transport-agnostic `MCPServer.handle_request`; no tool behavior changes.
- Extend protocol-version negotiation in the shared `initialize` handler (echo-or-fallback over `2025-03-26` and `2024-11-05`): stdio clients requesting `2024-11-05` get a bit-for-bit identical response to today, while HTTP clients can negotiate `2025-03-26` (the `MCP-Protocol-Version` header is accepted leniently in this iteration; `initialize` negotiation is the contract).
- Add transport security controls required by the MCP spec for HTTP servers: `Origin` header validation against a localhost allowlist (DNS-rebinding protection), keeping the existing `127.0.0.1` default bind, and an **optional** bearer token (`--mcp-token` / env) for non-localhost exposure.
- Reconcile the spec/code mismatch: the flat prose spec's "stdio and HTTP" claim becomes true; the delta creates the OpenSpec-recognized capability that legacy file never registered.
- stdio transport, all tool schemas, and the REST API surface are unchanged. No breaking changes. No new dependencies (the server is stdlib `http.server` today and stays that way).

Deferred (non-goals for this change): SSE streaming responses and server-initiated notifications, session management via `Mcp-Session-Id`, resumability, and full OAuth. These become relevant only when remote/shared deployment is in scope.

## Capabilities

### New Capabilities

- `mcp-http-transport`: Serving the MCP JSON-RPC tool surface over HTTP (Streamable HTTP transport, stateless JSON mode) from the existing `serve` process, including request shaping, protocol-version negotiation, and HTTP transport security (origin validation, optional bearer auth).

### Modified Capabilities

None. The flat prose file `openspec/specs/mcp-server.md` is not an OpenSpec-recognized capability (only `retrieval-quality` is); its "stdio and HTTP" claim is treated as a spec/code conflict this change resolves by making the claim true, not as a requirement delta.

## Impact

- **Code**: `src/server/api.py` (new `/mcp` route, origin validation, optional auth check), `src/mcp/server.py` (share `handle_request`; version negotiation), `src/cli/main.py` (`serve` flags for the MCP endpoint/token), new transport tests alongside `tests/test_mcp_server.py` and `tests/test_api_relations.py`.
- **APIs**: New HTTP endpoint `POST /mcp` on the `serve` port. Existing REST endpoints and the stdio MCP loop are untouched.
- **Docs**: `README.md` (MCP-over-HTTP client configuration), `openspec/specs/mcp-server.md` (conflict resolved by implementation).
- **Runtime**: No new processes or ports; the single instance lock and refcounted embedding lifecycle are shared with REST, so concurrent MCP-over-HTTP tool calls behave like existing concurrent REST searches.
