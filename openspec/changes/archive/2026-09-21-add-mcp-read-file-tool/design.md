# Design

## Context

The MCP server dispatches tools through `TOOLS_SPEC` + `_execute_tool_impl` in `src/mcp/server.py`, sharing one dispatch path for stdio and HTTP transports. The REST endpoint `GET /api/v1/file` already reads files via `service.get_file_content()` (`src/service.py:652`), which resolves `root / file_path` and returns `None` for unknown repos or missing files. See proposal.md for motivation.

One latent issue in the existing helper matters here: `root / file_path` performs no containment check, so a path like `../../etc/passwd` escapes the repository root. The REST endpoint has this flaw today; adding a second exposure surface (MCP) is the right moment to fix it centrally.

## Goals / Non-Goals

**Goals:**
- Expose `read_file` over MCP with behavior equivalent to `GET /api/v1/file`.
- Repository-root containment enforced in one place (`get_file_content`), protecting both REST and MCP callers.
- Error handling consistent with existing MCP tools: argument errors raise `ValueError` (surfaced as `isError: true` tool results); unknown repo / missing file return a structured error result, not a crash.

**Non-Goals:**
- No alias/name resolution for `repo_id` — exact repo IDs only, matching the current `get_file_content` contract used by the REST endpoint.
- No binary file support, no pagination/range reads, no directory listing.
- No changes to the REST route shape or to indexing.

## Decisions

- **Reuse `service.get_file_content()` unchanged in signature.** The MCP handler adds only argument validation (`repo_id`, `path` present and non-empty) and maps the `None` result to a structured "not found" error. Alternative considered: a separate service method for MCP — rejected as duplication.
- **Fix containment inside `get_file_content`** by resolving the target path and verifying it stays under the repository root (rejecting `..` escapes and absolute paths); an out-of-root path returns `None`, flowing into the same "not found" result. Alternative considered: validating in each transport handler — rejected because the REST endpoint would remain exposed.
- **Result shape mirrors the REST response**: `{repo_id, file_path, content}` so agents and the web UI share one mental model.
- **Tool registration**: single entry appended to `TOOLS_SPEC` with a `required: ["repo_id", "path"]` input schema, plus one `elif` branch in `_execute_tool_impl`, following the `sync_repository` pattern.

## Risks / Trade-offs

- [Agent pulls very large files, blowing up context windows] → Mitigation: document in the tool description that `read_file` is for targeted full-file reads after search/symbol navigation; no artificial size cap in v1 (REST parity), revisit if misuse appears.
- [Containment fix changes behavior for any caller relying on `..` paths] → Mitigation: that usage is a security bug, not a contract; the REST endpoint returns 404 for such paths after the fix, same as a missing file.
- [Symlinked files inside a repo pointing outside the root] → Mitigation: containment check uses resolved paths, so out-of-root symlink targets are rejected the same way.
