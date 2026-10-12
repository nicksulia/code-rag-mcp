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
import json
import logging
import uuid
import contextvars
import shlex
import sys
import tempfile
import functools
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Set, Any, TypedDict
from urllib.parse import urlsplit
from .ssh_support import (
    SSHError, ProtectedFiles, inspect_private_key, has_controls, file_identity,
    Identity, GitContext, run_checked,
)

from ..models.schema import (
    Repository,
    RepoSourceType,
    RepoStatus,
    RepoGroup,
    RepoDependency,
)

logger = logging.getLogger("rag.repository")

_OPERATION_ERRORS = {
    "git_authentication_failed": (500, "Git authentication failed. Configure unattended credentials or an unlocked SSH agent outside this operation, then retry."),
    "git_host_key_failed": (500, "Git host verification failed. Verify the host and configure trusted known hosts outside this operation, then retry."),
    "git_connection_failed": (500, "Git could not connect to the remote. Check network access and remote configuration, then retry."),
    "git_timeout": (504, "Git exceeded its execution deadline. Check connectivity and unattended authentication, then retry."),
    "git_unavailable": (503, "Git could not be started. Install Git and make it available to the server, then retry."),
    "git_command_failed": (500, "A required Git command failed. Check the checkout and remote configuration, then retry."),
    "checkout_unavailable": (400, "No usable existing Git checkout with a valid HEAD is available. Complete a remote sync before indexing locally."),
    "invalid_sync_options": (400, "Sync requires an object with only optional boolean force and local_only fields."),
    "repository_not_found": (404, "The requested repository is not registered. Check the repository catalog."),
    "repository_operation_failed": (500, "The repository operation failed. Check the catalog and server configuration before retrying."),
    "ssh_key_invalid": (400, "Use a valid name and an absolute server-side OpenSSH private-key path owned by the server account, readable only by its owner."),
    "ssh_key_not_found": (404, "The selected SSH key profile does not exist. Refresh the key catalog and select an existing profile."),
    "ssh_key_conflict": (409, "This private-key path or public fingerprint is already registered."),
    "ssh_key_in_use": (409, "The SSH key is assigned to repositories. Clear their assignments before removing the reference."),
    "ssh_assignment_invalid": (400, "Select an existing key only for a credential-free SSH Git remote. Explicitly clear the assignment when switching to another transport."),
    "ssh_key_indexed": (409, "The private file is already represented in an index. Remove its source/index entry before registration; registration does not clean up indexed secrets."),
    "ssh_key_changed": (409, "The referenced key identity has changed. Register a new profile and explicitly reassign the repository."),
    "ssh_key_unavailable": (400, "The referenced key is missing or insecure. Restore its ownership, owner-only permissions and OpenSSH format outside the application."),
    "ssh_tool_unavailable": (503, "Required OpenSSH tools are unavailable. Install ssh and ssh-keygen for the server account."),
    "ssh_key_timeout": (504, "SSH key inspection exceeded its deadline. Check the server-side file and OpenSSH installation."),
    "ssh_config_unsupported": (400, "The effective SSH configuration cannot be safely isolated. Use supported OpenSSH configuration and retry."),
    "ssh_override_conflict": (400, "Managed SSH keys cannot use a custom Git SSH launcher. Remove the override or explicitly select System SSH."),
    "ssh_http_forbidden": (403, "SSH credentials and key management require a verified loopback HTTP listener. Use trusted local CLI/stdio or a matching localhost tunnel."),
    "ssh_key_body_too_large": (413, "SSH key metadata requests must not exceed 8192 bytes."),
}
_OPERATION_PHASES = {
    "clone", "fetch", "checkout", "pull", "set_remote", "inspect_checkout",
    "index", "save_configuration",
    "ssh_catalog", "ssh_validation", "ssh_configuration", "http_policy",
}


class RepositoryOperationError(RuntimeError):
    """Credential-safe repository failure; no process diagnostics are retained."""

    def __init__(
        self, code: str = "repository_operation_failed", phase: Optional[str] = None,
        repo_id: Optional[str] = None, config_saved: Optional[bool] = None,
        exit_code: Optional[int] = None,
        repo_ids: Optional[List[str]] = None,
    ):
        self.code = code if code in _OPERATION_ERRORS else "repository_operation_failed"
        self.http_status, message = _OPERATION_ERRORS[self.code]
        self.phase = phase if phase in _OPERATION_PHASES else None
        # A selection can be a credential-bearing URL, not a catalog identifier.
        self.repo_id = repo_id if isinstance(repo_id, str) and re.fullmatch(r"[^:/@\\\x00-\x1f\x7f]{1,128}", repo_id) else None
        self.config_saved = config_saved
        self.exit_code = exit_code
        self.repo_ids = repo_ids
        super().__init__(message)

    def to_dict(self, include_config_saved: bool = True) -> Dict[str, Any]:
        result: Dict[str, Any] = {"error": str(self), "code": self.code}
        if self.phase is not None:
            result["phase"] = self.phase
        if self.repo_id is not None:
            result["repo_id"] = self.repo_id
        if include_config_saved and self.config_saved is not None:
            result["config_saved"] = self.config_saved
        if self.repo_ids is not None:
            result["repo_ids"] = self.repo_ids
        return result


class GitOperationError(RepositoryOperationError):
    def __init__(self, code: str, phase: str, repo_id: Optional[str] = None,
                 exit_code: Optional[int] = None, missing_branch: bool = False):
        super().__init__(code, phase, repo_id, exit_code=exit_code)
        self.missing_branch = missing_branch


