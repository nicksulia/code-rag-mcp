# Tasks

## 1. Path classifier module

- [x] 1.1 Create `src/retriever/path_classifier.py` with a `ChunkClass` enum (`SOURCE`/`TEST`/`FIXTURE`/`NOISE`), an ordered `RULES` table of `(compiled_regex, ChunkClass)` covering the conventions listed in the spec (Python, Go, JS/TS, Java, C/C++, Rust, C#, Ruby, PHP, Solidity, Bash, plus forward-looking Kotlin/Gherkin; mocks/snapshots/stories as `noise`, `fixtures/`+`testdata/` as `fixture`), and `classify(file_path) -> ChunkClass` that normalizes `\` to `/` before matching and defaults to `SOURCE`. Verify: module imports cleanly and `classify` returns `SOURCE` for an unmatched path.
- [x] 1.2 Add `tests/test_path_classifier.py` with a path→expected-class table covering every spec scenario: `tests/test_catalog.py`→test, `src/context/manager.py` and `docs/latest/guide.md`→source (anchoring), `internal/store/mocks/mock_user_store.go`→noise, `tests/mocks/test_helpers.py`→noise (most-specific-wins), `examples/basic_usage.py`→source, plus one representative convention per supported language (`*_test.go`, `*.test.ts`, `*.spec.js`, `src/test/java/...`, `*Tests.cs`, `*_spec.rb`, `*Test.php`, `*.t.sol`, `test_*.sh`, `*.feature`, `conftest.py`, `benches/`), plus `src/parser_test.cpp`→test, `foo_unittest.cc`→test (C/C++ coverage), a Windows-separator path `tests\\mocks\\win_helpers.py`→noise, positive fixture cases `tests/fixtures/user_payload.json`→fixture and `internal/testdata/golden.json`→fixture, and spec-mandated noise cases `__snapshots__/button.snap`→noise and `src/Button.stories.tsx`→noise. Verify: `pytest tests/test_path_classifier.py` passes.

## 2. Retriever integration

- [x] 2.1 Add `test_penalty=0.65`, `fixture_penalty=0.5`, `noise_penalty=0.4` constructor params to `HybridRetriever` (alongside `hop_decay`). Verify: existing constructions in `src/service.py` still work unchanged (defaults applied).
- [x] 2.2 In the fusion loop of `hybrid_retriever.py`, after boosts/centrality and beside the `hop_decay` line, apply `rrf *= penalty` where penalty comes from `classify(chunk.file_path)`; treat class `TEST` with `(chunk.symbol_name or "").startswith("Example")` as penalty 1.0 (guard required: `symbol_name` is `Optional`, `None` for module-level chunks). Verify: unit test with stubbed dense/sparse results shows an equally-ranked source chunk above a test chunk, and an `Example*` chunk in a `_test.go` path undemoted.
- [x] 2.3 Verify soft demotion, not filtering: a unit test where a test chunk dominates both retrieval arms still appears in results.

## 3. Validation

- [x] 3.1 Run the full test suite (`pytest tests/`) and confirm no regressions.
- [x] 3.2 Smoke-test against an existing index without reindexing: run a `search_codebases` query that previously surfaced test files, and confirm source chunks now rank above test chunks while test chunks still appear when strongly relevant.
