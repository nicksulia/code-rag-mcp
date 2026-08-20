## 1. Ingestion Logic Updates

- [x] 1.1 Update `ALLOWED_HIDDEN_DIRS`/related constants in `src/ingestion/repo_manager.py` to reflect that both `.github/skills` and `.github/workflows` are allowed children of `.github`.
- [x] 1.2 Update `RepoManager.is_path_ignored` so paths under `.github` are only ignored when `parts[1]` is neither `skills` nor `workflows` (or a nested descendant thereof), preserving existing strict ignoring of `.github/agents`, `.github/prompts`, `.github/instructions`, `.github/memory.md`, and other `.github/*` dotfiles.
- [x] 1.3 Update the directory-pruning branch in `RepoManager.scan_repository_files` (the `elif rel_root == ".github":` block) to allow descending into both `skills` and `workflows` subdirectories.
- [x] 1.4 Confirm no other pruning/ignore logic (e.g. `DEFAULT_IGNORED_EXTENSIONS`, gitignore pattern handling) needs adjustment for `.yml`/`.yaml` files under `.github/workflows`.

## 2. Chunking Verification

- [x] 2.1 Verify `LangChainCodeChunker.detect_language` correctly resolves `.github/workflows/*.yml` and `*.yaml` files to the `yaml` language via the existing extension map (no code change expected).
- [x] 2.2 Verify chunk output (scope header, chunk boundaries) is reasonable for representative GitHub Actions workflow YAML content; adjust `DEFAULT_SEPARATORS`/`LANGCHAIN_SEPARATORS` only if chunking behaves poorly for workflow files.

## 3. Tests

- [x] 3.1 Extend `tests/test_github_skills_indexing.py` (or add `tests/test_github_workflows_indexing.py`) with `is_path_ignored` assertions: `.github/workflows/ci.yml` and `.github/workflows/release.yaml` are NOT ignored; `.github/agents/dev.agent.md`, `.github/prompts/plan.prompt.md`, `.github/instructions/python.instructions.md`, `.github/memory.md`, `.github/.gitattributes` remain ignored; `.github/skills/**` assertions still pass.
- [x] 3.2 Add a `scan_repository_files` integration test that creates a mock repo with `.github/workflows/ci.yml`, `.github/skills/<name>/SKILL.md`, `.github/agents/dev.agent.md`, and a standard `src/app.py` file, then asserts the workflow and skill files are included in `added` while the agent file is excluded.
- [x] 3.3 Add a minimal chunking test confirming `.github/workflows/ci.yml` content is chunked with `language == "yaml"`.

## 4. Documentation & Spec Sync

- [x] 4.1 Confirm `openspec/specs/repository-management.md` "Allowed Hidden Directories & Skills Policy" requirement will be updated via archive to include the `.github/workflows/**` allowance (already captured in this change's delta spec).
- [x] 4.2 Update `README.md` or other developer-facing docs only if they explicitly describe the `.github` ingestion allow-list (search for existing references before editing).

## 5. Verification

- [x] 5.1 Run the full test suite (`pytest`) and confirm no regressions, especially in `tests/test_github_skills_indexing.py` and repository scanning tests.
- [x] 5.2 Manually re-sync one of the fixture repos (or this repo itself) and confirm `.github/workflows/*.yml` files appear in search results while `.github/agents/*`, `.github/prompts/*`, etc. do not.