def safe_operation_error(
    error: Exception, repo_id: Optional[str] = None, phase: str = "index",
    config_saved: Optional[bool] = None,
) -> RepositoryOperationError:
    """Project unexpected failures without logging exception text or tracebacks."""
    if isinstance(error, RepositoryOperationError):
        safe = error
    else:
        safe = RepositoryOperationError(phase=phase, repo_id=repo_id)
        logger.error("Repository operation failed: repo=%s phase=%s code=%s type=%s",
                     safe.repo_id, safe.phase, safe.code, type(error).__name__)
    if config_saved is not None:
        safe.config_saved = config_saved
    return safe


def validate_sync_options(force: bool, local_only: bool) -> None:
    if not isinstance(force, bool) or not isinstance(local_only, bool):
        raise RepositoryOperationError("invalid_sync_options")


def _classify_git_diagnostic(diagnostic: str) -> str:
    diagnostic = diagnostic[:8192].lower()
    signatures = (
        ("git_host_key_failed", ("host key verification failed", "remote host identification has changed", "no matching host key")),
        ("git_authentication_failed", ("permission denied (publickey", "authentication failed", "could not read username", "could not read password", "access denied", "terminal prompts disabled")),
        ("git_connection_failed", ("could not resolve host", "could not resolve hostname", "connection refused", "network is unreachable", "connection timed out", "connection reset")),
    )
    for code, patterns in signatures:
        if any(pattern in diagnostic for pattern in patterns):
            return code
    return "git_command_failed"


def _run_git(
    arguments: List[str], phase: str, cwd: Optional[Path] = None,
    repo_id: Optional[str] = None,
    context: Optional[GitContext] = None,
) -> subprocess.CompletedProcess:
    """Run checked, noninteractive Git: 120s remote / 15s local deadlines.

    Inherit agent and user configuration. Custom SSH/credential launchers must
    already support unattended use; never rewrite them or user configuration.
    """
    env = os.environ.copy()
    env.update({
        "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/usr/bin/false",
        "SSH_ASKPASS": "/usr/bin/false", "SSH_ASKPASS_REQUIRE": "never",
        "GCM_INTERACTIVE": "never",
    })
    identity = context.identity if context else None
    if context is not None and not context.ssh_allowed:
        if identity is not None:
            raise RepositoryOperationError("ssh_http_forbidden", "http_policy", repo_id)
        env["GIT_ALLOW_PROTOCOL"] = "https:http:file:git"
    if identity is not None:
        if env.get("GIT_SSH_COMMAND") or env.get("GIT_SSH") or env.get("GIT_SSH_VARIANT", "ssh") != "ssh":
            raise RepositoryOperationError("ssh_override_conflict", "ssh_configuration", repo_id)
        try:
            override = run_checked(
                ["git", "config", "--get", "core.sshCommand"], cwd=str(cwd) if cwd else None,
                check=False, timeout=15, env=env,
            )
            if override.returncode not in (0, 1):
                raise RepositoryOperationError("ssh_config_unsupported", "ssh_configuration", repo_id)
            if override.returncode == 0:
                raise RepositoryOperationError("ssh_override_conflict", "ssh_configuration", repo_id)
            inspect_private_key(identity.private_key_path, identity.fingerprint)
        except SSHError as error:
            raise RepositoryOperationError(error.code, "ssh_validation", repo_id) from None
        except subprocess.TimeoutExpired:
            raise RepositoryOperationError("git_timeout", phase, repo_id) from None
        except OSError:
            raise RepositoryOperationError("git_unavailable", phase, repo_id) from None
        env["GIT_ALLOW_PROTOCOL"] = "ssh"
    if phase == "inspect_checkout":
        # Partial checkouts must not fetch objects while inspecting cached HEAD.
        env["GIT_NO_LAZY_FETCH"] = "1"
        env["GIT_ALLOW_PROTOCOL"] = ""
    timeout = 120 if phase in {"clone", "fetch", "pull"} else 15
    with tempfile.TemporaryDirectory(prefix="rag-git-") as temporary:
        directory = Path(temporary)
        if identity is not None:
            profile_file = directory / "profile.json"
            profile_file.write_text(json.dumps({
                "private_key_path": identity.private_key_path, "fingerprint": identity.fingerprint,
            }), encoding="utf-8")
            profile_file.chmod(0o600)
            launcher = Path(__file__).with_name("ssh_support.py").resolve()
            env["GIT_SSH_COMMAND"] = shlex.join([sys.executable, "-I", str(launcher), str(directory)])
            env["GIT_SSH_VARIANT"] = "ssh"
        elif not env.get("GIT_SSH_COMMAND") and not env.get("GIT_SSH"):
            if os.name == "posix":
                fallback = directory / "system-ssh"
                fallback.write_text(
                    '#!/bin/sh\nexec ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$@"\n',
                    encoding="ascii",
                )
                fallback.chmod(0o700)
                env.pop("GIT_SSH_COMMAND", None)
                # Git's core.sshCommand takes precedence over GIT_SSH.
                env["GIT_SSH"] = str(fallback)
            else:
                env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes"
        try:
            return run_checked(
                ["git", *arguments], cwd=str(cwd) if cwd is not None else None,
                check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                timeout=timeout, env=env, start_new_session=True,
                encoding="utf-8", errors="replace",
            )
        except subprocess.CalledProcessError as error:
            diagnostic = (error.stderr or "")[:8192]
            if isinstance(diagnostic, bytes):
                diagnostic = diagnostic.decode("utf-8", errors="replace")
            code = _classify_git_diagnostic(diagnostic)
            phase_for_error = phase
            if (directory / "error").exists():
                code = (directory / "error").read_text(encoding="ascii")
                phase_for_error = "ssh_validation" if code.startswith("ssh_key") else "ssh_configuration"
            elif context is not None and not context.ssh_allowed and "transport 'ssh' not allowed" in diagnostic.lower():
                code, phase_for_error = "ssh_http_forbidden", "http_policy"
            elif identity is not None and re.search(r"transport '.+' not allowed", diagnostic.lower()):
                code, phase_for_error = "ssh_config_unsupported", "ssh_configuration"
            missing_branch = (
                phase == "checkout" and code == "git_command_failed"
                and re.search(r"pathspec '.+?' did not match any file\(s\) known to git", diagnostic[:8192]) is not None
            )
            safe = GitOperationError(code, phase_for_error, repo_id, error.returncode, missing_branch)
        except subprocess.TimeoutExpired:
            safe = GitOperationError("git_timeout", phase, repo_id)
        except OSError as error:
            code = "git_unavailable" if isinstance(error, (FileNotFoundError, PermissionError)) and error.filename in (None, "git") else "git_command_failed"
            safe = GitOperationError(code, phase, repo_id)
    logger.error("Git failed: repo=%s phase=%s code=%s exit=%s",
                 safe.repo_id, safe.phase, safe.code, safe.exit_code)
    raise safe from None


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


