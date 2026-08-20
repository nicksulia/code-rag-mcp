# Change Proposal: Indexing `.github/skills/*` & Metadata Assets

- **Change ID**: `06-github-skills-indexing`
- **Author**: Antigravity Engineering
- **Status**: COMPLETED
- **Created**: 2026-08-18

---

## 1. Why (Motivation)
Repositories frequently store agent skill definitions (`.github/skills/**/SKILL.md`), reference architectures, and code template assets under `.github/skills/`. 

Previously, the ingestion scanner treated all directories starting with `.` as hidden/system folders, ignoring `.github/` entirely. To provide agent skill knowledge to RAG queries while keeping developer noise low, the ingestion pipeline explicitly allows `.github/skills/**` while strictly ignoring other `.github/` directories (such as `.github/agents/`, `.github/workflows/`, `.github/prompts/`, `.github/instructions/`, `.github/memory.md`) and VCS internals (`.git`).

---

## 2. Scope & Goals

### In Scope
- [x] Create change proposal, design, and task list in `openspec/changes/06-github-skills-indexing/`.
- [x] Update specification `openspec/specs/repository-management.md` with the strict `.github/skills/**` policy.
- [x] Update `RepoManager.scan_repository_files` to prune all non-skills subdirectories under `.github`.
- [x] Update `RepoManager.is_path_ignored` to strictly allow `.github/skills/*` while ignoring `.github/agents/*`, `.github/workflows/*`, `.github/prompts/*`, `.github/instructions/*`, `.github/memory.md`, etc.
- [x] Add unit tests in `tests/test_github_skills_indexing.py` validating path filtering, file scanning, and chunk ingestion.
- [x] Verify full regression test suite passes and purge non-skills files on re-sync.

---

## 3. Impact Analysis
- **Ingestion**: File scanning strictly limits `.github/` ingestion to `.github/skills/**`.
- **Index Hygiene**: Prevents clutter from GitHub Actions workflows, prompt scratchpads, and agent instructions.
- **Chunking & Indexing**: Handled seamlessly by `LangChainCodeChunker`, `VectorStore`, and `BM25LexicalStore` without breaking existing schemas.
