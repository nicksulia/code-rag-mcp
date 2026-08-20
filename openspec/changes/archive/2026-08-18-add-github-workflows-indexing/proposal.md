## Why

CI/CD pipeline definitions under `.github/workflows/*.yml` encode critical operational knowledge — build steps, test matrices, deployment gates, required secrets, and environment configuration — that is currently invisible to the RAG engine. The `06-github-skills-indexing` change intentionally restricted `.github/` ingestion to `.github/skills/**`, so workflow files are always skipped even though they are plain-text, version-controlled YAML with no more risk than any other tracked file. Engineers asking "how does the release pipeline work?" or "what triggers deployment?" get no grounded answer today.

## What Changes

- Extend the `.github/` allow-list so `.github/workflows/**` (`.yml`/`.yaml` workflow definition files) is traversed and indexed alongside the existing `.github/skills/**` allowance.
- Update `RepoManager.is_path_ignored` and `RepoManager.scan_repository_files` directory pruning to permit descending into `.github/workflows` in addition to `.github/skills`, while all other `.github/*` subpaths (`agents`, `prompts`, `instructions`, `memory.md`, dotfiles, etc.) remain strictly ignored.
- Reuse the existing YAML chunking/language-detection path (`SupportedLanguage.YAML`, `.yml`/`.yaml` extension mapping) already present in `LangChainCodeChunker` — no new parser is required, only enabling the files to reach it.
- Add unit tests validating path filtering, directory pruning, and end-to-end scan/chunk behavior for `.github/workflows/*.yml` and `.yaml` files, alongside regression coverage confirming `.github/skills/**` and other `.github/*` exclusions are unaffected.
- Update `openspec/specs/repository-management.md` to document the expanded allow-list.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `repository-management`: The "Allowed Hidden Directories & Skills Policy" requirement is widened so `.github/workflows/**` is also traversed and indexed, in addition to the existing `.github/skills/**` allowance; all other `.github/*` content remains ignored.

## Impact

- **Ingestion** (`src/ingestion/repo_manager.py`): `ALLOWED_HIDDEN_DIRS`/`is_path_ignored` logic and `scan_repository_files` directory-pruning branch for `.github` gain a second allowed child directory (`workflows`).
- **Chunking**: No changes needed — `LangChainCodeChunker` already maps `.yml`/`.yaml` to the `yaml` language and chunks generically via `LANGCHAIN_SEPARATORS`/`DEFAULT_SEPARATORS`.
- **Tests**: `tests/test_github_skills_indexing.py` gets updated/expanded assertions (or a new `tests/test_github_workflows_indexing.py`) for workflow file inclusion and continued exclusion of other `.github/*` paths.
- **Index Hygiene**: Slightly increases indexed surface area per repo, bounded to `.github/workflows/**` only; no impact on retrieval ranking logic, vector/BM25 schemas, or the symbol graph.
