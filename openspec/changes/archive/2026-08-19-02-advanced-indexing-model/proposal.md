# Change Proposal: Advanced Multi-Vector & BM25F Indexing Architecture

- **Change ID**: `02-advanced-indexing-model`
- **Author**: Antigravity Engineering
- **Status**: IN_PROGRESS
- **Created**: 2026-08-17

---

## 1. Why (Motivation)
Basic code search indexes code chunks as flat text blobs with naive keyword weighting. This creates two problems:
1. **Semantic Loss**: The high-level intent (function signature, return types, docstring) is drowned out by long implementation details (loops, error handling, local variables).
2. **Keyword Imprecision**: Searching for a specific function name (e.g. `verify_credentials`) gives equal lexical weight whether the term appears as the function identifier or as a generic variable/comment.

We need an **Advanced Multi-Vector & BM25F Indexing Engine** that separates Signature/Intent from Implementation Logic, applies field-weighted BM25F scoring, leverages cross-repo Code Graph Centrality, and supports pluggable external neural code embeddings.

---

## 2. Scope & Requirements

### In Scope
- [x] Dual-vector dense indexing (Signature Vector + Body Vector) with 384-dimensional syntax-weighted representations.
- [x] Pluggable embedding providers (`AdvancedSubwordNeuralEngine`, Gemini, OpenAI, Voyage AI, Ollama).
- [x] BM25F (Field-Weighted BM25) across `symbol_name`, `signature`, `docstring`, and `code_body`.
- [x] Code Graph Centrality scoring to highlight key shared components and API contracts.
- [x] Backward-compatible database migration and re-syncing.

---

## 3. Impact Analysis
- **Performance**: Dense similarity and BM25F queries execute in < 5ms per query.
- **Dependencies**: Uses high-performance pure-Python math with pluggable HTTP bindings for remote LLM/embedding providers.
