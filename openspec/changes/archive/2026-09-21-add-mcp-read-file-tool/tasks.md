# Tasks

## 1. Service Layer

- [x] 1.1 Add repository-root containment to `service.get_file_content()` (`src/service.py:652`): resolve `root / file_path`, reject absolute paths and `..` escapes (return `None`), verify with a unit test asserting `../` and absolute paths return `None` while a normal nested path still reads
- [x] 1.2 Add REST endpoint coverage for `GET /api/v1/file` in `tests/test_api_relations.py` (which already hosts the API `_request` harness): a successful read of a fixture file returns 200 with `{repo_id, file_path, content}`, and `../`/absolute paths return 404 — no test covers this endpoint today

## 2. MCP Tool

- [x] 2.1 Add the `read_file` tool definition to `TOOLS_SPEC` in `src/mcp/server.py` (description noting it is for targeted full-file reads; input schema with required `repo_id` and `path` strings) and verify `tools/list` output includes it via a test in `tests/test_mcp_server.py`
- [x] 2.2 Add the `read_file` branch to `_execute_tool_impl` in `src/mcp/server.py`: raise `ValueError` naming the missing argument when `repo_id` or `path` is absent/empty, call `service.get_file_content()`, return a structured not-found error result when it returns `None`, otherwise return `{repo_id, file_path, content}`; verify with tests covering success, missing argument, unknown repo, and nonexistent file

## 3. Transport Coverage

- [x] 3.1 Add an HTTP-transport test for `read_file` in `tests/test_mcp_http_transport.py` (successful read plus one error case) and verify it passes

## 4. Verification

- [x] 4.1 Run `python -m pytest tests/test_mcp_server.py tests/test_mcp_http_transport.py tests/test_api_relations.py -v` and verify all pass (the new 1.2 cases confirm the containment change did not regress the REST endpoint)
- [x] 4.2 Run `openspec validate add-mcp-read-file-tool` and verify the change validates
