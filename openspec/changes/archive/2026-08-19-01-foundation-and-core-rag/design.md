# Technical Design: Multi-Repository Code RAG Engine

- **Change ID**: `01-foundation-and-core-rag`
- **Status**: DESIGNED

---

## 1. System Architecture

### 1.1 Ingestion & File Discovery (`src/ingestion/`)
- `RepoManager`:
  - Registers repositories in local SQLite catalog (`repos.db`).
  - Uses `pathspec` to parse `.gitignore` rules.
  - Computes per-file SHA-256 hashes to skip unchanged files.
  - Normalizes repository paths and handles git cloning/pulling.

### 1.2 AST Parsing & Semantic Chunking (`src/parser/`)
- `ASTChunker`:
  - Utilizes Tree-sitter parsers for Python, TypeScript/JavaScript, Go, Rust, Java, C/C++, HTML/CSS, SQL, Markdown.
  - Extracts nodes: `function_definition`, `method_definition`, `class_definition`, `interface_declaration`, `impl_item`, `struct_specifier`.
  - Injects contextual metadata: file banner, enclosing class signature, module imports.
- `SymbolExtractor`:
  - Extracts declared symbols, caller-callee call sites, imports, and cross-repo API signatures (e.g. `axios.post('/api/...')` or `fetch('/...')` matched to `@app.post('/api/...')`).

### 1.3 Hybrid Indexing & Storage (`src/indexer/`)
- `VectorStore`:
  - LanceDB table / SQLite vector index with 384/1536-dim embeddings.
  - Stores metadata: `chunk_id`, `repo_id`, `file_path`, `language`, `symbol_name`, `chunk_type`, `start_line`, `end_line`, `raw_content`, `enriched_content`.
- `LexicalStore`:
  - BM25 tokenizer splitting snake_case and camelCase tokens for exact code identifier retrieval.
- `GraphStore`:
  - SQLite database storing nodes (`symbols`) and directed edges (`calls`, `imports`, `implements`).

### 1.4 Context Retrieval & Reranker (`src/retriever/`)
- `HybridRetriever`:
  - Runs parallel dense vector similarity query and sparse BM25 query.
  - Merges rankings with Reciprocal Rank Fusion (RRF).
  - Queries `GraphStore` to retrieve 1-hop caller/callee signatures for top ranked symbols.
  - Deduplicates overlapping file spans.
- `ContextPackager`:
  - Formats retrieved chunks with syntax delimiters and verified citation headers `[CITATION #n] repo://...`.

### 1.5 Generation & Synthesis (`src/generator/`)
- `RAGGenerator`:
  - Synthesizes answers using LiteLLM (Gemini, Claude, OpenAI, Ollama).
  - Enforces markdown code citations.
  - Streams response tokens via SSE.

### 1.6 Interfaces (`src/server/`, `src/mcp/`, `src/cli/`, `web/`)
- **FastAPI REST API**: Endpoints for repos, search, symbols, streaming chat, file content.
- **MCP Server**: Implements MCP tools for integration into AI coding tools.
- **CLI**: Click-based CLI for terminal usage.
- **Web UI**: Modern dashboard with dark mode, interactive chat, repo manager, and code inspector.
