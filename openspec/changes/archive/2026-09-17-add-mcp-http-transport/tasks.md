# Tasks

## 1. Protocol negotiation in the shared MCP handler

- [x] 1.1 Add `SUPPORTED_PROTOCOL_VERSIONS = ("2025-03-26", "2024-11-05")` to `src/mcp/server.py` and implement echo-or-fallback negotiation in the `initialize` branch of `handle_request` (echo the client's requested version when supported, otherwise return the preferred first entry); verify with a new unit test in `tests/test_mcp_server.py` asserting `2025-03-26` is echoed, unknown versions fall back, and `2024-11-05` clients get an identical response to today
- [x] 1.2 Run the full existing `tests/test_mcp_server.py` suite and verify it passes unmodified (stdio parity is preserved)

## 2. HTTP transport endpoint

- [x] 2.1 Add MCP routing to `make_request_handler` in `src/server/api.py`: construct an `MCPServer` over the shared `MultiRepoRAGService`, and route `POST /mcp` before the REST routes — parse the JSON body, reject non-JSON with `400` + JSON-RPC error `-32700`, reject JSON arrays with `400`, pass objects to `handle_request`, return `200 application/json` when it yields a dict and `202` with empty body when it yields `None`; verify with a test that POSTs `initialize`, `tools/list`, and a `notifications/initialized` to a running handler and asserts the three response shapes
- [x] 2.2 Return `405 Method Not Allowed` for `GET /mcp` and `DELETE /mcp`; verify with a test asserting both methods return 405
- [x] 2.3 Verify HTTP/stdio parity: a test that issues the same `tools/call` (e.g. `list_repositories`) through `handle_request` directly and through `POST /mcp`, asserting identical result payloads

## 3. Transport security

- [x] 3.1 Implement `Origin` validation for `/mcp` in `src/server/api.py`: requests without an `Origin` header pass; origins whose hostname is `localhost`, `127.0.0.1`, `::1`, or the configured bind host pass; all others get `403` before any body parsing; verify with tests covering an attacker origin (403), a localhost origin (accepted), a bind-host origin on a non-localhost bind (accepted), and a missing origin (accepted)
- [x] 3.2 Exclude `/mcp` responses from the blanket `Access-Control-Allow-Origin: *` CORS headers the REST routes send; verify with a test asserting a successful `POST /mcp` response carries no `Access-Control-Allow-Origin` header
- [x] 3.3 Add optional bearer auth: `--mcp-token` CLI flag on `serve` plus `MCP_AUTH_TOKEN` env fallback, enforced on `/mcp` via `secrets.compare_digest`, returning `401` with `WWW-Authenticate: Bearer` on missing/invalid tokens; verify with tests for configured+missing header (401), configured+valid token (processed), and unconfigured (no auth required)
- [x] 3.4 Print a stderr startup warning from `cmd_serve` when the bind host is non-localhost and no token is configured; verify by running `serve --host 0.0.0.0` without a token and observing the warning, and with a token observing none

## 4. Concurrency and runtime integration

- [x] 4.1 Add a test issuing concurrent `POST /mcp` `tools/call` requests for `search_codebases` against a running server with a small indexed fixture, asserting both return correct results and the embedding model loads at most once for the overlapping window (inspect `GET /api/v1/models/status` active-operation accounting or lifecycle logs)
- [x] 4.2 Add a test that a `tools/call` arriving while a re-embedding rebuild holds the index returns a JSON-RPC result with `isError: true` and a reindex-in-progress message (simulate via the same mechanism `ReindexInProgressError` tests use)

## 5. Documentation and validation

- [x] 5.1 Update `README.md`: document the `/mcp` endpoint, an HTTP client configuration example, the `--mcp-token`/`MCP_AUTH_TOKEN` option, the origin-validation behavior, and the deferred items (SSE, sessions); verify the documented `curl` example works against a running server
- [x] 5.2 Run the full test suite (`python3 -m unittest discover -s tests -p "test_*.py"`) and verify all tests pass, including pre-existing suites
- [x] 5.3 Run `openspec validate add-mcp-http-transport --strict` and verify the change validates cleanly
