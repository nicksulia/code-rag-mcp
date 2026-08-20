# Implementation Tasks: Advanced Indexing Model

- **Change ID**: `02-advanced-indexing-model`
- **Status**: COMPLETED

---

## Tasks

- [x] Implement `src/indexer/embeddings.py` (BaseEmbeddingEngine, AdvancedSubwordNeuralEngine with 384-dim, Gemini/OpenAI/Ollama adapters, EmbeddingFactory)
- [x] Upgrade `src/indexer/vector_store.py` to support dual vectors (`sig_vector`, `body_vector`), 384-dim, and composite similarity scoring
- [x] Upgrade `src/indexer/lexical_store.py` to BM25F with field weights (`symbol_name`, `signature`, `docstring`, `code_body`)
- [x] Upgrade `src/indexer/graph_store.py` with `get_symbol_centrality`
- [x] Upgrade `src/retriever/hybrid_retriever.py` with BM25F + multi-vector + Code Graph Centrality fusion
- [x] Update `src/service.py` to coordinate the new indexer and support auto-migration
- [x] Create `tests/test_advanced_indexing.py` and run full regression test suite (17/17 tests passing)
