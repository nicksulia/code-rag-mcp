## Context

See proposal.md - Why. The generation layer today spans:

- `src/generator/rag_engine.py` (`RAGGenerator`): local Ollama, remote provider (Gemini/Anthropic/OpenAI), and deterministic-offline-synthesizer invocation, plus model lifecycle (`unload_model`).
- `src/retriever/reranker.py` (`ContextPackager`): packages ranked `SearchResult`s into a formatted prompt string and a `List[Citation]`, with a token/char budget.
- `src/models/schema.py` (`Citation`): the generation-citation model returned alongside synthesized answers.
- `src/service.py` (`MultiRepoRAGService`): wires `self.packager` and `self.rag_generator`, and exposes `query_rag()` / `stream_rag()`.
- `src/server/api.py`: `POST /api/v1/rag/query` and `POST /api/v1/rag/stream` handlers call `service.query_rag` / `service.stream_rag`.
- `src/mcp/server.py`: the `query_cross_repo_rag` tool definition and its `_execute_tool_impl` branch.
- `src/cli/main.py`: the `chat` subcommand (`cmd_chat`) and its `--provider`/`--model`/`--top-k` options.
- `web/index.html`, `web/app.js`, `web/style.css`: the RAG Studio split-pane tab (streaming chat + inline citation inspector).

`search_codebases` / `POST /api/v1/search` / `service.search()` already return ranked results with repository, file, line, symbol, graph, and relation metadata and are unaffected.

## Goals / Non-Goals

**Goals:**
- Delete all generation-only code paths (LLM invocation, model lifecycle, prompt packaging, generation citations, chat/RAG UI and API/MCP/CLI surfaces) with no dead code left behind.
- Keep `search_codebases`, `POST /api/v1/search`, and `service.search()` behaviorally unchanged.
- Keep embedding generation/lifecycle, dense+lexical retrieval, RRF reranking, symbol/graph enrichment, scope expansion, and relation hop-decay unchanged.

**Non-Goals:**
- Changing the `/api/v1/search` or `search_codebases` response contract.
- Introducing any new client-side generation/orchestration hooks for cloud LLMs (that's the consuming client's responsibility).
- Redesigning the Web UI beyond removing RAG Studio and promoting Hybrid Search to the primary/default view.

## Decisions

1. **Delete rather than deprecate.** Per the proposal, these are breaking removals (no shim, no feature flag), since the proposal explicitly calls out removed MCP tool, endpoints, and CLI command. Alternative considered: keep the endpoints returning `410 Gone` — rejected because the proposal calls for outright removal and no consumers are expected to depend on a transition period.
2. **Remove `src/generator/` entirely** (module + `RAGGenerator`) rather than keeping an empty package. `src/service.py` drops the `self.rag_generator` attribute, `query_rag()`, `stream_rag()`, and the `unload_model()` calls guarded by `hasattr(self.rag_generator, ...)`; `unload_models()` keeps only embedding-model unloading.
3. **Remove `ContextPackager` from `src/retriever/reranker.py`**, keeping the RRF fusion/reranking classes in that same file intact, since the proposal explicitly separates "packaging" (generation-oriented) from "fusion and reranking" (preserved).
4. **Remove `Citation` from `src/models/schema.py`** since it is only constructed by `ContextPackager` and only consumed by the generation response paths; confirm via grep before deleting that no retrieval-only code constructs it.
5. **CLI**: remove the `chat` subparser and `cmd_chat` function, and its dispatch branch, rather than aliasing it to `search`, since the proposal says LLM-specific provider/model options become unsupported outright.
6. **Web UI**: remove the RAG Studio tab/markup/JS/CSS and make Hybrid Search the default/landing view; keep the shared sidebar scope controls (repo checkboxes, group multi-select, expansion controls) since Hybrid Search still uses them.
7. **Order of removal**: bottom-up (generator module and its schema types → service layer → API/MCP/CLI entry points → Web UI → docs/tests) so intermediate states stay import-clean and testable after each step.

## Risks / Trade-offs

- [Risk] Deleting `Citation` could break an import used elsewhere unexpectedly → Mitigation: grep for `Citation` usage across `src/` and `tests/` before removal and after, and run the full test suite.
- [Risk] `service.unload_models()` / `shutdown()` currently guard LLM unloading with `hasattr(self.rag_generator, ...)`; removing `rag_generator` without updating these methods leaves dead attribute references → Mitigation: update both methods in the same task as removing `rag_generator`.
- [Risk] Removing the `chat` CLI command could leave `argparse` subparser wiring inconsistent (e.g., stray imports) → Mitigation: verify `python -m src.cli.main --help` (or equivalent) after edits shows no `chat` command and no import errors.
- [Risk] Web UI removal could leave orphaned CSS classes or unreachable JS functions → Mitigation: search `app.js`/`style.css` for RAG-Studio-only identifiers and remove them together with the markup.

## Migration Plan

1. Remove `src/generator/` and `Citation`/`ContextPackager` (with their tests), then fix resulting import errors in `service.py`.
2. Remove `query_rag`/`stream_rag`/generation-only branches from `service.py`, keeping `search()` and embedding lifecycle methods intact.
3. Remove the MCP `query_cross_repo_rag` tool definition/handler, the REST `rag/query`/`rag/stream` handlers, and the CLI `chat` command.
4. Remove the Web UI RAG Studio tab and make Hybrid Search the default view.
5. Update/remove tests referencing removed code (`test_e2e_rag.py`, `test_mcp_server.py`, `test_ollama_integration.py`, `test_unsloth_integration.py`) so the suite covers only preserved behavior.
6. Update README/docs describing the service as a retrieval and code-search service for external cloud LLM consumers.
7. Rollback strategy: this is a source-controlled deletion; rollback is a straightforward `git revert` of the change's commit(s) since no data migration or persisted schema change is involved.

## Open Questions

None - the proposal and existing code fully determine the scope of removal.
