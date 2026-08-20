## REMOVED Requirements

### Requirement: `query_cross_repo_rag` Tool

The MCP server SHALL expose a `query_cross_repo_rag` tool that lets an agent ask a natural language technical or architectural question across multiple repositories and receive a synthesized answer with verified code citations.

Arguments:

- `prompt` (`string`, **required**): the technical inquiry or architectural question to answer using grounded multi-repository codebase context.
- `repos` (`string[]`, *optional*, default: `null` / all repos): array of repository IDs or names to narrow context.
- `top_k` (`integer`, *optional*, default: `8`): number of top retrieved code chunks to include in the synthesis prompt.
- `groups` (`string[]`, *optional*, default: `null`): group names whose members join the primary repository set.
- `expand` (`string`, *optional*, default: `"none"`): one of `none`, `upstream`, `downstream`, `both`.
- `expand_depth` (`integer`, *optional*, default: `1`): maximum traversal depth when expanding.

The tool SHALL report the resolved repository scope (primary vs. expanded), and citations drawn from expanded repositories SHALL be marked as such.

**Reason**: Answer generation no longer belongs in this service; remote cloud LLM clients consume retrieval results directly and perform generation themselves.
**Migration**: Cloud LLM clients call `search_codebases` (and, for deeper code intelligence, `get_symbol_definition`, `get_call_hierarchy`, and `get_cross_repo_api_links`) to retrieve ranked code chunks and graph context, then synthesize answers with citations in their own environment.
