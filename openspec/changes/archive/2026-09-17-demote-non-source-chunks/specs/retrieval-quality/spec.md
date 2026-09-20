# Spec Delta

## Purpose

Keeps hybrid search results focused on source code and documentation by classifying indexed chunks by file path and soft-demoting non-source chunks (tests, fixtures, mocks, snapshots) during RRF fusion, so general queries surface implementations above test artifacts.

## ADDED Requirements

### Requirement: Path-based chunk classification

The system SHALL classify every retrieved chunk into exactly one class — `source`, `test`, `fixture`, or `noise` — derived solely from the chunk's file path. Classification SHALL match on whole path segments and filename boundaries, never on unanchored substrings. Path separators SHALL be normalized to `/` before matching, so repositories indexed on Windows (where `file_path` contains `\`) classify identically to POSIX. The rule set SHALL cover the test conventions of every language the indexer supports, including at minimum: Python (`test_*.py`, `*_test.py`, `conftest.py`, `tests/`), Go (`*_test.go`, `testdata/`), JavaScript/TypeScript (`*.test.*`, `*.spec.*`, `__tests__/`), Java (`src/test/`, `*Test.java`, `*Tests.java`, `*IT.java`), C/C++ (`test_*.c`, `test_*.cpp`, `*_test.cc`, `*_test.cpp`, `*_unittest.cc`, `test/`, `tests/`), Rust (`tests/`, `benches/`), C# (`*Tests.cs`, `*.Tests/` projects), Ruby (`spec/`, `*_spec.rb`), PHP (`*Test.php`), Solidity (`*.t.sol`), and Bash (`test_*.sh`, `*.bats`). The rule set MAY additionally include forward-looking conventions for languages the indexer does not yet parse (e.g. Kotlin `*Test.kt`, Gherkin `*.feature`); such rules are inert until those languages are indexed. Mock files (`mock_*`, `*_mock.*`, `**/mocks/`, `**/__mocks__/`), snapshots (`*.snap`, `__snapshots__/`), and Storybook stories (`*.stories.*`) SHALL classify as `noise`; fixture and test-data directories (`fixtures/`, `testdata/`) SHALL classify as `fixture`.

#### Scenario: Test file is classified as test

- **WHEN** a chunk with file path `tests/test_catalog.py` is retrieved
- **THEN** the chunk is classified as `test`

#### Scenario: Source paths containing "test" substrings are not misclassified

- **WHEN** chunks with file paths `src/context/manager.py` or `docs/latest/guide.md` are retrieved
- **THEN** both chunks are classified as `source`

#### Scenario: Mock file is classified as noise

- **WHEN** a chunk with file path `internal/store/mocks/mock_user_store.go` is retrieved
- **THEN** the chunk is classified as `noise`

#### Scenario: Most specific rule wins

- **WHEN** a chunk with file path `tests/mocks/test_helpers.py` is retrieved
- **THEN** the chunk is classified as `noise`, not `test`

#### Scenario: C++ test file is classified as test

- **WHEN** a chunk with file path `src/parser_test.cpp` is retrieved
- **THEN** the chunk is classified as `test`

#### Scenario: Windows-style path separators classify identically

- **WHEN** a chunk with file path `tests\mocks\test_helpers.py` is retrieved
- **THEN** the chunk is classified as `noise`

### Requirement: Tiered demotion during hybrid fusion

During Reciprocal Rank Fusion, the system SHALL multiply each chunk's fused score by a class penalty before final ranking. The penalties SHALL satisfy `noise < fixture < test < source` with `source` equal to 1.0, and SHALL be configurable without code changes to the ranking pipeline structure. Demotion SHALL be a soft score adjustment, never a hard filter: non-source chunks remain eligible for results.

#### Scenario: Source outranks an equally matched test

- **WHEN** a source chunk and a test chunk receive equal dense and sparse ranks for a query
- **THEN** the source chunk ranks above the test chunk in the final results

#### Scenario: A highly relevant test can still surface

- **WHEN** a test chunk strongly outranks all source chunks in both retrieval arms for a query
- **THEN** the test chunk MAY appear in the final results

#### Scenario: Penalty applies to the whole fused score

- **WHEN** a test chunk receives symbol-match and graph-centrality boosts
- **THEN** the class penalty multiplies the score after those boosts, dampening them as well

### Requirement: Example symbols are treated as source

The system SHALL treat a chunk as `source` when its path classifies as `test` but its symbol name begins with `Example`, because example functions are usage documentation.

#### Scenario: Go example function is not demoted

- **WHEN** a chunk from `catalog_test.go` has symbol name `ExampleCreateCatalog`
- **THEN** the chunk is ranked with the `source` penalty of 1.0

### Requirement: Example directories are never demoted

The system SHALL classify paths under `examples/` directories as `source`, since they contain documentation rather than test artifacts.

#### Scenario: Example directory file ranks as source

- **WHEN** a chunk with file path `examples/basic_usage.py` is retrieved
- **THEN** the chunk is classified as `source`

### Requirement: Query-time derivation with no reindexing

Classification and demotion SHALL be computed at query time from data already present on indexed chunks, so the behavior applies to repositories indexed before this change without reindexing, schema migration, or re-embedding.

#### Scenario: Existing index reflects demotion immediately

- **WHEN** a search runs against a repository indexed before this change shipped
- **THEN** test, fixture, and noise chunks from that repository are demoted in the results

### Requirement: Inline test constructs rank as source

Because classification is path-based, test code embedded inside source files — including Rust `#[cfg(test)]` modules and Python doctests — SHALL classify as `source` and remain undemoted. This is a recorded limitation, not a filtering guarantee.

#### Scenario: Rust inline test module is not demoted

- **WHEN** a chunk from `src/parser.rs` contains only a `#[cfg(test)]` test module
- **THEN** the chunk is classified as `source`
