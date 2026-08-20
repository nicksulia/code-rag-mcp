## Why

Answer generation no longer belongs in this service because remote cloud LLM clients will consume its retrieval results directly. Removing the local and in-process generation layer reduces model/runtime complexity while preserving semantic natural-language search, hybrid reranking, and code intelligence.

## What Changes

- **BREAKING** Remove the `query_cross_repo_rag` MCP tool; cloud LLM clients use `search_codebases` and the existing symbol and graph tools.
- **BREAKING** Remove the `POST /api/v1/rag/query` and `POST /api/v1/rag/stream` endpoints while preserving `POST /api/v1/search`.
- **BREAKING** Remove the CLI `chat` command and LLM-specific provider/model options.
- Remove `RAGGenerator`, local/remote LLM invocation, deterministic answer synthesis, token streaming, and LLM model lifecycle handling.
- Remove `ContextPackager` and generation-specific citation models because search results already expose ranked chunks with repository, file, line, graph, and relation metadata.
- Remove the Web UI RAG Studio and make retrieval-oriented search the primary query experience.
- Preserve embedding generation and lifecycle management, dense and lexical retrieval, RRF reranking, symbol and graph enrichment, repository scope expansion, and relation hop-decay.
- Update tests and documentation to describe the project as a retrieval and code-search service for external cloud LLM consumers.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `rag-generation`: Retire the in-service LLM generation capability and all provider, prompting, fallback synthesis, streaming, and citation-generation requirements.
- `context-fusion-reranking`: Preserve result fusion and reranking requirements while removing generation-oriented context packaging requirements.
- `mcp-server`: Remove `query_cross_repo_rag` while preserving retrieval and code-intelligence tools, especially natural-language `search_codebases`.
- `api-and-web-ui`: Remove RAG query/stream endpoints and the RAG Studio while preserving the search API and retrieval-focused UI.

## Impact

- Affected code includes `src/generator/`, `src/service.py`, `src/server/api.py`, `src/mcp/server.py`, `src/cli/main.py`, generation-only schema types, the Web UI, and related tests.
- MCP, REST, CLI, and Web UI consumers using generated-answer surfaces must migrate to ranked search results and perform generation in their cloud LLM environment.
- LLM-specific environment variables and command-line configuration become unsupported; embedding-provider configuration remains supported.
- The `/api/v1/search` response and `search_codebases` result contract remain the integration boundary for cloud LLM retrieval.
