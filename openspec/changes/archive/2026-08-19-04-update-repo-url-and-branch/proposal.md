# Change Proposal: Update Repository URL and Branch

- **Change ID**: `04-update-repo-url-and-branch`
- **Author**: Antigravity Engineering
- **Status**: IN_PROGRESS
- **Created**: 2026-08-17

---

## 1. Why (Motivation)
Software repositories constantly evolve across multiple feature branches, pull requests (e.g. `pull/49`), and remote URL migrations. Users need to switch active branches or update remote repository URLs without deleting and re-registering the repository from scratch.

---

## 2. Scope & Goals

### In Scope
- [x] Backend capability in `RepoManager` to checkout new branches (`git fetch` + `git checkout`) and update remote URLs (`git remote set-url origin`).
- [x] Service method `update_repository` supporting `branch`, `url_or_path`, `name`, and `auto_sync`.
- [x] REST API endpoint `PUT /api/v1/repos/{repo_id}`.
- [x] CLI command `python3 main.py update <repo_id> [--url <url>] [--branch <branch>] [--name <name>] [--no-sync]`.
- [x] MCP tool `update_repository` for AI IDE agents.
- [x] Web UI "Edit Repository / Switch Branch" modal dialog on each repo card in Repo Hub.
- [x] Automated unit and integration tests.

---

## 3. Impact Analysis
- **Zero Data Corruption**: Safe git branch switching without leaving git working tree dirty.
- **Immediate Index Invalidation**: Automatically re-indexes changed files so retrieval reflects the active branch immediately.
