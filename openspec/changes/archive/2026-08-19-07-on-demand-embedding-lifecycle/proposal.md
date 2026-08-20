## Why

The RAG engine runs as a single long-lived instance, but the Ollama embedding model (`qwen3-embedding:4b`) stays resident in VRAM/RAM for the entire process lifetime — it is only released on server shutdown or an explicit `/api/v1/models/unload` call. On a workstation that also runs the code LLM, IDEs, and other GPU workloads, several gigabytes are pinned while the engine sits idle. Embedding is used in short bursts (indexing runs and per-query search), so the model should be resident only for the duration of those bursts, and the default model should be the much smaller `qwen3-embedding:0.6b`.

## What Changes

- **Default embedding model reduced to `qwen3-embedding:0.6b`** across the service, CLI defaults, factory fallbacks, and documentation. **BREAKING**: dense vector dimension changes (2560 → 1024), invalidating existing dense indexes.
- **Dimension-mismatch detection with automatic reindex**: on startup and before any embedding/search operation, the vector store compares its persisted dimension and model identity against the active engine; on mismatch the affected repositories are automatically re-embedded and the dense index rebuilt, with progress surfaced through existing CLI/API/SSE reporting.
- **On-demand model lifecycle**: the embedding model is no longer warmed or held at process start. It is loaded on entry to an embedding or search operation and unloaded when the operation completes and no other operation is in flight.
- **Single-instance enforcement**: the engine acquires an exclusive instance lock so only one process owns the embedding lifecycle; a second instance fails fast with a clear message rather than duplicating a resident model.
- **Reference-counted, thread-safe activation**: concurrent search/index requests within the single instance share one load and trigger exactly one unload after the last one finishes, with a short configurable idle grace period to avoid load/unload thrashing on bursty traffic.
- **Deferred dimension probing**: the startup probe that currently loads the model just to measure its dimension is replaced by a cached, on-first-use probe so importing/starting the service never pulls the model into memory.
- **Configurable policy**: `EMBEDDING_KEEP_ALIVE` / idle-grace and an `always-resident` opt-out let users restore the old behavior for throughput-heavy indexing.

## Capabilities

### New Capabilities
- `embedding-model-lifecycle`: Single-instance ownership of the embedding model, on-demand load/unload around embedding and search operations, reference counting and idle grace, and the runtime policy controls that govern it.

### Modified Capabilities
- `hybrid-indexing`: Default Ollama embedding model becomes `qwen3-embedding:0.6b`; dimension probing becomes lazy rather than a startup-time load; the dense index records the model identity and dimension it was built with and requires automatic reindex on mismatch.

## Impact

- **Code**: `src/indexer/embeddings.py` (`OllamaEmbeddingEngine` probe/load/unload, `EmbeddingFactory` defaults), `src/indexer/vector_store.py` (persisted model/dimension metadata, mismatch detection), `src/service.py` (defaults, lifecycle wrapping of index/search entry points, instance lock), `src/server/api.py` (lifecycle around request handling, unload endpoint semantics, startup lock), `src/cli/main.py` (default model, `ensure_model_ready` no longer forces a resident load), `src/mcp/` (tools go through the same lifecycle wrapper).
- **APIs**: `/api/v1/models/unload` remains but becomes a manual override of an already-automatic behavior; search/index responses may report a reindex being triggered.
- **Data**: existing `data/` dense indexes built with 4b/8b models are rebuilt on first run after upgrade; repository registrations, symbol graph, and BM25 index are unaffected except for the re-embedding pass.
- **Operations**: first search after idle pays a model load latency (small for 0.6b); running two engine instances against the same data dir is now rejected.
- **Docs**: `README.md` model defaults and setup instructions.
