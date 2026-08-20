## REMOVED Requirements

### Requirement: LLM Providers

The system SHALL interface with multi-provider LLMs — Local Ollama (`qwen2.5-coder`, `llama3.2`, `deepseek-coder`) alongside cloud providers (Google Gemini, Anthropic Claude, OpenAI) — and SHALL fall back to a built-in deterministic offline synthesizer when no LLM host or API key is available, in order to perform grounded cross-repo reasoning, architectural inquiries, call trace explanations, bug investigation, and code generation.

**Reason**: Answer generation no longer belongs in this service. Remote cloud LLM clients consume the service's retrieval results directly and perform generation in their own environment, removing the need for local/remote LLM invocation and model lifecycle handling.
**Migration**: Cloud LLM clients call `search_codebases` (MCP) or `POST /api/v1/search` (REST) to retrieve ranked code chunks with repository, file, line, graph, and relation metadata, and perform generation themselves.

### Requirement: Grounding & Citation Rules

The system SHALL enforce a system prompt requiring grounded citations that point to exact repository names, file paths, and line ranges (`[repo-name] file.py:L10-L25`), and SHALL explicitly distinguish frontend callers from backend API handlers in generated answers.

**Reason**: Citation rules exist only to constrain LLM-generated answers. With generation removed from the service, ranked search results already expose repository, file, line, graph, and relation metadata directly, making generation-time citation enforcement unnecessary.
**Migration**: Consumers derive citations directly from the `repository`, `file_path`, and `line_range` fields already present on each ranked search result.
