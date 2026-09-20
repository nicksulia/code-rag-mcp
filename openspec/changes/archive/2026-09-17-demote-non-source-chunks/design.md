# Design

## Context

See proposal.md — Why. Relevant current state: `HybridRetriever.search()` in `src/retriever/hybrid_retriever.py` fuses dense and sparse candidates via RRF, applies a `+0.020` symbol-name boost, a graph-centrality boost, and a multiplicative `hop_decay` demotion for expanded repos. Every candidate in the fusion loop already carries `file_path` and `symbol_name`. `CodeChunk` and the `chunks_v2` table have no test/metadata field, and ingestion (`repo_manager.py` ignore lists) excludes neither tests nor mocks.

## Goals / Non-Goals

**Goals:**

- Demote test/fixture/noise chunks at query time with no schema migration, no reindex, and no ingestion changes.
- One classification mechanism, data-driven, covering all languages the indexer supports.
- Penalties tunable as constructor knobs, mirroring the existing `hop_decay` pattern.

**Non-Goals:**

- Query-intent routing (suppressing vs. boosting tests per query intent) — deferred; becomes a smaller decision once penalties exist.
- Diversity caps on non-source chunks in final results — deferred.
- Ingestion-time exclusion of mocks/fixtures — deferred unless candidate-pool crowding is observed.
- Chunk-level (content-based) detection of inline test modules — recorded limitation, see specs.

## Decisions

### 1. Derive classification at query time, don't persist it

Classify from `file_path` inside the fusion loop. Alternative considered: add `is_test` to `CodeChunk` + `chunks_v2` (ALTER + full reindex + re-embedding). Rejected because path conventions cover the vast majority of layouts, derivation works on every existing index the moment the code ships, and penalty tuning stays free of re-embedding costs. Persistence can be added later if a non-path signal is ever needed.

### 2. A separate `path_classifier.py` module with a data-driven rule table

`classify(file_path) -> ChunkClass` over an ordered `RULES: list[(compiled_regex, ChunkClass)]` table — data, not per-language dispatch logic. Cross-language collisions are practically impossible with anchored patterns (`*_test.go` cannot match a Ruby file), so the chunk's `language` field is not consulted. A separate module keeps the retriever diff to ~10 lines and makes the table unit-testable as path→expected-class cases.

### 3. Anchored matching, ordered rules, most specific first

Directory patterns match whole path segments (`(^|/)tests?/`), file patterns anchor to the basename (`^test_.*\.py$`, `_test\.go$`) — never unanchored substrings, so `src/context/` and `docs/latest/` stay `source`. Rule order: `noise` → `fixture` → `test`, with `source` as the default, so `tests/mocks/` resolves to `noise`. Directory segments match case-insensitively; CamelCase filename patterns (`*Test.java`, `*Tests.cs`) match case-sensitively to avoid false hits on ordinary lowercase filenames. `file_path` is normalized to forward slashes before matching — ingestion stores `str(Path.relative_to(...))`, which contains `\` on Windows and would silently defeat every segment anchor.

### 4. Tiered multiplicative penalties in the fusion loop

Defaults: `noise 0.4`, `fixture 0.5`, `test 0.65`, `source 1.0` — within the 0.6–0.7 band recommended for tests in the motivating analysis, with mocks/fixtures demoted harder for repeating interface symbols without implementation value. Applied as `rrf *= penalty` immediately after boosts/centrality and beside the existing `hop_decay` line, so demotion dampens the symbol boost tests illegitimately earn. Exposed as `HybridRetriever` constructor params alongside `hop_decay`. Alternative considered: hard-filtering non-source chunks — rejected; a fixture that is genuinely the best match must still be able to surface.

### 5. Example carve-out at fusion time, not in the classifier

Go `Example*` functions live in `_test.go` files, so path classification alone would demote usage documentation. The fusion loop already has `chunk.symbol_name`; `if class == test and symbol_name.startswith("Example"): penalty = 1.0` is one condition there rather than symbol-awareness leaking into a pure path classifier. `examples/` directories are simply absent from the rule table and classify as `source`.

## Risks / Trade-offs

- Exotic repo layouts misclassify (e.g. a source directory literally named `tests/`, or a utility module named `mock_retry.py`) → demotion is soft, never a filter; misclassified code can still rank; patterns are one-line data edits if a false positive is reported. (A directory named `testing/` does NOT misclassify — segment-anchored `tests?/` never matches it.)
- Mock-heavy repos may crowd the `top_k * 3` candidate pools before fusion → monitor after rollout; escape hatch is a pre-fusion drop of the `noise` class or deferred ingestion-time exclusion, both compatible with this design.
- Penalty values (0.4/0.5/0.65) are starting points without an eval harness → ship within the recommended band and tune on observed queries; a golden-query eval set is a possible follow-up but out of scope.
- Rule table grows stale as ecosystems evolve → the unit-test table of path→class doubles as an executable contract; adding a convention is a one-line change.

## Migration Plan

None. Code-only deploy: existing indexes, embeddings, and the `chunks_v2` schema are untouched, and demotion applies to previously indexed repositories on the next search. Rollback is a revert; ranking returns to current behavior.