PROJECT_FILE_LIMIT = 1_048_576
PROJECT_REQUEST_LIMIT = 2_097_152
REPOSITORY_FIELDS = ("repo_id", "name", "source_type", "url_or_path", "branch")


class TransferValidationError(ValueError):
    """Configuration cannot be represented by the transfer contract."""


class TransferConflictError(Exception):
    def __init__(self, summary: "TransferSummary"):
        self.summary = summary
        super().__init__(
            "Repository configuration conflicts: "
            + ", ".join(conflict["repo_id"] for conflict in summary["conflicts"])
        )


class RepositoryConfig(TypedDict):
    repo_id: str
    name: str
    source_type: str
    url_or_path: str
    branch: Optional[str]


class SSHAssignment(TypedDict):
    """None means clear; an absent update object preserves the binding."""

    ssh_key_id: Optional[str]


class SSHKeyProfile(TypedDict):
    key_id: str
    name: str
    private_key_path: str
    fingerprint: str
    algorithm: str
    repo_ids: List[str]


def validate_ssh_name(name: Any) -> str:
    if not isinstance(name, str) or has_controls(name):
        raise RepositoryOperationError("ssh_key_invalid", "ssh_validation")
    name = name.strip()
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        raise RepositoryOperationError("ssh_key_invalid", "ssh_validation") from None
    if not 1 <= len(name) <= 80:
        raise RepositoryOperationError("ssh_key_invalid", "ssh_validation")
    return name


def is_ssh_remote(address: str) -> bool:
    try:
        _validate_git_address(address, "url_or_path")
    except (TransferValidationError, ValueError):
        return False
    return address.startswith("ssh://") or "://" not in address


def _manager_operation(method):
    @functools.wraps(method)
    def operation(self, *args, **kwargs):
        with self.git_operation():
            return method(self, *args, **kwargs)
    return operation


class GroupConfig(TypedDict):
    name: str
    members: List[str]


class DependencyConfig(TypedDict):
    repo_id: str
    depends_on_repo_id: str


class ProjectDocument(TypedDict):
    version: int
    repositories: List[RepositoryConfig]
    groups: List[GroupConfig]
    dependencies: List[DependencyConfig]


class RepositoryAdditions(TypedDict):
    count: int
    ids: List[str]


class GroupAdditions(TypedDict):
    count: int
    names: List[str]


class MembershipEntry(TypedDict):
    group: str
    repo_id: str


class MembershipAdditions(TypedDict):
    count: int
    entries: List[MembershipEntry]


class DependencyAdditions(TypedDict):
    count: int
    entries: List[DependencyConfig]


class TransferAdditions(TypedDict):
    repositories: RepositoryAdditions
    groups: GroupAdditions
    memberships: MembershipAdditions
    dependencies: DependencyAdditions


class TransferConflict(TypedDict):
    repo_id: str
    fields: List[str]


class TransferWarning(TypedDict):
    repo_id: str
    code: str


class TransferSummary(TypedDict):
    can_import: bool
    additions: TransferAdditions
    unchanged_repositories: List[str]
    conflicts: List[TransferConflict]
    warnings: List[TransferWarning]


@dataclass
class CatalogConfiguration:
    document: ProjectDocument
    repository_keys: Dict[str, str]
    group_keys: Dict[str, str]


