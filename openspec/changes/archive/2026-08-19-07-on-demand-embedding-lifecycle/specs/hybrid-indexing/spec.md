## ADDED Requirements

### Requirement: Default local embedding model is a 0.6B-class model
The system SHALL use `qwen3-embedding:0.6b` as the default local embedding model for dense indexing and query embedding, superseding the previous `qwen3-embedding:4b` default. Larger models SHALL remain selectable via configuration (environment variable or CLI option), and an explicitly configured model SHALL always take precedence over the default.

#### Scenario: Default model on a fresh install
- **WHEN** the engine starts with no embedding model configured
- **THEN** dense embedding and query embedding use `qwen3-embedding:0.6b`

#### Scenario: Explicit override is honored
- **WHEN** the user configures `qwen3-embedding:8b`
- **THEN** that model is used and the 0.6b default is not applied

#### Scenario: Missing default model is reported
- **WHEN** the default model is not present in the local model runtime
- **THEN** the system reports the missing model with the exact pull command and offers auto-pull, instead of failing with an opaque error

### Requirement: Embedding dimension is probed lazily and cached
The system SHALL determine the active embedding model's vector dimension without loading the model at startup. The dimension SHALL be resolved from persisted index metadata when available, or on first embedding use otherwise, and SHALL be cached for the lifetime of the configured model selection.

#### Scenario: No probe request at startup
- **WHEN** the engine starts and no embedding or search operation runs
- **THEN** no dimension probe request is issued to the model runtime

#### Scenario: Dimension resolved from persisted metadata
- **WHEN** the dense index already records a dimension for the configured model
- **THEN** the vector store is configured from that recorded dimension without contacting the model runtime

#### Scenario: Dimension probed on first use
- **WHEN** the first embedding operation runs for a model with no recorded dimension
- **THEN** the dimension is measured once, cached, and persisted with the index

### Requirement: Dense index records its embedding provenance
The dense index SHALL persist the embedding provider, model identifier, and vector dimension used to build it, and SHALL expose that provenance through the status interface.

#### Scenario: Provenance written on index build
- **WHEN** repositories are indexed with a given provider and model
- **THEN** the persisted index records that provider, model identifier, and vector dimension

#### Scenario: Provenance is reported
- **WHEN** the status interface is queried
- **THEN** it reports the embedding provider, model, and dimension the current dense index was built with

### Requirement: Embedding model mismatch triggers automatic reindex
When the configured embedding provider, model identifier, or resulting vector dimension differs from the provenance recorded in the dense index, the system SHALL treat the existing dense vectors as invalid and SHALL automatically re-embed and rebuild the dense index for the affected repositories before serving search results. The reindex SHALL report progress through the existing indexing progress channels, SHALL preserve repository registrations, symbol graph data, and lexical index data, and SHALL update the recorded provenance on completion. Search requests arriving during a mismatch SHALL either wait for the rebuild or return a clear "reindexing in progress" response rather than mixing incompatible vectors.

#### Scenario: Upgrade to the 0.6b default rebuilds vectors
- **WHEN** an engine whose dense index was built with `qwen3-embedding:4b` starts with the new `qwen3-embedding:0.6b` default
- **THEN** the affected repositories are automatically re-embedded, the dense index is rebuilt at the new dimension, and repository registrations, symbol graph, and BM25 index are retained

#### Scenario: Search during rebuild is not silently wrong
- **WHEN** a search request arrives while an automatic reindex is in progress
- **THEN** the request waits for the rebuild or returns a "reindexing in progress" status, and never scores queries against vectors from a different model

#### Scenario: Matching provenance skips reindex
- **WHEN** the configured provider, model, and dimension match the recorded provenance
- **THEN** no reindex is triggered and search proceeds against the existing dense index

#### Scenario: Provenance updated after rebuild
- **WHEN** an automatic reindex completes successfully
- **THEN** the persisted provenance names the new model and dimension, and a subsequent start triggers no further reindex
