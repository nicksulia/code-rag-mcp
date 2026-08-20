# Change Proposal: Foundation & Core Multi-Repo Code RAG System

- **Change ID**: `01-foundation-and-core-rag`
- **Author**: Antigravity Engineering
- **Status**: IN_PROGRESS
- **Created**: 2026-08-17

---

## 1. Why (Motivation)
Developers frequently work with multi-repository ecosystems (e.g. frontend web app, backend REST/gRPC API microservices, shared SDKs/types, infrastructure). Navigating code and answering cross-repository questions ("Where is this API endpoint called?", "How does auth flow across services?", "What breaks if this schema changes?") is difficult with single-repo tools.

We need a dedicated Multi-Repository Code RAG engine that understands multi-language AST structures, builds cross-repository symbol and call graphs, performs hybrid dense/sparse search, and exposes interfaces for both human developers (Web UI, CLI) and AI coding assistants (MCP server).

---

## 2. Scope & Goals

### In Scope
- [x] Multi-repository management (local folders + remote git repositories).
- [x] Tree-sitter AST parsing and semantic chunking with scope context headers.
- [x] Hybrid indexing (Dense embeddings + BM25/FTS lexical index).
- [x] Symbol table & cross-repo call graph generation in SQLite.
- [x] Reciprocal Rank Fusion (RRF) retriever & reranker.
- [x] Multi-provider LLM RAG generator with strict file/line citation grounding.
- [x] Model Context Protocol (MCP) server for Antigravity, Cursor, and Claude Code.
- [x] FastAPI REST backend + interactive Web UI dashboard.
- [x] CLI commands (`repo-rag add`, `repo-rag sync`, `repo-rag search`, `repo-rag chat`, `repo-rag serve`).

### Out of Scope (Future Changesets)
- Full distributed multi-node clustering (Phase 2).
- Real-time LSP (Language Server Protocol) dynamic runtime type inference (Phase 2).

---

## 3. Impact Analysis
- **Dependencies**: Python 3.10+, tree-sitter, lancedb / sqlite-vec, rank_bm25, fastembed / litellm, fastapi, uvicorn, mcp, click, rich.
- **Backwards Compatibility**: Initial foundation version, introduces clean spec contracts.