def _transfer_object(value: Any, fields: Set[str], location: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise TransferValidationError(
            f"{location} must contain exactly {', '.join(sorted(fields))}."
        )
    return value


def _transfer_text(value: Any, location: str, preserve: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TransferValidationError(f"{location} must be a nonempty string.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise TransferValidationError(
            f"{location} must contain valid Unicode."
        ) from None
    return value if preserve else value.strip()


def _transfer_id(value: Any, location: str) -> str:
    value = _transfer_text(value, location)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise TransferValidationError(
            f"{location} must match [A-Za-z0-9][A-Za-z0-9._-]*."
        )
    return value


def _validate_git_address(address: str, location: str) -> None:
    valid = False
    if not re.search(r"[\s\x00-\x1f\x7f]", address):
        if "://" in address:
            try:
                parsed = urlsplit(address)
                # Accessing port also detects malformed/non-numeric ports.
                port = parsed.port
                valid = (
                    parsed.scheme in {"http", "https", "ssh", "git"}
                    and bool(parsed.hostname)
                    and bool(parsed.path.strip("/"))
                    and not parsed.query
                    and not parsed.fragment
                    and "?" not in address
                    and "#" not in address
                    and (port is None or port > 0)
                    and (
                        parsed.username is None
                        or (
                            parsed.scheme == "ssh"
                            and bool(parsed.username)
                            and parsed.password is None
                        )
                    )
                )
            except ValueError:
                valid = False
        else:
            valid = bool(
                re.fullmatch(
                    r"(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9][A-Za-z0-9.-]*:(?!:|//)[^\s?#]+",
                    address,
                )
            )
    if not valid:
        raise TransferValidationError(
            f"{location} must be a supported credential-free Git address "
            "(HTTP(S), SSH, git://, or scp-style); remove credentials or unsupported syntax."
        )


def normalize_project_document(value: Any) -> ProjectDocument:
    document = _transfer_object(
        value, {"version", "repositories", "groups", "dependencies"}, "document"
    )
    if (
        not isinstance(document["version"], int)
        or isinstance(document["version"], bool)
        or document["version"] != 1
    ):
        raise TransferValidationError("version must be integer 1.")
    for field in ("repositories", "groups", "dependencies"):
        if not isinstance(document[field], list):
            raise TransferValidationError(f"{field} must be an array.")
    repositories: List[RepositoryConfig] = []
    repo_ids: Set[str] = set()
    for index, entry in enumerate(document["repositories"]):
        location = f"repositories[{index}]"
        entry = _transfer_object(entry, set(REPOSITORY_FIELDS), location)
        rid = _transfer_id(entry["repo_id"], f"{location}.repo_id")
        location = f"Repository '{rid}'"
        if rid in repo_ids:
            raise TransferValidationError(f"Duplicate repository ID '{rid}'.")
        repo_ids.add(rid)
        source = _transfer_text(entry["source_type"], f"{location}.source_type")
        if source not in {"local", "git"}:
            raise TransferValidationError(
                f"{location}.source_type must be local or git."
            )
        address = _transfer_text(
            entry["url_or_path"], f"{location}.url_or_path", preserve=source == "local"
        )
        if source == "git":
            _validate_git_address(address, f"{location}.url_or_path")
        repositories.append(
            {
                "repo_id": rid,
                "name": _transfer_text(entry["name"], f"{location}.name"),
                "source_type": source,
                "url_or_path": address,
                "branch": None
                if entry["branch"] is None
                else _transfer_text(entry["branch"], f"{location}.branch"),
            }
        )
    groups: List[GroupConfig] = []
    names: Set[str] = set()
    for index, entry in enumerate(document["groups"]):
        location = f"groups[{index}]"
        entry = _transfer_object(entry, {"name", "members"}, location)
        name = _transfer_text(entry["name"], f"{location}.name")
        if name in names:
            raise TransferValidationError(f"{location}.name duplicates another group.")
        names.add(name)
        if not isinstance(entry["members"], list):
            raise TransferValidationError(f"{location}.members must be an array.")
        members = [
            _transfer_id(member, f"{location}.members[{i}]")
            for i, member in enumerate(entry["members"])
        ]
        if len(members) != len(set(members)):
            raise TransferValidationError(
                f"{location}.members contains repeated repository IDs."
            )
        groups.append({"name": name, "members": sorted(members)})
    dependencies: List[DependencyConfig] = []
    edges: Set[Tuple[str, str]] = set()
    for index, entry in enumerate(document["dependencies"]):
        location = f"dependencies[{index}]"
        entry = _transfer_object(entry, {"repo_id", "depends_on_repo_id"}, location)
        edge = (
            _transfer_id(entry["repo_id"], f"{location}.repo_id"),
            _transfer_id(entry["depends_on_repo_id"], f"{location}.depends_on_repo_id"),
        )
        if edge in edges:
            raise TransferValidationError(
                f"Duplicate dependency '{edge[0]}' -> '{edge[1]}'."
            )
        edges.add(edge)
        dependencies.append({"repo_id": edge[0], "depends_on_repo_id": edge[1]})
    return {
        "version": 1,
        "repositories": sorted(repositories, key=lambda repo: repo["repo_id"]),
        "groups": sorted(groups, key=lambda group: group["name"]),
        "dependencies": sorted(
            dependencies, key=lambda edge: (edge["repo_id"], edge["depends_on_repo_id"])
        ),
    }


def validate_dependency_graph(repo_ids: Set[str], edges: Set[Tuple[str, str]]) -> None:
    adjacency: Dict[str, List[str]] = {rid: [] for rid in repo_ids}
    for rid, target in sorted(edges):
        if rid == target:
            raise SelfDependencyError(rid)
        for endpoint in (rid, target):
            if endpoint not in repo_ids:
                raise UnknownRepositoryError(endpoint)
        adjacency[rid].append(target)
    visited: Set[str] = set()
    active: Set[str] = set()
    for root in sorted(repo_ids):
        if root in visited:
            continue
        stack = [(root, iter(adjacency[root]))]
        active.add(root)
        while stack:
            rid, children = stack[-1]
            target = next(children, None)
            if target is None:
                stack.pop()
                active.remove(rid)
                visited.add(rid)
            elif target in active:
                raise DependencyCycleError(rid, target)
            elif target not in visited:
                active.add(target)
                stack.append((target, iter(adjacency[target])))


def _validate_configuration_relations(
    document: ProjectDocument, repo_ids: Set[str], edges: Set[Tuple[str, str]]
) -> None:
    for group in document["groups"]:
        for member in group["members"]:
            if member not in repo_ids:
                raise UnknownRepositoryError(member)
    validate_dependency_graph(repo_ids, edges)


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
        self.ssh_network_allowed = True
        self._git_operation = contextvars.ContextVar("git_operation", default=None)
        self._init_db()

    @contextlib.contextmanager
    def git_operation(self, repo: Optional[Repository] = None):
        current = self._git_operation.get()
        if current is not None:
            yield current
            return
        current = GitContext(ssh_allowed=self.ssh_network_allowed)
        if repo is not None:
            self._capture_identity(current, repo)
        token = self._git_operation.set(current)
        try:
            yield current
        finally:
            self._git_operation.reset(token)

    def _capture_identity(self, context: GitContext, repo: Repository) -> None:
        if context.captured:
            return
        context.repo_id = repo.repo_id
        if repo.ssh_key_id is not None:
            profile = self.get_ssh_key(repo.ssh_key_id)
            context.identity = Identity(profile["private_key_path"], profile["fingerprint"])
        context.captured = True

    def _git(self, arguments: List[str], phase: str, repo: Repository, cwd: Optional[Path] = None):
        current = self._git_operation.get()
        if current is None:
            with self.git_operation(repo):
                return self._git(arguments, phase, repo, cwd)
        self._capture_identity(current, repo)
        if not current.ssh_allowed and (current.identity is not None or is_ssh_remote(repo.url_or_path)):
            raise RepositoryOperationError("ssh_http_forbidden", "http_policy", repo.repo_id)
        return _run_git(arguments, phase, cwd, repo.repo_id, current)

    def require_ssh_management(self) -> None:
        if not self.ssh_network_allowed:
            raise RepositoryOperationError("ssh_http_forbidden", "http_policy")

    def has_ssh_keys(self) -> bool:
        with contextlib.closing(self._connect()) as conn:
            return conn.execute("SELECT 1 FROM ssh_key_profiles LIMIT 1").fetchone() is not None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def list_ssh_keys(self) -> List[SSHKeyProfile]:
        """Machine-local profile metadata, never part of project transfer."""
        self.require_ssh_management()
        with contextlib.closing(self._connect()) as conn:
            return [self._ssh_profile(conn, row) for row in conn.execute(
                "SELECT key_id, name, private_key_path, fingerprint, algorithm "
                "FROM ssh_key_profiles ORDER BY name, key_id"
            )]

    def register_ssh_key(
        self, name: str, private_key_path: str,
        indexed_paths: Optional[List[Tuple[str, str]]] = None,
    ) -> SSHKeyProfile:
        self.require_ssh_management()
        name = validate_ssh_name(name)
        try:
            path, fingerprint, algorithm = inspect_private_key(private_key_path)
        except SSHError as error:
            raise RepositoryOperationError(error.code, "ssh_validation") from None
        key_id = uuid.uuid4().hex
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._reject_indexed_key(conn, path)
            identity = file_identity(path)
            for rid, rel_path in indexed_paths or []:
                repo = self.get_repo(rid)
                if repo is not None:
                    candidate = self.get_repo_root_path(repo) / rel_path
                    if candidate.resolve() == path or file_identity(candidate) == identity:
                        raise RepositoryOperationError("ssh_key_indexed", "ssh_validation")
            try:
                conn.execute(
                    "INSERT INTO ssh_key_profiles VALUES (?, ?, ?, ?, ?, ?)",
                    (key_id, name, str(path), fingerprint, algorithm, time.time()),
                )
            except sqlite3.IntegrityError:
                raise RepositoryOperationError("ssh_key_conflict", "ssh_catalog") from None
        return self.get_ssh_key(key_id)

    def _reject_indexed_key(self, conn: sqlite3.Connection, path: Path) -> None:
        protection = ProtectedFiles([str(path)])
        for rid, source, address, rel_path in conn.execute(
            "SELECT r.repo_id, r.source_type, r.url_or_path, h.file_path "
            "FROM repositories r JOIN file_hashes h ON r.repo_id = h.repo_id"
        ):
            root = Path(address) if source == "local" else self.repos_dir / rid
            candidate = root / rel_path
            # Compare identity, not markers: other private files do not make
            # this candidate already indexed.
            if candidate.resolve() == path or file_identity(candidate) in protection.identities:
                raise RepositoryOperationError("ssh_key_indexed", "ssh_validation")

    def protected_files(self) -> ProtectedFiles:
        with contextlib.closing(self._connect()) as conn:
            return ProtectedFiles(path for (path,) in conn.execute(
                "SELECT private_key_path FROM ssh_key_profiles"
            ))

    @staticmethod
    def _ssh_profile(conn: sqlite3.Connection, row: tuple) -> SSHKeyProfile:
        return SSHKeyProfile(
            key_id=row[0], name=row[1], private_key_path=row[2],
            fingerprint=row[3], algorithm=row[4],
            repo_ids=[rid for (rid,) in conn.execute(
                "SELECT repo_id FROM repo_ssh_keys WHERE key_id = ? ORDER BY repo_id",
                (row[0],),
            )],
        )

    def get_ssh_key(self, key_id: str) -> SSHKeyProfile:
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT key_id, name, private_key_path, fingerprint, algorithm "
                "FROM ssh_key_profiles WHERE key_id = ?", (key_id,),
            ).fetchone()
            if row is None:
                raise RepositoryOperationError("ssh_key_not_found", "ssh_catalog")
            return self._ssh_profile(conn, row)

    def rename_ssh_key(self, key_id: str, name: str) -> SSHKeyProfile:
        self.require_ssh_management()
        name = validate_ssh_name(name)
        with contextlib.closing(self._connect()) as conn, conn:
            if not conn.execute(
                "UPDATE ssh_key_profiles SET name = ? WHERE key_id = ?", (name, key_id),
            ).rowcount:
                raise RepositoryOperationError("ssh_key_not_found", "ssh_catalog")
        return self.get_ssh_key(key_id)

    def delete_ssh_key(self, key_id: str) -> None:
        """Remove an unused reference only; never unlink user files."""
        self.require_ssh_management()
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM ssh_key_profiles WHERE key_id = ?", (key_id,)).fetchone() is None:
                raise RepositoryOperationError("ssh_key_not_found", "ssh_catalog")
            repo_ids = [rid for (rid,) in conn.execute(
                "SELECT repo_id FROM repo_ssh_keys WHERE key_id = ? ORDER BY repo_id", (key_id,),
            )]
            if repo_ids:
                raise RepositoryOperationError("ssh_key_in_use", "ssh_catalog", repo_ids=repo_ids)
            conn.execute("DELETE FROM ssh_key_profiles WHERE key_id = ?", (key_id,))

    def save_configuration(self, repo: Repository, assignment: Optional[SSHAssignment] = None) -> None:
        """Atomically save metadata and an explicit machine-local binding change.

        Omitted assignment preserves; null clears; a string selects a profile.
        Runtime saves never touch bindings, even from stale Repository objects.
        """
        if assignment is not None:
            self.require_ssh_management()
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT key_id FROM repo_ssh_keys WHERE repo_id = ?", (repo.repo_id,),
            ).fetchone()
            key_id = current[0] if current else None
            if assignment is not None:
                if not isinstance(assignment, dict) or set(assignment) != {"ssh_key_id"}:
                    raise RepositoryOperationError("ssh_assignment_invalid", "save_configuration")
                key_id = assignment["ssh_key_id"]
                if key_id is not None and (not isinstance(key_id, str) or not key_id):
                    raise RepositoryOperationError("ssh_assignment_invalid", "save_configuration")
            if key_id is not None:
                if repo.source_type != RepoSourceType.GIT or not is_ssh_remote(repo.url_or_path):
                    raise RepositoryOperationError("ssh_assignment_invalid", "save_configuration")
                if conn.execute("SELECT 1 FROM ssh_key_profiles WHERE key_id = ?", (key_id,)).fetchone() is None:
                    raise RepositoryOperationError("ssh_key_not_found", "save_configuration")
            self._save_repo(conn, repo)
            if assignment is not None:
                conn.execute("DELETE FROM repo_ssh_keys WHERE repo_id = ?", (repo.repo_id,))
                if key_id is not None:
                    conn.execute("INSERT INTO repo_ssh_keys (repo_id, key_id) VALUES (?, ?)", (repo.repo_id, key_id))
            repo.ssh_key_id = key_id
            selected = conn.execute(
                "SELECT private_key_path, fingerprint FROM ssh_key_profiles WHERE key_id = ?", (key_id,),
            ).fetchone() if key_id else None
        operation = self._git_operation.get()
        if operation is not None:
            operation.repo_id, operation.captured = repo.repo_id, True
            operation.identity = Identity(*selected) if selected else None

    def _configuration_snapshot(self, conn: sqlite3.Connection) -> CatalogConfiguration:
        rows = conn.execute(
            "SELECT repo_id, name, source_type, url_or_path, branch FROM repositories"
        ).fetchall()
        raw_repo_ids = {row[0] for row in rows}
        raw_groups = {
            name: [] for (name,) in conn.execute("SELECT name FROM repo_groups")
        }
        for name, rid in conn.execute(
            "SELECT group_name, repo_id FROM repo_group_members"
        ):
            if name not in raw_groups:
                raise TransferValidationError(
                    "repo_group_members.group_name references a missing group."
                )
            if rid not in raw_repo_ids:
                raise UnknownRepositoryError(
                    _transfer_id(rid, "catalog.group.members.repo_id")
                )
            raw_groups[name].append(rid)
        raw_edges = conn.execute(
            "SELECT repo_id, depends_on_repo_id FROM repo_dependencies"
        ).fetchall()
        for edge in raw_edges:
            for endpoint in edge:
                if endpoint not in raw_repo_ids:
                    raise UnknownRepositoryError(
                        _transfer_id(endpoint, "catalog.dependencies.repo_id")
                    )
        document = normalize_project_document(
            {
                "version": 1,
                "repositories": [dict(zip(REPOSITORY_FIELDS, row)) for row in rows],
                "groups": [
                    {"name": name, "members": members}
                    for name, members in raw_groups.items()
                ],
                "dependencies": [
                    {"repo_id": rid, "depends_on_repo_id": target}
                    for rid, target in raw_edges
                ],
            }
        )
        keys = {_transfer_id(row[0], "catalog.repo_id"): row[0] for row in rows}
        group_keys = {
            _transfer_text(name, "catalog.group.name"): name for name in raw_groups
        }
        edges = {
            (edge["repo_id"], edge["depends_on_repo_id"])
            for edge in document["dependencies"]
        }
        _validate_configuration_relations(document, set(keys), edges)
        return CatalogConfiguration(document, keys, group_keys)

    def export_projects(self) -> bytes:
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn, conn:
            conn.execute("BEGIN")
            document = self._configuration_snapshot(conn).document
        body = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        if len(body) > PROJECT_FILE_LIMIT:
            raise TransferValidationError(
                f"Export exceeds the {PROJECT_FILE_LIMIT}-byte file limit."
            )
        return body

    def _plan_project_import(
        self, document: ProjectDocument, catalog: CatalogConfiguration
    ) -> TransferSummary:
        existing = {repo["repo_id"]: repo for repo in catalog.document["repositories"]}
        groups = {
            group["name"]: set(group["members"]) for group in catalog.document["groups"]
        }
        edges = {
            (edge["repo_id"], edge["depends_on_repo_id"])
            for edge in catalog.document["dependencies"]
        }
        incoming_edges = {
            (edge["repo_id"], edge["depends_on_repo_id"])
            for edge in document["dependencies"]
        }
        repo_ids = set(existing) | {
            repo["repo_id"] for repo in document["repositories"]
        }
        _validate_configuration_relations(document, repo_ids, edges | incoming_edges)
        new_ids: List[str] = []
        unchanged: List[str] = []
        conflicts: List[TransferConflict] = []
        for repo in document["repositories"]:
            rid = repo["repo_id"]
            if rid not in existing:
                new_ids.append(rid)
            else:
                previous = dict(existing[rid])
                fields = [
                    field
                    for field, value in repo.items()
                    if field != "repo_id" and value != previous[field]
                ]
                if fields:
                    conflicts.append({"repo_id": rid, "fields": fields})
                else:
                    unchanged.append(rid)
        new_groups = [
            group["name"] for group in document["groups"] if group["name"] not in groups
        ]
        memberships: List[MembershipEntry] = [
            {"group": group["name"], "repo_id": rid}
            for group in document["groups"]
            for rid in group["members"]
            if rid not in groups.get(group["name"], set())
        ]
        dependencies: List[DependencyConfig] = [
            {"repo_id": rid, "depends_on_repo_id": target}
            for rid, target in sorted(incoming_edges - edges)
        ]
        return {
            "can_import": not conflicts,
            "additions": {
                "repositories": {"count": len(new_ids), "ids": new_ids},
                "groups": {"count": len(new_groups), "names": new_groups},
                "memberships": {"count": len(memberships), "entries": memberships},
                "dependencies": {"count": len(dependencies), "entries": dependencies},
            },
            "unchanged_repositories": unchanged,
            "conflicts": conflicts,
            "warnings": [
                {"repo_id": repo["repo_id"], "code": "local_path_machine_specific"}
                for repo in document["repositories"]
                if repo["source_type"] == "local"
            ],
        }

    def preview_project_import(self, value: Any) -> TransferSummary:
        document = normalize_project_document(value)
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn, conn:
            conn.execute("BEGIN")
            return self._plan_project_import(
                document, self._configuration_snapshot(conn)
            )

    def import_projects(self, value: Any) -> TransferSummary:
        document = normalize_project_document(value)
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            catalog = self._configuration_snapshot(conn)
            summary = self._plan_project_import(document, catalog)
            if not summary["can_import"]:
                raise TransferConflictError(summary)
            additions = summary["additions"]
            new_ids = set(additions["repositories"]["ids"])
            for repo in document["repositories"]:
                if repo["repo_id"] in new_ids:
                    conn.execute(
                        """
                        INSERT INTO repositories (
                            repo_id, name, source_type, url_or_path, branch,
                            commit_hash, total_files, total_chunks, total_symbols,
                            status, last_synced_at, error_message
                        ) VALUES (?, ?, ?, ?, ?, NULL, 0, 0, 0, ?, NULL, NULL)
                    """,
                        (
                            repo["repo_id"],
                            repo["name"],
                            repo["source_type"],
                            repo["url_or_path"],
                            repo["branch"],
                            RepoStatus.READY.value,
                        ),
                    )
                    catalog.repository_keys[repo["repo_id"]] = repo["repo_id"]
            now = time.time()
            for name in additions["groups"]["names"]:
                conn.execute(
                    "INSERT INTO repo_groups (name, created_at) VALUES (?, ?)",
                    (name, now),
                )
                catalog.group_keys[name] = name
            for member in additions["memberships"]["entries"]:
                conn.execute(
                    "INSERT INTO repo_group_members (group_name, repo_id) VALUES (?, ?)",
                    (
                        catalog.group_keys[member["group"]],
                        catalog.repository_keys[member["repo_id"]],
                    ),
                )
            for edge in additions["dependencies"]["entries"]:
                conn.execute(
                    """
                    INSERT INTO repo_dependencies (repo_id, depends_on_repo_id, created_at)
                    VALUES (?, ?, ?)
                """,
                    (
                        catalog.repository_keys[edge["repo_id"]],
                        catalog.repository_keys[edge["depends_on_repo_id"]],
                        now,
                    ),
                )
            return summary

    def _init_db(self):
        with contextlib.closing(self._connect()) as conn:
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
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ssh_key_profiles (
                    key_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    private_key_path TEXT NOT NULL UNIQUE,
                    fingerprint TEXT NOT NULL UNIQUE,
                    algorithm TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS repo_ssh_keys (
                    repo_id TEXT PRIMARY KEY REFERENCES repositories(repo_id) ON DELETE CASCADE,
                    key_id TEXT NOT NULL REFERENCES ssh_key_profiles(key_id) ON DELETE RESTRICT
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
        assignment: Optional[SSHAssignment] = None,
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
        self.save_configuration(repo, assignment)
        return repo

    def save_repo(self, repo: Repository):
        """UPSERT runtime metadata without deleting machine-local associations."""
        with contextlib.closing(self._connect()) as conn, conn:
            self._save_repo(conn, repo)

    @staticmethod
    def _save_repo(conn: sqlite3.Connection, repo: Repository) -> None:
        conn.execute(
            """
            INSERT INTO repositories (
                repo_id, name, source_type, url_or_path, branch,
                commit_hash, total_files, total_chunks, total_symbols,
                status, last_synced_at, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo_id) DO UPDATE SET
                name=excluded.name, source_type=excluded.source_type,
                url_or_path=excluded.url_or_path, branch=excluded.branch,
                commit_hash=excluded.commit_hash, total_files=excluded.total_files,
                total_chunks=excluded.total_chunks, total_symbols=excluded.total_symbols,
                status=excluded.status, last_synced_at=excluded.last_synced_at,
                error_message=excluded.error_message
            """,
            (
                repo.repo_id, repo.name,
                repo.source_type.value if isinstance(repo.source_type, RepoSourceType) else repo.source_type,
                repo.url_or_path, repo.branch, repo.commit_hash,
                repo.total_files, repo.total_chunks, repo.total_symbols,
                repo.status.value if isinstance(repo.status, RepoStatus) else repo.status,
                repo.last_synced_at, repo.error_message,
            ),
        )

    def get_repo(self, repo_id: str) -> Optional[Repository]:
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT r.*, k.key_id FROM repositories r LEFT JOIN repo_ssh_keys k "
                "ON r.repo_id = k.repo_id WHERE r.repo_id = ?", (repo_id,),
            )
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
                ssh_key_id=row[12],
            )

    def list_repos(self) -> List[Repository]:
        with contextlib.closing(sqlite3.connect(str(self.db_path))) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT r.*, k.key_id FROM repositories r LEFT JOIN repo_ssh_keys k "
                "ON r.repo_id = k.repo_id ORDER BY r.name ASC"
            )
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
                        ssh_key_id=row[12],
                    )
                )
            return repos

    @_manager_operation
    def update_repo(
        self,
        repo_id: str,
        url_or_path: Optional[str] = None,
        branch: Optional[str] = None,
        name: Optional[str] = None,
        assignment: Optional[SSHAssignment] = None,
    ) -> Repository:
        """Save desired configuration, then perform checked Git updates.

        A failed update retains configuration and prior indexed state, not a
        rollback of completed working-tree operations.
        """
        repo = self.get_repo(repo_id)
        if not repo:
            raise RepositoryOperationError("repository_not_found", repo_id=repo_id, config_saved=False)

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

        try:
            self.save_configuration(repo, assignment)
        except Exception as error:
            raise safe_operation_error(error, repo_id, "save_configuration", False) from None

        try:
            if repo.source_type == RepoSourceType.GIT and (url_changed or branch_changed):
                self.switch_git_branch_or_url(
                    repo,
                    new_url=repo.url_or_path if url_changed else None,
                    new_branch=repo.branch if branch_changed else None,
                )
        except Exception as error:
            safe = safe_operation_error(error, repo_id, "index", True)
            repo.status = RepoStatus.ERROR
            repo.error_message = str(safe)
            try:
                self.save_repo(repo)
            except Exception as save_error:
                raise safe_operation_error(save_error, repo_id, "save_configuration", True) from None
            raise safe from None

        return repo

    @_manager_operation
    def switch_git_branch_or_url(
        self,
        repo: Repository,
        new_url: Optional[str] = None,
        new_branch: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Update remote/branch using checked, noninteractive Git.

        Fetch/pull have 120s deadlines; set-remote/checkout have 15s deadlines.
        Typed safe failures stop all dependent commands.
        """
        dest_dir = self.repos_dir / repo.repo_id
        if not dest_dir.exists():
            return self.clone_or_pull_git_repo(repo)

        if new_url:
            self._git(["remote", "set-url", "origin", new_url], "set_remote", repo, dest_dir)
        if new_branch:
            self._git(["fetch", "origin", new_branch], "fetch", repo, dest_dir)
            self._checkout_branch(repo, dest_dir, new_branch)
            self._git(["pull", "origin", new_branch], "pull", repo, dest_dir)
        return True, None

    def _checkout_branch(self, repo: Repository, root: Path, branch: str) -> None:
        try:
            self._git(["checkout", branch], "checkout", repo, root)
        except GitOperationError as error:
            if not error.missing_branch:
                raise
            self._git(["checkout", "-b", branch, f"origin/{branch}"], "checkout", repo, root)

    def delete_repo(self, repo_id: str) -> bool:
        with contextlib.closing(self._connect()) as conn, conn:
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

            repo_ids = {row[0] for row in cursor.execute("SELECT repo_id FROM repositories")}
            edges = set(cursor.execute("SELECT repo_id, depends_on_repo_id FROM repo_dependencies"))
            validate_dependency_graph(repo_ids, edges | {(rid, target_rid)})

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

    @_manager_operation
    def clone_or_pull_git_repo(self, repo: Repository) -> Tuple[bool, Optional[str]]:
        """Prepare Git with checked failures, never fall back to cached indexing.

        Clone/fetch/pull: 120s; checkout: 15s. Input/prompts are disabled while
        inherited unattended SSH agents and configuration remain available.
        """
        if repo.source_type == RepoSourceType.LOCAL:
            return True, None

        target_dir = self.get_repo_root_path(repo)
        target_dir.parent.mkdir(parents=True, exist_ok=True)

        if target_dir.exists() and (target_dir / ".git").exists():
            self._git(["fetch", "origin"], "fetch", repo, target_dir)
            self._checkout_branch(repo, target_dir, repo.branch or "main")
            self._git(["pull", "origin", repo.branch or "main"], "pull", repo, target_dir)
        else:
            self._git(["clone", "--branch", repo.branch or "main", repo.url_or_path, str(target_dir)], "clone", repo)
        return True, None

    def get_current_git_commit(self, repo_root: Path, repo_id: Optional[str] = None) -> Optional[str]:
        """Inspect actual HEAD with a checked noninteractive 15s Git deadline."""
        if not (repo_root / ".git").exists():
            return None
        res = _run_git(["rev-parse", "--verify", "HEAD^{commit}"], "inspect_checkout", repo_root, repo_id)
        commit = res.stdout.strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", commit):
            raise RepositoryOperationError("checkout_unavailable", "inspect_checkout", repo_id)
        return commit

    def inspect_existing_checkout(self, repo: Repository) -> str:
        """Require an existing checkout/HEAD; never access remote or switch branch."""
        root = self.get_repo_root_path(repo)
        if not root.is_dir() or not (root / ".git").exists():
            raise RepositoryOperationError("checkout_unavailable", "inspect_checkout", repo.repo_id)
        try:
            commit = self.get_current_git_commit(root, repo.repo_id)
        except GitOperationError as error:
            if error.code != "git_command_failed":
                raise
            raise RepositoryOperationError("checkout_unavailable", "inspect_checkout", repo.repo_id) from None
        if not commit:
            raise RepositoryOperationError("checkout_unavailable", "inspect_checkout", repo.repo_id)
        return commit

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
        protected_files = self.protected_files()

        # Forced scans still need prior paths to remove protected/deleted files.
        previous_hashes: Dict[str, str] = {}
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

                if self.is_path_ignored(rel_path, gitignore_patterns) or protected_files.contains(full_path):
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
            if force or rel_path not in previous_hashes:
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
