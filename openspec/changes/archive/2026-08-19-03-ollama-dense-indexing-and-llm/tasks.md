# Implementation Tasks: Ollama Qwen3-Embedding-8B & Local LLM

- **Change ID**: `03-ollama-dense-indexing-and-llm`
- **Status**: COMPLETED

---

## Task Checklist

- [x] Implement `OllamaEmbeddingEngine` in `src/indexer/embeddings.py` (default: `qwen3-embedding:8b`, `/api/embed` + `/api/embeddings`, dynamic dimension probe)
- [x] Update `EmbeddingFactory` to support `provider="ollama"`
- [x] Implement Ollama LLM reasoning & token streaming in `src/generator/rag_engine.py` (default: `qwen2.5-coder:7b`)
- [x] Update `src/service.py` to accept `embedding_provider`, `embedding_model`, `llm_provider`, `llm_model`, and `ollama_host`
- [x] Update `src/cli/main.py` with CLI arguments (`--embedding-provider`, `--embedding-model`, `--llm-provider`, `--llm-model`, `--ollama-host`)
- [x] Create `tests/test_ollama_integration.py` covering mock embedding batching, dimension probe, and chat streaming (3 new tests)
- [x] Run full automated test suite and verify end-to-end functionality (20/20 tests passing)
