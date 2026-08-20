## Context

See `proposal.md` — Why. Current state relevant to the approach:

- `MultiRepoRAGService.__init__` (`src/service.py`) builds `OllamaEmbeddingEngine` via `EmbeddingFactory`, whose constructor calls `_probe_dimension()` — an immediate `/api/embed` call that pulls the model into VRAM at process start, before any user work.
- `VectorStore` is constructed with the engine and takes its dimension at construction time; there is no persisted record of which model produced the stored vectors.
- Release exists but is manual and coarse: `service.unload_models()` (POST `/api/v1/models/unload`, CLI `unload`, server shutdown) sends `keep_alive: 0` via `/api/generate`.
- The HTTP server is `ThreadedHTTPServer`, so search and index requests are concurrent; MCP stdio and CLI paths share the same `MultiRepoRAGService`.
- Embedding entry points funnel through the engine's `embed`/batch methods used by `VectorStore` (indexing) and `HybridRetriever` (query embedding).

Requirements are in `specs/embedding-model-lifecycle/spec.md` and `specs/hybrid-indexing/spec.md`.

## Goals / Non-Goals

**Goals:**
- One choke point that owns load/release, so every caller (HTTP, SSE, CLI, MCP) inherits the behavior without per-call-site changes.
- Correct behavior under the existing thread-per-request server: no double loads, no premature unload, no leaked reference counts on exceptions.
- A safe, automatic path across the 2560 → 1024 dimension break with no manual operator steps.
- No new third-party dependencies (project uses stdlib `urllib` for Ollama).

**Non-Goals:**
- Changing the LLM generator's lifecycle. `RAGGenerator` already unloads with `keep_alive: 0`; it keeps its current behavior and is only touched where it shares the unload endpoint.
- Managing the Ollama server process itself (starting/stopping `ollama serve`).
- Cross-machine or multi-node coordination; the instance lock is per data directory on one host.
- Re-tuning retrieval weights for the smaller model's quality characteristics.

## Decisions

### 1. Residency is controlled by Ollama `keep_alive`, not by a resident client object

Each embedding request sends `keep_alive: <policy>` on `/api/embed` (and the `/api/embeddings` fallback), and release is an explicit `keep_alive: 0` call. "Load" therefore means "issue the first request with a keep-alive window"; there is no separate warm-up call to pay for.

- *Alternative — explicit warm-up request then unload*: adds a wasted forward pass per burst and a window where the model is loaded but idle.
- *Alternative — rely solely on Ollama's default 5-minute keep_alive*: does not satisfy the release requirements, is invisible to the status interface, and is not configurable per-workload.

`keep_alive` is set to the configured idle grace (default `"30s"`) so that even a crashed instance's model self-evicts; the explicit `keep_alive: 0` on release is the primary mechanism and the timer is the backstop.

### 2. An `EmbeddingSession` context manager wraps operations, with reference counting

A small lifecycle object owns: a `threading.RLock`, an `active` counter, and a cancellable release timer.

```
with service.embedding_session("search:query"):
    ...embed and retrieve...
```

Entry increments the counter and cancels any pending release timer; exit decrements under `try/finally` and, at zero, arms a `threading.Timer` for the idle grace which fires the `keep_alive: 0` release. Wrapping the *operation* rather than each `embed()` call is what makes an indexing run a single residency window instead of thousands.

Wrapped entry points: `index_repository` / sync, `search`, `query`/`chat` (streaming generators wrapped for their whole generator lifetime, not just first yield), and the MCP tool handlers.

- *Alternative — decorate `embed_text`/`embed_batch`*: would load and release around every chunk during indexing.
- *Alternative — asyncio-based lifecycle*: the server is thread-based; threads keep this consistent with the existing code.

### 3. Single-instance enforcement via an OS-level lock file in the data directory

`<data_dir>/.rag-instance.lock` is opened and locked with `fcntl.flock(LOCK_EX | LOCK_NB)`, and the owning pid plus start time are written into it. The kernel releases the lock when the process dies, so an abnormal exit leaves a stale *file* but not a stale *lock* — the pid in the file is used only for the error message. A `--allow-multi-instance` escape hatch downgrades the failure to a warning for read-only tooling.

