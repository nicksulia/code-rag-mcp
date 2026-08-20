# Change Proposal: Ollama Qwen3-Embedding-8B & Local LLM Integration

- **Change ID**: `03-ollama-dense-indexing-and-llm`
- **Author**: Antigravity Engineering
- **Status**: IN_PROGRESS
- **Created**: 2026-08-17

---

## 1. Why (Motivation)
Developers working with sensitive enterprise codebases require a 100% locally-hosted, private RAG pipeline that does not send source code or vector embeddings over the internet.
By integrating **Ollama** using **`Qwen3-Embedding-8B`** for dense vector indexing and **`qwen2.5-coder`** for LLM generation, developers get high-accuracy code reasoning running locally on their hardware with zero external API dependencies.

---

## 2. Scope & Goals

### In Scope
- [x] `OllamaEmbeddingEngine` with `Qwen3-Embedding-8B` as the primary dense embedding model.
- [x] Dynamic embedding dimension auto-detection (detects whether the model is 4096, 3584, 1536, 1024, or 768-dim).
- [x] Native Ollama `/api/chat` integration with `qwen2.5-coder` (or `llama3.2`) with real-time token streaming.
- [x] Configurable CLI arguments and environment variables.
- [x] Informative diagnostics when Ollama server or model needs to be pulled.

---

## 3. Impact Analysis
- **Privacy**: Zero external network calls when `--embedding-provider ollama --llm-provider ollama` are active.
- **Portability**: Auto-adjusts vector database tables to match the exact dimension returned by the loaded model.
