# Implementation Tasks: Indexing `.github/skills/*` & Metadata Assets

- **Change ID**: `06-github-skills-indexing`
- **Status**: COMPLETED

---

## Task Checklist

- [x] Create change proposal and technical design under `openspec/changes/06-github-skills-indexing/`
- [x] Update specification `openspec/specs/repository-management.md` to version `2.3.0`
- [x] Update `src/ingestion/repo_manager.py` with `ALLOWED_HIDDEN_DIRS = {".github"}`
- [x] Update `is_path_ignored` and `scan_repository_files` in `repo_manager.py`
- [x] Add unit and integration tests in `tests/test_github_skills_indexing.py`
- [x] Verify test suite passes (`python3 -m unittest discover -s tests`)
- [x] Re-sync existing repos and verify `.github/skills/*` file indexing
- [x] Mark tasks as COMPLETED and update OpenSpec status
