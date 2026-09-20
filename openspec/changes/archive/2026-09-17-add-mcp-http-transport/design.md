# Design

## Context

See proposal.md for motivation. Current state that shapes the approach:

- `src/mcp/server.py` implements MCP as line-delimited JSON-RPC over stdio. `MCPServer.handle_request(request: dict) -> Optional[dict]` is transport-agnostic: it returns a response dict for requests and `None` for notifications. The stdio loop is a thin shell around it.
- `src/server/api.py` serves REST + web UI from a stdlib `ThreadingMixIn`/`HTTPServer` (`make_request_handler(service, web_dir)`), bound to `127.0.0.1:8000` by default, with `Access-Control-Allow-Origin: *` on all responses today.
- `src/service.py` takes an exclusive instance lock per data dir; `src/indexer/lifecycle.py` is a reference-counted (`RLock`) embedding-model residency controller explicitly designed for overlapping sessions. REST already exercises it concurrently.
- `handle_request` answers `initialize` with a hardcoded `protocolVersion: "2024-11-05"`. The Streamable HTTP transport exists only in MCP spec revisions `2025-03-26` and later.
- `ReindexInProgressError` currently surfaces on REST as `503`; inside `handle_request`'s `tools/call` path, any exception becomes an `isError: true` tool result — that mapping already satisfies the "busy during reindex" requirement without new code.

## Goals / Non-Goals

**Goals:**
- `POST /mcp` on the existing `serve` port, stateless, JSON responses, delegating to the unmodified tool dispatch used by stdio.
- Protocol-version negotiation shared by both transports, with stdio behavior bit-for-bit compatible for clients requesting `2024-11-05`.
- Spec-required HTTP transport hygiene: `Origin` validation (DNS-rebinding protection), plus opt-in bearer auth for non-localhost binds.
- Zero new dependencies; stdlib only, matching the existing server.

**Non-Goals:**
- SSE streaming, server-initiated notifications, `Mcp-Session-Id` session management, resumability (all optional under the spec; deferred until remote/shared deployment needs them).
- OAuth 2.1 authorization flows; full per-session state; horizontal scaling.
- Changing any tool schema, tool result shape, REST endpoint, or the stdio framing.
- Retrofitting the legacy flat prose specs into OpenSpec-recognized capabilities (only the `mcp-http-transport` delta is created here).

## Decisions

### 1. Topology: fold `/mcp` into the existing `serve` process — rejected alternatives

One HTTP server, one port, one instance lock, one embedding lifecycle. `make_request_handler` gains an MCP routing branch in `do_POST`/`do_GET`/`do_DELETE` before the REST routes, constructed with an `MCPServer` wrapping the same `MultiRepoRAGService`.

- **Rejected: separate `mcp --http` process/port.** The exclusive per-data-dir instance lock makes a second process fail fast by default, and two processes would maintain two embedding-model residencies (double memory). It would only make sense behind `--allow-multi-instance` as a deliberate remote-deployment choice — out of scope now.
- **Rejected: standalone socket/uds.** Doesn't serve HTTP-only clients, which are the point of the change.

### 2. Transport mode: stateless, JSON-only Streamable HTTP

The MCP spec permits `application/json` responses when the server has nothing to stream, permits omitting session IDs (stateless mode), and permits `405` on `GET /mcp` when no SSE stream is offered. Our server has no server-initiated messages and `handle_request` is already stateless, so full Streamable HTTP buys nothing today.

