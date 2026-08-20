# Technical Design: Ollama Qwen3-Embedding-8B & LLM Engine

- **Change ID**: `03-ollama-dense-indexing-and-llm`
- **Status**: DESIGNED

---

## 1. System Architecture

```mermaid
flowchart LR
    subgraph OllamaLocal["Local Ollama Server (http://localhost:11434)"]
        EmbedAPI["POST /api/embed (qwen3-embedding:8b)"]
        ChatAPI["POST /api/chat (qwen2.5-coder:7b)"]
    end

    subgraph Indexer["Multi-Repo Indexer"]
        Chunker["AST Semantic Chunker"]
        OllamaEmbedEngine["OllamaEmbeddingEngine (Dynamic Probe)"]
        VectorDB[("Dual-Vector Store (V_sig, V_body)")]
    end

    subgraph Generation["RAG Generator"]
        Retriever["Hybrid Retriever (BM25F + Qwen Vectors)"]
        OllamaLLMEngine["Ollama LLM Streamer"]
        Clients["Web UI / CLI / MCP"]
    end

    Chunker --> OllamaEmbedEngine --> EmbedAPI
    EmbedAPI --> VectorDB
    
    Query --> Retriever --> VectorDB
    Retriever --> OllamaLLMEngine --> ChatAPI
    ChatAPI --> Clients
```

---

## 2. API Contracts & Dynamic Dimension Detection

### 2.1 Embedding Endpoint (`/api/embed`)
- Request payload:
  ```json
  {
    "model": "qwen3-embedding:8b",
    "input": ["def verify_token(...)", "class AuthService:..."]
  }
  ```
- Response payload:
  ```json
  {
    "model": "qwen3-embedding:8b",
    "embeddings": [[0.012, -0.045, ...]]
  }
  ```
- **Fallback (`/api/embeddings`)**: If `/api/embed` is not supported on older Ollama versions, seamlessly falls back to `/api/embeddings` with `{"model": model, "prompt": text}`.

### 2.2 Chat Endpoint (`/api/chat`)
- Request payload:
  ```json
  {
    "model": "qwen2.5-coder",
    "messages": [
      {"role": "system", "content": "You are an expert codebase architect..."},
      {"role": "user", "content": "=== CONTEXT ===\n... \n=== QUERY ===\n..."}
    ],
    "stream": true,
    "options": {
      "temperature": 0.2,
      "num_ctx": 8192
    }
  }
  ```
- Streams JSON chunks with `{"message": {"content": "token"}, "done": false}`.
