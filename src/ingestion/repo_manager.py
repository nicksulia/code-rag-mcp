"""
Repository management and ingestion module.
Discovers, clones, filters, tracks hashes, and monitors changes across multiple repositories.
"""

import os
import re
import time
import hashlib
import fnmatch
import subprocess
import sqlite3
import contextlib
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Set, Any

from ..models.schema import (
    Repository,
    RepoSourceType,
    RepoStatus,
    RepoGroup,
    RepoDependency,
)


class RelationError(Exception):
    """Base exception for repository relation errors."""

    pass


class UnknownRepositoryError(RelationError):
    def __init__(self, repo_id: str):
        super().__init__(f"Repository '{repo_id}' is not registered.")
        self.repo_id = repo_id


class UnknownGroupError(RelationError):
    def __init__(self, group_name: str):
        super().__init__(f"Group '{group_name}' does not exist.")
        self.group_name = group_name


class DuplicateGroupError(RelationError):
    def __init__(self, group_name: str):
        super().__init__(f"Group '{group_name}' already exists.")
        self.group_name = group_name


class SelfDependencyError(RelationError):
    def __init__(self, repo_id: str):
        super().__init__(f"Repository '{repo_id}' cannot depend on itself.")
        self.repo_id = repo_id


class DependencyCycleError(RelationError):
    def __init__(self, repo_id: str, depends_on_repo_id: str):
        super().__init__(
            f"Adding dependency '{repo_id}' -> '{depends_on_repo_id}' would create a cycle."
        )
        self.repo_id = repo_id
        self.depends_on_repo_id = depends_on_repo_id


DEFAULT_IGNORED_DIRS = {
    "node_modules",
    "vendor",
    "venv",
    ".venv",
    "env",
    ".env",
    "__pycache__",
    ".tox",
    ".pytest_cache",
    ".mypy_cache",
    "target",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".output",
    ".git",
    ".svn",
    ".hg",
    ".idea",
    ".vscode",
    "coverage",
}

DEFAULT_IGNORED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".webp",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".tar.gz",
    ".7z",
    ".rar",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".wasm",
    ".pyc",
    ".lock",
    ".min.js",
    ".min.css",
    ".map",
    ".bundle.js",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp3",
    ".mp4",
    ".mov",
    ".db",
    ".sqlite",
    ".sqlite3",
}

ALLOWED_HIDDEN_DIRS = {".github"}

# Subdirectories of .github that are strictly allowed to be traversed and indexed.
# All other .github/* content (agents, prompts, instructions, memory.md, dotfiles, etc.)
# remains ignored.
ALLOWED_GITHUB_SUBDIRS = {"skills", "workflows"}

MAX_FILE_SIZE_BYTES = 1_500_000  # 1.5 MB