Request mapping (single code path, mirroring `handle_request`'s contract):

```
POST /mcp
  body not JSON            -> 400 + JSON-RPC error -32700
  body is a JSON array     -> 400 (batching removed in 2025-03-26)
  no "method" key          -> 202 Accepted, empty body (notification or client response;
                              short-circuit before handle_request, which would answer a
                              method-less message carrying an id with -32601, wrongly
                              yielding 200 for a JSON-RPC response message)
  handle_request -> dict   -> 200 application/json (the response)
  handle_request -> None   -> 202 Accepted, empty body (notification)
GET /mcp, DELETE /mcp      -> 405
```

- **Rejected: full Streamable HTTP (SSE + sessions + replay).** Complexity (event store, resumability, session table) with no consumer; the stateless mode is forward-compatible — a later change can add SSE without breaking JSON-mode clients.
- **Rejected: legacy HTTP+SSE transport (2024-11-05).** Already superseded; new HTTP clients target Streamable HTTP.

### 3. Version negotiation: echo-or-fallback, shared by both transports

`SUPPORTED_PROTOCOL_VERSIONS = ("2025-03-26", "2024-11-05")` (first = preferred). `initialize` reads `params.protocolVersion`: if supported, echo it; otherwise return the preferred version and let the client decide (spec-defined negotiation). Because the logic lives in `handle_request`, stdio clients requesting `2024-11-05` get exactly today's response — no stdio behavior change. The `MCP-Protocol-Version` header is not hard-validated in this iteration (leniency is safe because stateless responses carry no version-sensitive framing); initialize negotiation is the contract, and header enforcement can tighten when sessions/SSE arrive.

### 4. Origin validation: localhost + bind-host allowlist, applied before dispatch

For `/mcp` requests with an `Origin` header, extract the origin hostname and accept `localhost`, `127.0.0.1`, `::1`, plus the configured bind host when it is non-localhost (any port — dev servers and the bundled web UI move across ports). No `Origin` header → accept (non-browser clients). Anything else → `403`, before any JSON parsing. `/mcp` responses also omit the blanket `Access-Control-Allow-Origin: *` header the REST routes send, so a browser that somehow obtained a response cannot read it cross-origin.

Including the bind host keeps same-origin browser clients working on non-localhost binds (e.g. a page served from `http://<lan-ip>:8000`), matching the spec's "allowlist covering the configured bind address and localhost variants"; the rebinding threat is cross-origin, which the hostname check still stops.

- **Why not strict origin==bind-address matching:** too brittle for local dev (UI on :8000, proxies, SSH-forwarded ports) while the rebinding threat is specifically cross-origin browser attacks, which the hostname check stops.
- **Rejected: Host-header validation as the primary control.** Rebinding attacks originate in browsers that always send `Origin` on non-safelisted POSTs (`application/json` forces preflight); `Origin` is the signal the MCP spec mandates checking.

### 5. Auth: opt-in bearer token, constant-time compared

`serve --mcp-token <token>` or `MCP_AUTH_TOKEN` env. When set, `/mcp` requires `Authorization: Bearer <token>`, compared with `secrets.compare_digest`; failures get `401` + `WWW-Authenticate: Bearer`. When unset (default), no auth — preserving today's zero-config localhost workflow. At startup, binding a non-localhost host without a token prints a loud stderr warning recommending the token and noting the plain-HTTP caveat (token over HTTP is sniffer-visible; remote exposure belongs behind TLS or a tunnel).

### 6. Concurrency: reuse what exists

The endpoint runs on the existing threaded server; each POST wraps tool execution in the same `embedding_session` refcount used by stdio and REST, so overlapping MCP calls share one model load. `ReindexInProgressError` already funnels through `handle_request`'s exception handler into an `isError: true` result — no new error path. SQLite access patterns are unchanged from what threaded REST already performs.

## Risks / Trade-offs

- [Browser preflight passes because `do_OPTIONS` answers `*` for all paths] → Origin validation runs before dispatch on the actual POST, so the request is rejected with `403` even if the preflight succeeded; additionally `/mcp` responses don't emit CORS headers.
- [Preflight advertises `Access-Control-Allow-Headers: Content-Type` only, so a browser client could not send `Authorization` cross-origin even with a token configured] → acceptable: bearer auth targets non-browser clients (no preflight), and cross-origin browser origins are rejected by origin validation anyway; if browser+token use ever matters, add `Authorization` to the preflight allow-headers for `/mcp`.
- [Token over plain HTTP is sniffable on non-localhost binds] → startup warning; docs direct remote use behind TLS/SSH tunnel; auth is off by default only for localhost-style use.
- [Lenient `MCP-Protocol-Version` header handling lets a mismatched client proceed] → stateless JSON responses carry no version-sensitive framing, so the risk is cosmetic; documented as tightening candidates when SSE/sessions land.
- [`Access-Control-Allow-Origin: *` remains on REST routes] → pre-existing posture, unchanged by this change; MCP endpoint is held to the stricter standard because the MCP spec mandates it.
- [405 on `GET /mcp` surprises clients probing for SSE] → spec-permitted; the 405 response is the defined signal for "no stream offered", and clients fall back or fail fast.

## Migration Plan

Purely additive: new route, new flag, negotiation logic that preserves `2024-11-05` echo for existing stdio clients. No data, index, or config migration. Rollback = revert the route; stdio and REST are untouched by construction. Verification: existing `tests/test_mcp_server.py` suite passes unmodified (stdio parity), new HTTP transport tests cover the spec scenarios.

## Open Questions

- Should the REST API share the bearer token when configured (one token for the whole server)? Deferrable — MCP-only enforcement satisfies this change; a follow-up can widen scope without altering these specs.
- When remote/shared deployment arrives, do we want SSE + sessions or a jump straight to an OAuth-gated deployment? Deferrable — the stateless mode remains valid either way.