- *Alternative — pid file with liveness check*: racy and needs manual stale cleanup.
- *Alternative — binding a TCP port as the mutex*: couples instance identity to the HTTP server, so CLI and MCP runs would not participate.

### 4. Dimension is resolved from persisted metadata first, probed lazily second

`OllamaEmbeddingEngine.__init__` no longer probes. `dimension` becomes a lazy property: it returns the cached value, else the value recorded in the index manifest for the same provider+model, else it performs one probe inside an embedding session. `VectorStore` is constructed with a *lazy dimension provider* rather than an eager integer so that constructing the service never touches the runtime.

### 5. Index provenance manifest drives automatic reindex

A manifest (`<data_dir>/index_meta.json`, or an equivalent record alongside the existing vector store files) stores `{provider, model, dimension, embedded_at}` per repository. On startup and before serving any search, the service compares the manifest to the active configuration:

- match → proceed;
- mismatch or missing → mark affected repositories stale, then re-embed from stored chunks (chunking, symbol graph, and BM25 index are model-independent and are preserved, so the rebuild is an embedding pass only, not a re-parse);
- the rebuild runs inside one embedding session and reports through the existing indexing progress/SSE channel;
- a `reindexing` flag makes concurrent searches either await completion or return the "reindexing in progress" status the spec requires.

- *Alternative — dimension-only check*: two different models can share 1024 dims, silently mixing incompatible vector spaces.
- *Alternative — fail with manual reindex instructions*: rejected in favor of the automatic path chosen with the user.

### 6. `always-resident` mode is a policy on the same object

`EMBEDDING_KEEP_ALIVE` (duration string, `0` for immediate release, `-1`/`always` for resident) plus a matching CLI flag configures the lifecycle object; always-resident simply never arms the release timer and sends `keep_alive: -1`. Manual `/api/v1/models/unload` releases immediately when `active == 0` and returns a busy response otherwise.

## Risks / Trade-offs

- **Per-burst load latency on the first search after idle** → 0.6b loads in well under a second on typical hardware; the idle grace (default 30s) means interactive sessions load once, and always-resident mode is available for throughput-heavy work.
- **Retrieval quality drop from 4b → 0.6b** → measured with the existing test fixtures before rollout; larger models remain a one-line configuration change, and provenance-driven reindex makes switching back safe.
- **Long automatic reindex on first run after upgrade** → embedding-only pass over existing chunks (no re-parse), progress reported, and the reindex is resumable per repository so an interruption does not restart from zero.
- **Reference-count leak wedging the model resident (or a release firing mid-operation)** → all increments/decrements go through the context manager's `try/finally`; unit tests cover exception paths, generator abandonment, and interleaved load/release; the Ollama-side `keep_alive` timer bounds the damage of any residual leak.
- **Release timer firing between two rapid indexing runs** → the grace period plus timer cancellation on entry absorbs normal gaps; worst case is one extra load.
- **`fcntl.flock` semantics on network filesystems** → data directories are expected to be local; the lock failure message states the data directory so a misconfigured shared path is diagnosable, and `--allow-multi-instance` unblocks the user.
- **Stale-lock false positive blocking a legitimate restart** → kernel-released `flock` avoids this by construction; the error message includes the recorded pid so the user can verify.

## Migration Plan

1. Ship lifecycle + lock + lazy probe first; defaults keep the existing model, so behavior change is memory-only and independently verifiable.
2. Add provenance manifest writing and mismatch detection; on first run, an index with no manifest is treated as unknown provenance and backfilled from the current configuration if the dimension matches, otherwise reindexed.
3. Flip the default model to `qwen3-embedding:0.6b` and update docs; the first start after this triggers the automatic reindex.
4. **Rollback**: set `OLLAMA_EMBEDDING_MODEL=qwen3-embedding:4b` and `EMBEDDING_KEEP_ALIVE=always` to restore prior behavior end to end; the provenance check then reindexes back to the 4b vector space with no code change.
