# Specification: Repository Management & Ingestion

## Status: ACTIVE
## Domain: Core Ingestion
## Version: 2.4.0

---

## 1. Overview
The Repository Management module is responsible for discovering, registering, synchronizing, modifying, and tracking multiple codebases (local directories or remote Git repositories). It ensures incremental indexing by computing content/commit hashes, respecting ignore rules, and managing repo metadata.

---

## 2. Requirements

### 2.1 Repository Registration
- **Local Repositories**: Support registering any valid local directory path.
- **Remote Git Repositories**: Support cloning from HTTPS/SSH URLs, tracking specific branches or tags.
- **Repository Identifiers**: Each repository must have a unique identifier (`repo_id`), display name, source type (`local` or `git`), and base filesystem path.

### 2.2 Ignore Rules & File Filtering
- System MUST respect `.gitignore` files found within repository roots and subfolders.
- Default exclude list must always ignore:
  - Dependency directories: `node_modules`, `vendor`, `venv`, `.venv`, `__pycache__`, `.tox`, `target`, `dist`, `build`.
  - Version control directories: `.git`, `.svn`, `.hg`.
  - Binary & media assets: `.png`, `.jpg`, `.jpeg`, `.gif`, `.ico`, `.pdf`, `.zip`, `.tar.gz`, `.wasm`, `.pyc`, `.exe`, `.so`, `.dylib`, `.dll`.
  - Lockfiles & Minified assets (configurable): `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `*.min.js`, `*.min.css`.
- **Allowed Hidden Directories & Skills Policy**: While dot-prefixed directories are generally ignored, `.github/skills/**` (agent skill definitions `SKILL.md`, reference architectures, and skill assets) and `.github/workflows/**` (GitHub Actions workflow definition files, `*.yml`/`*.yaml`) MUST be traversed and indexed. All other non-allowed directories and files under `.github` (such as `.github/agents/`, `.github/prompts/`, `.github/instructions/`, `.github/memory.md`, and dotfiles like `.gitattributes`) MUST remain strictly ignored. Files within `.github/workflows/**` remain subject to the same standard filtering rules (`.gitignore` patterns, size guard, default-ignored extensions) applied to any other allowed path.
- Individual dot-prefixed system files (e.g., `.gitattributes`, `.DS_Store`) remain strictly ignored.
- File size guard: Files exceeding a configurable threshold (default: 1.5MB) are skipped to avoid token explosion.

### 2.3 Incremental Sync & Change Detection
- Track file SHA-256 hashes and Git commit IDs across sync cycles.
- On sync, identify:
  - **Added Files**: New files to parse and index.
  - **Modified Files**: Changed files where existing chunks/symbols are invalidated and replaced.
  - **Deleted Files**: Removed files where chunks, vectors, and symbol graph edges are purged.
- Store sync timestamps and status (`synced`, `indexing`, `error`, `dirty`).

### 2.4 Dynamic Repository Updates & Branch Switching
- Support updating repository metadata: `url_or_path`, `branch`, and `name`.
- When updating Git repositories:
  - If URL changes: run `git remote set-url origin <new_url>`.
  - If branch changes: run `git fetch origin <branch>`, `git checkout <branch>`, and `git pull origin <branch>`.
- Automatically trigger incremental sync upon branch/URL modification unless explicitly deferred (`auto_sync=False`).

---

## 3. Data Schema

```json
{
  "repo_id": "string (unique slug)",
  "name": "string",
  "source_type": "local | git",
  "url_or_path": "string",
  "branch": "string (optional)",
  "commit_hash": "string (optional)",
  "total_files": "integer",
  "total_chunks": "integer",
  "total_symbols": "integer",
  "status": "ready | indexing | error",
  "last_synced_at": "number (timestamp)"
}
```
