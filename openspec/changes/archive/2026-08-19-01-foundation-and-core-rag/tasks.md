# Implementation Tasks: Foundation & Core Multi-Repo Code RAG

- **Change ID**: `01-foundation-and-core-rag`
- **Status**: COMPLETED

---

## Task Checklist

### Phase 1: Environment & Project Setup
- [x] Create OpenSpec configuration (`openspec/config.json`)
- [x] Write living specifications in `openspec/specs/`
- [x] Write proposal, design, and tasks in `openspec/changes/01-foundation-and-core-rag/`
- [x] Set up `pyproject.toml` / `requirements.txt` with required dependencies
- [x] Define core Pydantic/dataclass data schemas in `src/models/schema.py`

### Phase 2: Ingestion & AST Parsing
- [x] Implement `RepoManager` in `src/ingestion/repo_manager.py` (file scanning, ignore filtering, hash tracking)
- [x] Implement `ASTChunker` in `src/parser/ast_chunker.py` (Tree-sitter/AST multi-language semantic chunking & context injection)
- [x] Implement `SymbolExtractor` in `src/parser/symbol_extractor.py` (functions, classes, calls, imports, cross-repo APIs)

### Phase 3: Hybrid Indexing & Storage
- [x] Implement `VectorStore` in `src/indexer/vector_store.py` (embeddings + vector database)
- [x] Implement `LexicalStore` in `src/indexer/lexical_store.py` (BM25 tokenization & indexing)
- [x] Implement `GraphStore` in `src/indexer/graph_store.py` (SQLite symbol and call graph)

### Phase 4: Retrieval, Graph Expansion & Reranking
- [x] Implement `HybridRetriever` in `src/retriever/hybrid_retriever.py` (RRF fusion & symbol boosting)
- [x] Implement `ContextPackager` in `src/retriever/reranker.py` (chunk deduplication & citation formatting)

### Phase 5: RAG Generator & Interfaces
- [x] Implement `RAGGenerator` in `src/generator/rag_engine.py` (multi-provider LLM synthesis with grounded citations)
- [x] Implement HTTP server in `src/server/api.py` (REST endpoints & SSE streaming chat)
- [x] Implement MCP Server in `src/mcp/server.py` (Model Context Protocol tool definitions)
- [x] Implement CLI in `src/cli/main.py` and `main.py`
- [x] Implement Web UI in `web/index.html`, `web/style.css`, `web/app.js`

### Phase 6: Testing & Validation
- [x] Create multi-repo test fixtures (`fixtures/repo_auth_service`, `fixtures/repo_web_client`, `fixtures/repo_shared_schemas`)
- [x] Implement unit & integration test suite (`tests/test_ast_chunker.py`, `tests/test_symbol_graph.py`, `tests/test_hybrid_retriever.py`, `tests/test_mcp_server.py`, `tests/test_e2e_rag.py`)
- [x] Run test suite and verify end-to-end multi-repo RAG workflow (13/13 tests passing)
