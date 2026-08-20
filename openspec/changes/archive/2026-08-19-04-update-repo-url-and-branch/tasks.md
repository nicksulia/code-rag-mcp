# Implementation Tasks: Repository URL & Branch Updates

- **Change ID**: `04-update-repo-url-and-branch`
- **Status**: COMPLETED

---

## Task Checklist

- [x] Implement `update_repo` and `switch_git_branch_or_url` in `src/ingestion/repo_manager.py`
- [x] Implement `update_repository` in `src/service.py`
- [x] Implement `PUT /api/v1/repos/{repo_id}` in `src/server/api.py`
- [x] Implement `update` subcommand in `src/cli/main.py`
- [x] Implement `update_repository` tool in `src/mcp/server.py`
- [x] Implement Edit modal and branch switcher in `web/index.html`, `web/app.js`, and `web/style.css`
- [x] Create unit and integration tests in `tests/test_repo_update.py` (4 new tests)
- [x] Run full regression test suite and verify UI/CLI workflows (29/29 tests passing)
