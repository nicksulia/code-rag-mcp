# Technical Design: Dynamic Repository Updates & Branch Switching

- **Change ID**: `04-update-repo-url-and-branch`
- **Status**: DESIGNED

---

## 1. Git Execution Flow

```mermaid
sequenceDiagram
    participant User as User / Web UI / CLI / MCP
    participant API as MultiRepoRAGService
    participant RM as RepoManager
    participant Git as Git Process (Local FS)
    participant Indexer as Vector/BM25F/Graph Stores

    User->>API: update_repository(repo_id, branch="feature/pr-51", url=..., auto_sync=True)
    API->>RM: get_repo(repo_id)
    API->>RM: update_repo(repo, new_url, new_branch, new_name)
    RM->>Git: git remote set-url origin <new_url> (if URL changed)
    RM->>Git: git fetch origin <branch> (if branch changed)
    RM->>Git: git checkout <branch>
    RM->>Git: git pull origin <branch>
    RM->>RM: save_repo(updated_repo)
    alt auto_sync == True
        API->>API: sync_repository(repo_id, force=False)
        API->>Indexer: parse & embed modified files
    end
    API-->>User: Return updated repository state
```

---

## 2. Interface Contracts

### 2.1 REST API Endpoint
`PUT /api/v1/repos/{repo_id}`

**Request Body**:
```json
{
  "name": "Update API",
  "url_or_path": "git@github.com:my-org/my-repo.git",
  "branch": "main",
  "auto_sync": true
}
```

**Response**:
```json
{
  "repo": {
    "repo_id": "update-api",
    "name": "Update API",
    "branch": "main",
    "url_or_path": "git@github.com:my-org/my-repo.git",
    "status": "ready",
    "total_files": 153,
    "total_chunks": 568
  },
  "sync_result": {
    "added_files": 0,
    "modified_files": 0,
    "total_chunks": 568
  }
}
```

### 2.2 MCP Tool
`update_repository`:
- Inputs: `repo_id` (string, required), `branch` (string, optional), `url` (string, optional), `name` (string, optional), `auto_sync` (boolean, optional).

### 2.3 CLI Command
`python3 main.py update <repo_id> [--url <url>] [--branch <branch>] [--name <name>] [--no-sync]`
