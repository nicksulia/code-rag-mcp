# Proposal

## Why

Test files suffer from "RRF rank hijacking" in hybrid search: they dual-match both retrieval arms (BM25 on exact symbol/error strings, dense embeddings on behavioral descriptions), and Reciprocal Rank Fusion disproportionately rewards documents that rank well in both lists. As a result, test chunks frequently outrank the actual source code and documentation in `search_codebases` results, filling prompt context with boilerplate, mocks, and fixture noise instead of core implementations. Mocks are the worst offenders — a `MockUserStore` repeats the exact symbol names of the real interface and steals the symbol-name boost. Today nothing in the pipeline distinguishes test code from source code: `CodeChunk` carries no such metadata, and the ingestion ignore lists exclude neither `tests/` nor `mocks/`.

## What Changes

- Add a path-based chunk classifier (`src/retriever/path_classifier.py`) that maps a chunk's `file_path` to one of four classes — `source`, `test`, `fixture`, `noise` — using an ordered, anchored rule table covering the test conventions of every language the indexer supports (Python, TS/JS, Go, Rust, Java, C/C++, C#, Ruby, PHP, Solidity, Bash), plus forward-looking Kotlin/Gherkin rules that stay inert until those languages are parsed.
- Apply a tiered multiplicative penalty in the RRF fusion loop of `hybrid_retriever.py` (e.g. noise 0.3–0.5x, fixture 0.5x, test 0.6–0.7x, source 1.0x), alongside the existing `hop_decay` demotion. Penalty values are constructor-tunable knobs.
- Carve out Go `Example*` symbols: chunks whose class is `test` but whose `symbol_name` starts with `Example` are treated as source (they are high-value usage documentation).
- Never demote `examples/` directories — they are documentation, not tests.
- No schema migration, no reindexing, no ingestion changes: classification is derived from `file_path` at query time and applies to existing indexes immediately.
- Known limitation (recorded, not solved): Rust inline `#[cfg(test)]` modules and Python doctests live inside source files and are invisible to path-based classification.

## Capabilities

### New Capabilities

- `retrieval-quality`: Classification of indexed chunks by file path and tiered demotion of non-source chunks (tests, fixtures, mocks, snapshots) during hybrid RRF fusion, so source and documentation rank above test artifacts for general queries.

### Modified Capabilities

- None in this delta. Active main specs exist under `openspec/specs/`; the closest, `context-fusion-reranking.md` (§2.1 RRF scoring), describes the same fusion loop this change extends. It is intentionally left unmodified here to keep the delta self-contained, but it MUST be synced when this change archives — and it is already stale (it documents a +0.25 symbol boost and 0.8 sparse weight vs. the code's +0.020 and 0.85; pre-existing drift, out of scope here). `repository-relations.md`'s hop-decay requirement stays compatible: both penalties are multiplicative and applied in the same fusion-loop step, so relative ordering is immaterial.

## Impact

- **Code**: new `src/retriever/path_classifier.py` (~40 lines, data-driven rule table); `src/retriever/hybrid_retriever.py` (~10 lines: penalties + Example carve-out in the fusion loop); unit tests for the classifier.
- **Behavior**: ranking order of `search_codebases` results changes — test/mock/fixture chunks are soft-demoted, not filtered; they can still surface when genuinely the best matches.
- **APIs/schemas**: none. MCP tool surface, `CodeChunk` model, and `chunks_v2` schema are unchanged; no reindex required.
- **Deferred** (out of scope, possible follow-ups): query-intent routing (explicit MCP param or heuristic), diversity caps on non-source chunks, ingestion-time exclusion of mock/fixture files if candidate-pool crowding is observed, chunk-level detection for inline test modules (Rust `#[cfg(test)]`, doctests).
