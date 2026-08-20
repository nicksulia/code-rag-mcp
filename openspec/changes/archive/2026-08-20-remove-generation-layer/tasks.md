## 1. Generation Module & Models

- [x] 1.1 Delete `src/generator/` (`rag_engine.py`, `__init__.py`) and its `__pycache__`.
- [x] 1.2 Remove `ContextPackager` from `src/retriever/reranker.py`, keeping RRF fusion/reranking classes intact.
- [x] 1.3 Remove the `Citation` model from `src/models/schema.py` after confirming (via grep) it is not used outside generation code paths.

## 2. Service Layer

- [x] 2.1 Remove `self.packager` / `self.rag_generator` construction and imports (`ContextPackager`, `RAGGenerator`) from `src/service.py`.
- [x] 2.2 Remove `query_rag()` and `stream_rag()` from `src/service.py`.
- [x] 2.3 Remove the `rag_generator.unload_model()` calls in `shutdown()` and `unload_models()`, keeping embedding-model unloading behavior unchanged.
- [x] 2.4 Verify `service.search()` and embedding lifecycle methods are unaffected (no references to removed attributes remain).

## 3. MCP Server

- [x] 3.1 Remove the `query_cross_repo_rag` tool definition from the MCP tool list in `src/mcp/server.py`.
- [x] 3.2 Remove the `query_cross_repo_rag` branch from `_execute_tool_impl` and any related logging/citation-building code.
- [x] 3.3 Verify `search_codebases` and the other MCP tools (`get_symbol_definition`, `get_call_hierarchy`, `get_cross_repo_api_links`, `list_repositories`, `update_repository`, `sync_repository`, `manage_repository_relations`, `get_repository_relations`) are unaffected.

## 4. REST API

- [x] 4.1 Remove the `POST /api/v1/rag/query` handler from `src/server/api.py`.
- [x] 4.2 Remove the `POST /api/v1/rag/stream` (SSE) handler from `src/server/api.py`.
- [x] 4.3 Verify `POST /api/v1/search` and all other endpoints are unaffected.

## 5. CLI

- [x] 5.1 Remove the `chat` subparser (including `--provider`/`--model`/`--top-k` options) and the `cmd_chat` function from `src/cli/main.py`.
- [x] 5.2 Remove the `chat` dispatch branch in `main()`.
- [x] 5.3 Verify the CLI help output no longer lists `chat` and no import errors result.

## 6. Web UI

- [x] 6.1 Remove the RAG Studio tab markup from `web/index.html`.
- [x] 6.2 Remove RAG-Studio-only JS (streaming chat, citation inspector) from `web/app.js`.
- [x] 6.3 Remove RAG-Studio-only styles from `web/style.css`.
- [x] 6.4 Make Hybrid Search the default/landing view, keeping the shared sidebar scope controls intact.

## 7. Tests & Documentation

- [x] 7.1 Remove or update `tests/test_e2e_rag.py`, `tests/test_mcp_server.py`, `tests/test_ollama_integration.py`, and `tests/test_unsloth_integration.py` to drop generation-only coverage while preserving retrieval/search/MCP-tool coverage.
- [x] 7.2 Update README and other docs to describe the service as a retrieval and code-search service for external cloud LLM consumers, removing references to chat/RAG generation.
- [x] 7.3 Run the full test suite and confirm it passes with no references to removed generation code.
