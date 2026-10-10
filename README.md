# ⚡ Multi-Repository Code Search Engine

Search across multiple code repositories at once, built for AI coding assistants. Connect over [MCP](#mcp) and call tools such as `search_codebases`, `get_symbol_definition`, `get_call_hierarchy` and `read_file`; results are ranked code chunks with repo, file, lines, symbol and caller/callee context. Repositories are registered and indexed with the [CLI](#cli) or the web UI. Specs live in `openspec/` ([OpenSpec](https://github.com/Fission-AI/openspec)).

## Quick Start

Requires Python 3 (standard library only) and [Ollama](https://ollama.com) for embeddings.

1. Pull the embedding model:

   ```bash
   ollama pull qwen3-embedding:0.6b
   ```

2. Register and index your repositories, from a local directory or a git URL:

   ```bash
   # Local directory
   python3 main.py add my-app /abs/path/to/my-app

   # Git repository (add --branch <name> for a non-default branch)
   python3 main.py add nest git@github.com:nestjs/nest.git --type git
   ```

3. Check that search works:

   ```bash
   python3 main.py search "login user authenticate"
   ```

4. Start the server and keep it running (MCP endpoint and web UI at `http://127.0.0.1:8000`):

   ```bash
   python3 main.py serve
   ```

5. Add the server to your AI assistant's MCP config (Claude Code, Cursor, Windsurf, Antigravity, etc.) and restart the assistant:

   ```json
   {
     "mcpServers": {
       "multi-repo-code-rag": { "url": "http://127.0.0.1:8000/mcp" }
     }
   }
   ```

6. Ask the assistant a question about your code, e.g. "Use multi-repo-code-rag to find where users are authenticated."

## MCP

The assistant gets these tools from `POST /mcp` on the running `serve` process. Only one process may use the data directory at a time, so CLI commands fail while `serve` is running.

| Tool | Description |
|---|---|
| `search_codebases` | Hybrid search: `query`, `repos?`, `groups?`, `expand?`, `expand_depth?`, `limit?` |
| `get_symbol_definition` | Definition, file, lines, docstring, code: `symbol_name`, `repo_id?` |
| `get_call_hierarchy` | Callers and callees: `symbol_name`, `repo_id?` |
| `get_cross_repo_api_links` | Frontend API calls linked to backend route handlers |
| `list_repositories` | Repositories with counts, branch, sync status |
| `update_repository` | Change branch, URL or name, optionally re-sync: `repo_id`, `branch?`, `url?`, `name?`, `auto_sync?` |
| `sync_repository` | Incremental index: `repo_id`, `force?` |
| `manage_repository_relations` | `action`: `create_group`, `delete_group`, `add_to_group`, `remove_from_group`, `add_dependency`, `remove_dependency`; plus `group?`, `repos?`, `repo?`, `depends_on?` |
| `get_repository_relations` | Relations for `repo?`, or the whole graph |
| `read_file` | Full file content: `repo_id`, `path` |

To require authentication, start the server with `--mcp-token <token>` (or set `MCP_AUTH_TOKEN`) and send `Authorization: Bearer <token>` on every request. When binding a non-localhost host, use a token and put the server behind TLS or an SSH tunnel.

## CLI

The CLI manages repositories, groups, dependencies and search. Run `python3 main.py --help` (or `<command> --help`) for all commands and options.

## Embedding Model

The default model is `qwen3-embedding:0.6b` (override with `--embedding-model` or `$OLLAMA_EMBEDDING_MODEL`). Pull it with `ollama pull`, or pass `--auto-pull` to `serve` / `sync`. If Ollama is offline, a built-in encoder is used.

- **Memory**: the model loads on first use and is released after 30s idle. Change this with `--keep-alive` (`0`, a duration like `5m`, or `always`), or release it now with `python3 main.py unload`.
- **Changing models**: affected repositories are re-embedded automatically, and searches are rejected until that finishes.

## Configuration

Flags apply to the CLI and `serve`; environment variables are the fallback.

| Variable | Purpose |
|---|---|
| `RAG_DATA_DIR` | Data directory (default: project `data/`); `--data-dir` overrides |
| `EMBEDDING_PROVIDER` | `ollama` (default), `subword`, `gemini`, `openai`, `unsloth` / `vllm`; `--embedding-provider` overrides |
| `OLLAMA_HOST`, `OLLAMA_EMBEDDING_MODEL` | Ollama server (default `http://localhost:11434`) and model; `--ollama-host` / `--embedding-model` override |
| `GEMINI_API_KEY`, `OPENAI_API_KEY` | Required for `gemini` / `openai`; without the key the `subword` encoder is used |
| `UNSLOTH_URL` (or `OPENAI_BASE_URL`), `UNSLOTH_EMBEDDING_MODEL`, `UNSLOTH_API_KEY` | OpenAI-compatible embedding server for `unsloth` / `vllm` |
| `EMBEDDING_KEEP_ALIVE` | Model residency policy; `--keep-alive` overrides |
| `MCP_AUTH_TOKEN` | Bearer token required on `/mcp`; `--mcp-token` overrides |
| `VERBOSE`, `LOG_LEVEL=DEBUG` | Debug logging |

## Tests

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```

