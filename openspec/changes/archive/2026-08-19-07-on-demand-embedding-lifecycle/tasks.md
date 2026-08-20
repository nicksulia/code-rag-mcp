## 1. Lazy Model Probing

- [x] 1.1 Remove the eager `_probe_dimension()` call from `OllamaEmbeddingEngine.__init__` in `src/indexer/embeddings.py` so construction issues no HTTP request
- [x] 1.2 Convert `dimension` into a lazy, cached property that resolves from an injected metadata provider first, then a one-shot probe on first embedding use
- [x] 1.3 Change `VectorStore` in `src/indexer/vector_store.py` to accept a lazy dimension provider (callable) instead of reading `engine.dimension` at construction time
- [x] 1.4 Update `ensure_model_ready` and CLI `src/cli/main.py:26` startup check so availability/health checks use `/api/tags` only and never force a model load
- [x] 1.5 Add unit tests asserting that constructing `MultiRepoRAGService` issues no `/api/embed` or `/api/generate` request

## 2. Embedding Lifecycle Core

- [x] 2.1 Add an `EmbeddingLifecycle` class (new module under `src/indexer/`) holding an `RLock`, active-operation counter, cancellable release timer, policy, and residency state
- [x] 2.2 Implement the `session(reason)` context manager: increment + cancel pending release on entry; decrement in `finally` and arm the release timer at zero
- [x] 2.3 Implement `release()` sending `keep_alive: 0`, reusing the existing unload request path, and make it a no-op when the active count is non-zero
- [x] 2.4 Thread `keep_alive: <policy>` into `_fetch_ollama_embedding` for both `/api/embed` and the `/api/embeddings` fallback
- [x] 2.5 Implement policy parsing for `EMBEDDING_KEEP_ALIVE` (duration string, `0`, `-1`/`always`) with a 30s idle-grace default, plus `always-resident` behavior that never arms the timer
- [x] 2.6 Add structured logging for load/release transitions including trigger reason and elapsed load time
- [x] 2.7 Expose residency state (`loaded`/`released`, active count, model, policy) for the status interface
- [x] 2.8 Unit tests: single load under overlapping sessions, single release after the last exit, release on exception paths, late session cancelling a pending release, always-resident opt-out

## 3. Wiring Lifecycle Into Operations

- [x] 3.1 Instantiate `EmbeddingLifecycle` in `MultiRepoRAGService` and expose `service.embedding_session(reason)`
- [x] 3.2 Wrap indexing/sync entry points in `src/service.py` in a single embedding session per run
- [x] 3.3 Wrap search and query/chat entry points, ensuring streaming generators hold the session for their entire lifetime (including abandonment)
- [x] 3.4 Wrap MCP tool handlers in `src/mcp/` and the CLI search/index/chat commands
- [x] 3.5 Update `service.unload_models()` and `POST /api/v1/models/unload` in `src/server/api.py` to release when idle and return a busy response when an operation is in flight
- [x] 3.6 Keep server-shutdown unload as a final safety release and cancel any pending release timer on shutdown
- [x] 3.7 Integration test: concurrent search requests against the threaded server produce exactly one load and one release

## 4. Single-Instance Enforcement

- [x] 4.1 Add an instance-lock helper acquiring `fcntl.flock(LOCK_EX | LOCK_NB)` on `<data_dir>/.rag-instance.lock` and writing pid + start time
- [x] 4.2 Acquire the lock during service/server startup and release it on normal exit
- [x] 4.3 Fail fast with an actionable message naming the owning pid and data directory when the lock is held
- [x] 4.4 Add an `--allow-multi-instance` CLI flag that downgrades the failure to a warning
- [x] 4.5 Tests: second acquisition is rejected; lock left by a dead process is acquirable; lock released after normal exit

## 5. Index Provenance & Automatic Reindex

- [x] 5.1 Define and persist the provenance manifest (`<data_dir>/index_meta.json`) recording provider, model, dimension, and `embedded_at` per repository
- [x] 5.2 Write provenance on every successful index/re-embed and expose it through the status interface
- [x] 5.3 Implement mismatch detection comparing configured provider/model/dimension against the manifest, run at startup and before serving search
- [x] 5.4 Implement the re-embed pass that rebuilds dense vectors from stored chunks while preserving repository registrations, symbol graph, and BM25 index
- [x] 5.5 Make the re-embed resumable per repository and report progress through the existing indexing progress/SSE channel
- [x] 5.6 Add the `reindexing` guard so concurrent searches wait or return "reindexing in progress" instead of scoring against mixed vector spaces
- [x] 5.7 Backfill behavior for indexes with no manifest: adopt current configuration when the dimension matches, otherwise reindex
- [x] 5.8 Tests: mismatch triggers reindex, matching provenance skips it, provenance updated after rebuild, search during rebuild returns the guarded response

## 6. Default Model Switch to 0.6B

- [x] 6.1 Change the default embedding model to `qwen3-embedding:0.6b` in `src/service.py`, `src/indexer/embeddings.py` (`OllamaEmbeddingEngine` and `EmbeddingFactory`), and `src/cli/main.py`
- [x] 6.2 Verify explicit `OLLAMA_EMBEDDING_MODEL` / `--embedding-model` overrides still take precedence over the new default
- [x] 6.3 Ensure a missing default model reports the exact `ollama pull` command and offers auto-pull
- [x] 6.4 Update existing tests referencing `qwen3-embedding:4b`/`8b` defaults in `tests/test_ollama_integration.py`
- [x] 6.5 Run a retrieval sanity check on the existing fixtures under `fixtures/` comparing 0.6b results against the prior model and record the outcome

## 7. Documentation & Verification

- [x] 7.1 Update `README.md` for the new default model, on-demand lifecycle, `EMBEDDING_KEEP_ALIVE` policy, and single-instance requirement
- [x] 7.2 Document the automatic reindex-on-upgrade behavior and the rollback path from `design.md` — Migration Plan
- [x] 7.3 Run the full `pytest` suite plus `black`/`ruff` and fix regressions
- [x] 7.4 Manual end-to-end verification: start server (no model resident) → run a search (model loads) → idle past the grace period (model released) → confirm via `ollama ps`