class RepoManager:
    def __init__(self, data_dir: str = "./data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.repos_dir = self.data_dir / "repos"
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "catalog.db"
        self._init_db()

    def _init_db(self):
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS repositories (
                    repo_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    url_or_path TEXT NOT NULL,
                    branch TEXT,
                    commit_hash TEXT,
                    total_files INTEGER DEFAULT 0,
                    total_chunks INTEGER DEFAULT 0,
                    total_symbols INTEGER DEFAULT 0,
                    status TEXT NOT NULL,
                    last_synced_at REAL,
                    error_message TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS file_hashes (
                    repo_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    last_modified REAL,
                    size_bytes INTEGER,
                    PRIMARY KEY (repo_id, file_path)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS repo_groups (
                    name TEXT PRIMARY KEY,
                    created_at REAL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS repo_group_members (
                    group_name TEXT NOT NULL,
                    repo_id TEXT NOT NULL,
                    PRIMARY KEY (group_name, repo_id)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS repo_dependencies (
                    repo_id TEXT NOT NULL,
                    depends_on_repo_id TEXT NOT NULL,
                    created_at REAL,
                    PRIMARY KEY (repo_id, depends_on_repo_id)
                )
            """)
            conn.commit()

    def register_repo(
        self,
        repo_id: str,
        name: str,
        source_type: str,
        url_or_path: str,
        branch: str = "main",
    ) -> Repository:
        clean_path = (
            os.path.abspath(os.path.expanduser(url_or_path))
            if source_type == "local"
            else url_or_path
        )
        repo = Repository(
            repo_id=repo_id,
            name=name,
            source_type=RepoSourceType(source_type),
            url_or_path=clean_path,
            branch=branch,
            status=RepoStatus.READY,
            last_synced_at=None,
        )
        self.save_repo(repo)
        return repo

    def save_repo(self, repo: Repository):
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO repositories (
                    repo_id, name, source_type, url_or_path, branch,
                    commit_hash, total_files, total_chunks, total_symbols,
                    status, last_synced_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    repo.repo_id,
                    repo.name,
                    repo.source_type.value
                    if isinstance(repo.source_type, RepoSourceType)
                    else repo.source_type,
                    repo.url_or_path,
                    repo.branch,
                    repo.commit_hash,
                    repo.total_files,
                    repo.total_chunks,
                    repo.total_symbols,
                    repo.status.value
                    if isinstance(repo.status, RepoStatus)
                    else repo.status,
                    repo.last_synced_at,
                    repo.error_message,
                ),
            )
            conn.commit()

    def get_repo(self, repo_id: str) -> Optional[Repository]:
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM repositories WHERE repo_id = ?", (repo_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return Repository(
                repo_id=row[0],
                name=row[1],
                source_type=RepoSourceType(row[2]),
                url_or_path=row[3],
                branch=row[4],
                commit_hash=row[5],
                total_files=row[6],
                total_chunks=row[7],
                total_symbols=row[8],
                status=RepoStatus(row[9]),
                last_synced_at=row[10],
                error_message=row[11],
            )

    def list_repos(self) -> List[Repository]:
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM repositories ORDER BY name ASC")
            rows = cursor.fetchall()
            repos = []
            for row in rows:
                repos.append(
                    Repository(
                        repo_id=row[0],
                        name=row[1],
                        source_type=RepoSourceType(row[2]),
                        url_or_path=row[3],
                        branch=row[4],
                        commit_hash=row[5],
                        total_files=row[6],
                        total_chunks=row[7],
                        total_symbols=row[8],
                        status=RepoStatus(row[9]),
                        last_synced_at=row[10],
                        error_message=row[11],
                    )
                )
            return repos

    def update_repo(
        self,
        repo_id: str,
        url_or_path: Optional[str] = None,
        branch: Optional[str] = None,
        name: Optional[str] = None,
    ) -> Repository:
        repo = self.get_repo(repo_id)
        if not repo:
            raise ValueError(f"Repository '{repo_id}' not found.")

        if name:
            repo.name = name.strip()

        url_changed = False
        if url_or_path and url_or_path.strip() != repo.url_or_path:
            repo.url_or_path = url_or_path.strip()
            url_changed = True

        branch_changed = False
        if branch and branch.strip() != repo.branch:
            repo.branch = branch.strip()
            branch_changed = True

        if repo.source_type == RepoSourceType.GIT and (url_changed or branch_changed):
            ok, err = self.switch_git_branch_or_url(
                repo,
                new_url=repo.url_or_path if url_changed else None,
                new_branch=repo.branch if branch_changed else None,
            )
            if not ok:
                repo.status = RepoStatus.ERROR
                repo.error_message = err
                self.save_repo(repo)
                raise RuntimeError(f"Git update failed: {err}")

        self.save_repo(repo)
        return repo

    def switch_git_branch_or_url(
        self,
        repo: Repository,
        new_url: Optional[str] = None,
        new_branch: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Updates Git remote origin URL and/or checks out the target branch.
        """
        dest_dir = self.repos_dir / repo.repo_id
        if not dest_dir.exists():
            return self.clone_or_pull_git_repo(repo)

        try:
            # 1. Update remote URL if changed
            if new_url:
                res = subprocess.run(
                    ["git", "remote", "set-url", "origin", new_url],
                    cwd=str(dest_dir),
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if res.returncode != 0:
                    return False, f"Failed to set remote URL: {res.stderr}"

            # 2. Switch branch if changed
            if new_branch:
                # Fetch branch from remote
                subprocess.run(
                    ["git", "fetch", "origin", new_branch],
                    cwd=str(dest_dir),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                # Checkout branch
                res = subprocess.run(
                    ["git", "checkout", new_branch],
                    cwd=str(dest_dir),
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if res.returncode != 0:
                    # Try creating tracking branch if not existing locally
                    res_track = subprocess.run(
                        ["git", "checkout", "-b", new_branch, f"origin/{new_branch}"],
                        cwd=str(dest_dir),
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    if res_track.returncode != 0:
                        return (
                            False,
                            f"Failed to checkout branch '{new_branch}': {res.stderr or res_track.stderr}",
                        )

                # Pull latest changes for the branch
                subprocess.run(
                    ["git", "pull", "origin", new_branch],
                    cwd=str(dest_dir),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

            return True, None
        except Exception as ex:
            return False, f"Git branch switch exception: {str(ex)}"

    def delete_repo(self, repo_id: str) -> bool:
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM repo_group_members WHERE repo_id = ?", (repo_id,)
            )
            cursor.execute(
                "DELETE FROM repo_dependencies WHERE repo_id = ? OR depends_on_repo_id = ?",
                (repo_id, repo_id),
            )
            cursor.execute("DELETE FROM file_hashes WHERE repo_id = ?", (repo_id,))
            cursor.execute("DELETE FROM repositories WHERE repo_id = ?", (repo_id,))
            deleted = cursor.rowcount > 0
            conn.commit()
            return deleted

    # -------------------------------------------------------------------------
    # Repository Relations (Groups and Dependencies)
    # -------------------------------------------------------------------------

    def create_group(
        self, name: str, repo_ids: Optional[List[str]] = None
    ) -> RepoGroup:
        group_name = name.strip()
        if not group_name:
            raise ValueError("Group name cannot be empty.")

        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM repo_groups WHERE name = ?", (group_name,))
            if cursor.fetchone():
                raise DuplicateGroupError(group_name)

            members: List[str] = []
            if repo_ids:
                for rid in repo_ids:
                    clean_rid = rid.strip()
                    if not clean_rid:
                        continue
                    cursor.execute(
                        "SELECT repo_id FROM repositories WHERE repo_id = ?",
                        (clean_rid,),
                    )
                    if not cursor.fetchone():
                        raise UnknownRepositoryError(clean_rid)
                    if clean_rid not in members:
                        members.append(clean_rid)

            now = time.time()
            cursor.execute(
                "INSERT INTO repo_groups (name, created_at) VALUES (?, ?)",
                (group_name, now),
            )
            for clean_rid in members:
                cursor.execute(
                    "INSERT OR IGNORE INTO repo_group_members (group_name, repo_id) VALUES (?, ?)",
                    (group_name, clean_rid),
                )
            conn.commit()
            return RepoGroup(name=group_name, created_at=now, members=members)

    def delete_group(self, name: str) -> bool:
        group_name = name.strip()
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM repo_group_members WHERE group_name = ?", (group_name,)
            )
            cursor.execute("DELETE FROM repo_groups WHERE name = ?", (group_name,))
            deleted = cursor.rowcount > 0
            conn.commit()
            return deleted

    def add_repos_to_group(self, name: str, repo_ids: List[str]) -> RepoGroup:
        group_name = name.strip()
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT created_at FROM repo_groups WHERE name = ?", (group_name,)
            )
            row = cursor.fetchone()
            if not row:
                raise UnknownGroupError(group_name)
            created_at = row[0]

            for rid in repo_ids:
                clean_rid = rid.strip()
                if not clean_rid:
                    continue
                cursor.execute(
                    "SELECT repo_id FROM repositories WHERE repo_id = ?", (clean_rid,)
                )
                if not cursor.fetchone():
                    raise UnknownRepositoryError(clean_rid)
                cursor.execute(
                    "INSERT OR IGNORE INTO repo_group_members (group_name, repo_id) VALUES (?, ?)",
                    (group_name, clean_rid),
                )

            conn.commit()

            cursor.execute(
                "SELECT repo_id FROM repo_group_members WHERE group_name = ? ORDER BY repo_id ASC",
                (group_name,),
            )
            members = [r[0] for r in cursor.fetchall()]
            return RepoGroup(name=group_name, created_at=created_at, members=members)

    def remove_repo_from_group(self, name: str, repo_id: str) -> bool:
        group_name = name.strip()
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM repo_groups WHERE name = ?", (group_name,))
            if not cursor.fetchone():
                raise UnknownGroupError(group_name)
            cursor.execute(
                "DELETE FROM repo_group_members WHERE group_name = ? AND repo_id = ?",
                (group_name, repo_id.strip()),
            )
            deleted = cursor.rowcount > 0
            conn.commit()
            return deleted

    def list_groups(self) -> List[RepoGroup]:
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name, created_at FROM repo_groups ORDER BY name ASC")
            group_rows = cursor.fetchall()
            groups = []
            for name, created_at in group_rows:
                cursor.execute(
                    "SELECT repo_id FROM repo_group_members WHERE group_name = ? ORDER BY repo_id ASC",
                    (name,),
                )
                members = [r[0] for r in cursor.fetchall()]
                groups.append(
                    RepoGroup(name=name, created_at=created_at, members=members)
                )
            return groups

    def get_group_members(self, name: str) -> List[str]:
        group_name = name.strip()
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM repo_groups WHERE name = ?", (group_name,))
            if not cursor.fetchone():
                raise UnknownGroupError(group_name)
            cursor.execute(
                "SELECT repo_id FROM repo_group_members WHERE group_name = ? ORDER BY repo_id ASC",
                (group_name,),
            )
            return [r[0] for r in cursor.fetchall()]

    def add_dependency(self, repo_id: str, depends_on_repo_id: str) -> RepoDependency:
        rid = repo_id.strip()
        target_rid = depends_on_repo_id.strip()

        if rid == target_rid:
            raise SelfDependencyError(rid)

        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT repo_id FROM repositories WHERE repo_id = ?", (rid,))
            if not cursor.fetchone():
                raise UnknownRepositoryError(rid)

            cursor.execute(
                "SELECT repo_id FROM repositories WHERE repo_id = ?", (target_rid,)
            )
            if not cursor.fetchone():
                raise UnknownRepositoryError(target_rid)

            # Write-time cycle detection (D3)
            visited: Set[str] = set()
            stack = [target_rid]
            while stack:
                curr = stack.pop()
                if curr == rid:
                    raise DependencyCycleError(rid, target_rid)
                if curr not in visited:
                    visited.add(curr)
                    cursor.execute(
                        "SELECT depends_on_repo_id FROM repo_dependencies WHERE repo_id = ?",
                        (curr,),
                    )
                    for (nxt,) in cursor.fetchall():
                        if nxt not in visited:
                            stack.append(nxt)

            now = time.time()
            cursor.execute(
                """
                INSERT OR IGNORE INTO repo_dependencies (repo_id, depends_on_repo_id, created_at)
                VALUES (?, ?, ?)
            """,
                (rid, target_rid, now),
            )
            conn.commit()
            return RepoDependency(
                repo_id=rid, depends_on_repo_id=target_rid, created_at=now
            )

    def remove_dependency(self, repo_id: str, depends_on_repo_id: str) -> bool:
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM repo_dependencies WHERE repo_id = ? AND depends_on_repo_id = ?",
                (repo_id.strip(), depends_on_repo_id.strip()),
            )
            deleted = cursor.rowcount > 0
            conn.commit()
            return deleted

    def get_dependencies(self, repo_id: str) -> List[str]:
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT depends_on_repo_id FROM repo_dependencies WHERE repo_id = ? ORDER BY depends_on_repo_id ASC",
                (repo_id.strip(),),
            )
            return [r[0] for r in cursor.fetchall()]

    def get_dependents(self, repo_id: str) -> List[str]:
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT repo_id FROM repo_dependencies WHERE depends_on_repo_id = ? ORDER BY repo_id ASC",
                (repo_id.strip(),),
            )
            return [r[0] for r in cursor.fetchall()]

    def get_repo_relations(self, repo_id: str) -> Dict[str, Any]:
        rid = repo_id.strip()
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT repo_id FROM repositories WHERE repo_id = ?", (rid,))
            if not cursor.fetchone():
                raise UnknownRepositoryError(rid)

            cursor.execute(
                "SELECT group_name FROM repo_group_members WHERE repo_id = ? ORDER BY group_name ASC",
                (rid,),
            )
            groups = [r[0] for r in cursor.fetchall()]

            cursor.execute(
                "SELECT depends_on_repo_id FROM repo_dependencies WHERE repo_id = ? ORDER BY depends_on_repo_id ASC",
                (rid,),
            )
            dependencies = [r[0] for r in cursor.fetchall()]

            cursor.execute(
                "SELECT repo_id FROM repo_dependencies WHERE depends_on_repo_id = ? ORDER BY repo_id ASC",
                (rid,),
            )
            dependents = [r[0] for r in cursor.fetchall()]

            return {
                "repo_id": rid,
                "groups": groups,
                "dependencies": dependencies,
                "dependents": dependents,
            }

    def get_repo_root_path(self, repo: Repository) -> Path:
        if repo.source_type == RepoSourceType.LOCAL:
            return Path(repo.url_or_path)
        else:
            # Cloned git repo path in data_dir / repos / <repo_id>
            repo_clone_path = self.data_dir / "repos" / repo.repo_id
            return repo_clone_path

    def clone_or_pull_git_repo(self, repo: Repository) -> Tuple[bool, Optional[str]]:
        """Handles remote git clone or pull."""
        if repo.source_type == RepoSourceType.LOCAL:
            return True, None

        target_dir = self.get_repo_root_path(repo)
        target_dir.parent.mkdir(parents=True, exist_ok=True)

        try:
            if target_dir.exists() and (target_dir / ".git").exists():
                try:
                    subprocess.run(
                        ["git", "fetch", "origin"],
                        cwd=str(target_dir),
                        check=True,
                        capture_output=True,
                    )
                    subprocess.run(
                        ["git", "checkout", repo.branch or "main"],
                        cwd=str(target_dir),
                        check=True,
                        capture_output=True,
                    )
                    subprocess.run(
                        ["git", "pull", "origin", repo.branch or "main"],
                        cwd=str(target_dir),
                        check=True,
                        capture_output=True,
                    )
                except Exception as git_net_err:
                    # Offline / sandbox fallback: use local checkout
                    pass
            else:
                cmd = [
                    "git",
                    "clone",
                    "--branch",
                    repo.branch or "main",
                    repo.url_or_path,
                    str(target_dir),
                ]
                subprocess.run(cmd, check=True, capture_output=True)
            return True, None
        except Exception as e:
            return False, f"Git operation failed: {str(e)}"

    def get_current_git_commit(self, repo_root: Path) -> Optional[str]:
        if not (repo_root / ".git").exists():
            return None
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                check=True,
            )
            return res.stdout.strip()
        except Exception:
            return None

    def _parse_gitignore_patterns(self, root_dir: Path) -> List[str]:
        patterns = []
        gitignore_path = root_dir / ".gitignore"
        if gitignore_path.exists():
            try:
                with open(gitignore_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            patterns.append(line)
            except Exception:
                pass
        return patterns

    def is_path_ignored(self, rel_path: str, gitignore_patterns: List[str]) -> bool:
        parts = Path(rel_path).parts
        if not parts:
            return True

        # Special handling for .github: ONLY .github/skills/* and .github/workflows/* are allowed
        if parts[0] == ".github":
            if len(parts) < 2 or parts[1] not in ALLOWED_GITHUB_SUBDIRS:
                return True
            for part in parts[1:]:
                if part in DEFAULT_IGNORED_DIRS or part.startswith("."):
                    return True
        else:
            for part in parts:
                if part in DEFAULT_IGNORED_DIRS or part.startswith("."):
                    return True

        ext = os.path.splitext(rel_path)[1].lower()
        if ext in DEFAULT_IGNORED_EXTENSIONS:
            return True

        # Check against custom gitignore patterns
        for pattern in gitignore_patterns:
            pattern = pattern.rstrip("/")
            if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(
                os.path.basename(rel_path), pattern
            ):
                return True
            if pattern.startswith("*") and fnmatch.fnmatch(rel_path, f"**/{pattern}"):
                return True

        return False

    def scan_repository_files(
        self, repo: Repository, force: bool = False
    ) -> Tuple[List[str], Dict[str, str], List[str]]:
        """
        Scans repository directory and compares SHA-256 hashes with previous sync.
        If force=True, treats all files as added for re-indexing.
        Returns: (added_files, modified_files, deleted_files)
        """
        root_path = self.get_repo_root_path(repo)
        if not root_path.exists() or not root_path.is_dir():
            raise FileNotFoundError(f"Repository path does not exist: {root_path}")

        gitignore_patterns = self._parse_gitignore_patterns(root_path)

        # Load previous hashes unless forced
        previous_hashes: Dict[str, str] = {}
        if not force:
            with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT file_path, sha256 FROM file_hashes WHERE repo_id = ?",
                    (repo.repo_id,),
                )
                for fpath, shaval in cursor.fetchall():
                    previous_hashes[fpath] = shaval

        current_files: Dict[
            str, Tuple[str, float, int]
        ] = {}  # rel_path -> (sha256, mtime, size)

        for root, dirs, files in os.walk(root_path):
            rel_root = os.path.relpath(root, root_path)
            if rel_root == ".":
                # Root level: allow standard non-dot dirs and .github
                dirs[:] = [
                    d
                    for d in dirs
                    if d not in DEFAULT_IGNORED_DIRS
                    and (not d.startswith(".") or d == ".github")
                ]
            elif rel_root == ".github":
                # Directly inside .github: ONLY descend into allowed subdirectories
                dirs[:] = [d for d in dirs if d in ALLOWED_GITHUB_SUBDIRS]
            else:
                # Any other subfolder: prune dot-dirs and default ignored dirs
                dirs[:] = [
                    d
                    for d in dirs
                    if d not in DEFAULT_IGNORED_DIRS and not d.startswith(".")
                ]

            for file_name in files:
                full_path = Path(root) / file_name
                try:
                    rel_path = str(full_path.relative_to(root_path))
                except ValueError:
                    continue

                if self.is_path_ignored(rel_path, gitignore_patterns):
                    continue

                try:
                    stat = full_path.stat()
                    if stat.st_size > MAX_FILE_SIZE_BYTES:
                        continue

                    with open(full_path, "rb") as f:
                        file_bytes = f.read()
                        sha256 = hashlib.sha256(file_bytes).hexdigest()

                    current_files[rel_path] = (sha256, stat.st_mtime, stat.st_size)
                except Exception:
                    continue

        added_files = []
        modified_files = {}
        deleted_files = []

        for rel_path, (sha, mtime, size) in current_files.items():
            if rel_path not in previous_hashes:
                added_files.append(rel_path)
            elif previous_hashes[rel_path] != sha:
                modified_files[rel_path] = sha

        for prev_path in previous_hashes:
            if prev_path not in current_files:
                deleted_files.append(prev_path)

        return added_files, modified_files, deleted_files

    def commit_file_hashes(
        self,
        repo_id: str,
        current_file_hashes: Dict[str, Tuple[str, float, int]],
        deleted_files: List[str],
    ):
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            for del_path in deleted_files:
                cursor.execute(
                    "DELETE FROM file_hashes WHERE repo_id = ? AND file_path = ?",
                    (repo_id, del_path),
                )

            for rel_path, (sha, mtime, size) in current_file_hashes.items():
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO file_hashes (repo_id, file_path, sha256, last_modified, size_bytes)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    (repo_id, rel_path, sha, mtime, size),
                )
            conn.commit()

    def get_all_indexed_files(self, repo_id: str) -> List[str]:
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT file_path FROM file_hashes WHERE repo_id = ?", (repo_id,)
            )
            return [row[0] for row in cursor.fetchall()]
