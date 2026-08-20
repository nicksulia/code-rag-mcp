## REMOVED Requirements

### Requirement: Deduplication & Context Packaging

The system SHALL group overlapping or contiguous chunks from the same file into unified code blocks, format output with citation headers (`[CITATION #1] repo: frontend-app | file: src/api/client.ts:L24-L58 | symbol: fetchUserProfile`), and manage a token budget (e.g. 4,000 to 16,000 tokens) so the total packaged context respects user-configured limits while maximizing information density.

**Reason**: This requirement exists solely to package ranked chunks into an LLM prompt context window for the removed generation layer. Ranked search results already expose each chunk's repository, file, line range, symbol, graph, and relation metadata directly, so generation-oriented packaging and token budgeting are unnecessary once generation moves to external cloud LLM consumers.
**Migration**: Consumers read the ranked chunks returned by fusion and reranking directly, including their repository, file path, line range, and symbol metadata, and apply any prompt packaging or token budgeting themselves in their own LLM environment.
