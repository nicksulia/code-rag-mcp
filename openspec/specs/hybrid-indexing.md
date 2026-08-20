# Specification: Advanced Multi-Vector & BM25F Hybrid Indexing

## Status: ACTIVE
## Domain: Indexing & Storage
## Version: 2.2.0

---

## 1. Overview
The Hybrid Indexing system combines multi-vector dense semantic embeddings with field-weighted BM25F lexical ranking and Code Graph Centrality. It natively supports **Ollama with `qwen3-embedding:0.6b`** (default, with larger Qwen3 variants selectable) for local dense vector generation alongside Google Gemini, OpenAI, and high-dimensional offline subword embeddings.

---

## 2. Requirements

### 2.1 Dense Vector Representation
- **Dual Vector Architecture**: Each code chunk is indexed with:
  1. **Signature & Intent Vector ($V_{\text{sig}}$)**: Function name, parent class/module, parameter types, return signatures, docstrings, and headers.
  2. **Implementation Logic Vector ($V_{\text{body}}$)**: Structural code statements, variables, and internal call flows.
- **Composite Similarity**:
  $$S_{\text{dense}}(Q, D) = 0.6 \cdot \cos(V_Q, V_{\text{sig}}) + 0.4 \cdot \cos(V_Q, V_{\text{body}})$$

### 2.2 Pluggable Embedding Engines
- **Ollama Engine (`OllamaEmbeddingEngine`) [DEFAULT]**:
  - Primary default code embedding model: **`qwen3-embedding:0.6b`** (superseding the previous `qwen3-embedding:4b` default; larger models such as `qwen3-embedding:8b`, `bge-m3`, `nomic-embed-text` remain selectable via configuration, and an explicitly configured model always takes precedence over the default).
  - Endpoint: `http://localhost:11434/api/embed` (with fallback to `/api/embeddings`).
  - Model presence check & auto-pull on startup; if the default model is missing locally, the system reports it with the exact pull command and offers auto-pull instead of failing with an opaque error.
- **Offline Subword Engine (`AdvancedSubwordNeuralEngine`)**: 384-dimensional positional syntax-weighted embedding engine (automatic offline fallback when Ollama is unreachable).
- **Remote Cloud Providers**: Google Gemini (`text-embedding-004`), OpenAI (`text-embedding-3-small/large`), Voyage AI (`voyage-code-3`).

### 2.3 BM25F (Field-Weighted Lexical Ranking)
- Specialized weights: `symbol_name` ($4.0\times$), `signature` ($2.5\times$), `docstring` ($2.0\times$), `code_body` ($1.0\times$).

### 2.4 Code Graph Centrality
- Evaluates in-degree and cross-repo referencing to boost core shared libraries and critical endpoints.

### 2.5 Lazy Embedding Dimension Resolution
- The active embedding model's vector dimension is determined without loading the model at startup: no dimension probe request is issued until an embedding or search operation actually runs.
- The dimension is resolved from persisted index metadata when available, configuring the vector store without contacting the model runtime; otherwise it is measured once on first embedding use, then cached and persisted with the index for the lifetime of the configured model selection.

### 2.6 Embedding Provenance & Automatic Reindex On Mismatch
- The dense index persists the embedding provider, model identifier, and vector dimension used to build it, and exposes that provenance through the status interface.
- When the configured provider, model identifier, or resulting vector dimension differs from the recorded provenance, the existing dense vectors are treated as invalid: the system automatically re-embeds and rebuilds the dense index for the affected repositories before serving search results.
- The automatic reindex reports progress through the existing indexing progress channels, preserves repository registrations, symbol graph data, and lexical index data, and updates the recorded provenance on completion.
- Search requests arriving during a mismatch-triggered rebuild either wait for the rebuild or return a clear "reindexing in progress" response, rather than mixing incompatible vectors. When provenance already matches configuration, no reindex is triggered.
