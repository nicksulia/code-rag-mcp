# Proposal

## Why

The REST API already serves full file contents via `GET /api/v1/file?repo_id=...&path=...`, but the MCP server exposes no equivalent tool. AI assistants connected over MCP must approximate file reads through `search_codebases` (ranked chunks, not guaranteed full-file) or `get_symbol_definition` (symbol-scoped), which breaks common agent workflows that need to read an entire known file (e.g., after locating it via search or symbol navigation).

## What Changes

- Add a new MCP tool `read_file` to the MCP server (both stdio and HTTP transports, since both dispatch through the same `handle_request`/`TOOLS_SPEC`).
- `read_file` takes `repo_id` (required) and `path` (required, repo-relative) and returns the full UTF-8 text content of the file.
- Reuse the existing `service.get_file_content()` implementation shared with the REST endpoint for the file read itself.
- Harden `service.get_file_content()` with a repository-root containment check (rejecting `..` escapes and absolute paths), fixing a latent path-traversal flaw the REST endpoint has today; out-of-root paths return "not found" on both REST and MCP after the fix.
- Structured tool errors when `repo_id`/`path` are missing, the repository is unknown, or the file does not exist on disk — mirroring the REST endpoint's 400/404 behavior as MCP error results.
- No changes to the REST route shape, indexing, or storage. The only REST behavior change is that out-of-root traversal paths now return 404 instead of served content.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `mcp-server`: Add requirement for the new `read_file` tool (tool spec, arguments, error behavior) in the MCP tool catalog.

## Impact

- `src/mcp/server.py`: new entry in `TOOLS_SPEC`, new branch in `_execute_tool_impl`.
- `src/service.py`: containment check in `get_file_content()` (shared by REST and MCP).
- `tests/test_mcp_server.py` / `tests/test_mcp_http_transport.py`: coverage for the new tool over both transports.
- `tests/test_api_relations.py`: new coverage for `GET /api/v1/file` (success and traversal rejection) — no test covers this endpoint today.
- `openspec/specs/mcp-server.md`: gains a `read_file` tool specification upon archive.
- No dependency, schema, or storage changes. Backward compatible for all legitimate use: existing MCP tools and in-root file reads are unaffected; only out-of-root traversal paths change behavior (404), which is a security fix, not a contract break.
