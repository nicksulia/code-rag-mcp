"""
Data schemas and domain models for Multi-Repository Code RAG.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
from enum import Enum
import json


class ChunkType(str, Enum):
    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    INTERFACE = "interface"
    STRUCT = "struct"
    MODULE = "module"
    DOC = "doc"
    CONFIG = "config"
    BLOCK = "block"


class RepoSourceType(str, Enum):
    LOCAL = "local"
    GIT = "git"


class RepoStatus(str, Enum):
    READY = "ready"
    INDEXING = "indexing"
    ERROR = "error"
    DIRTY = "dirty"


class RelationDirection(str, Enum):
    NONE = "none"
    UPSTREAM = "upstream"
    DOWNSTREAM = "downstream"
    BOTH = "both"


@dataclass
class RepoGroup:
    name: str
    created_at: Optional[float] = None
    members: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RepoDependency:
    repo_id: str
    depends_on_repo_id: str
    created_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExpandedRepo:
    repo_id: str
    direction: RelationDirection
    hops: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "direction": self.direction.value
            if isinstance(self.direction, RelationDirection)
            else self.direction,
            "hops": self.hops,
        }


@dataclass
class ResolvedScope:
    """
    Result of turning a caller's repository request into a concrete repository set.

    `primary` is None when no repositories, groups, or expansion were requested,
    meaning "search everything". An empty list means the request resolved to no
    repositories and retrieval must return nothing.
    """

    primary: Optional[List[str]] = None
    expanded: Dict[str, ExpandedRepo] = field(default_factory=dict)

    @property
    def is_unscoped(self) -> bool:
        return self.primary is None

    @property
    def is_empty(self) -> bool:
        return self.primary is not None and not self.primary and not self.expanded

    @property
    def all_repo_ids(self) -> Optional[List[str]]:
        if self.primary is None:
            return None
        return list(dict.fromkeys(list(self.primary) + list(self.expanded.keys())))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary": list(self.primary) if self.primary is not None else None,
            "expanded": [e.to_dict() for e in self.expanded.values()],
            "all_repo_ids": self.all_repo_ids,
        }


@dataclass
class Repository:
    repo_id: str
    name: str
    source_type: RepoSourceType
    url_or_path: str
    branch: Optional[str] = "main"
    commit_hash: Optional[str] = None
    total_files: int = 0
    total_chunks: int = 0
    total_symbols: int = 0
    status: RepoStatus = RepoStatus.READY
    last_synced_at: Optional[float] = None
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["source_type"] = (
            self.source_type.value
            if isinstance(self.source_type, RepoSourceType)
            else self.source_type
        )
        d["status"] = (
            self.status.value if isinstance(self.status, RepoStatus) else self.status
        )
        return d


@dataclass
class CodeChunk:
    chunk_id: str
    repo_id: str
    file_path: str
    language: str
    start_line: int
    end_line: int
    raw_content: str
    enriched_content: str
    symbol_name: Optional[str] = None
    chunk_type: ChunkType = ChunkType.BLOCK
    parent_symbol: Optional[str] = None
    imports: List[str] = field(default_factory=list)
    docstring: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["chunk_type"] = (
            self.chunk_type.value
            if isinstance(self.chunk_type, ChunkType)
            else self.chunk_type
        )
        return d


@dataclass
class Symbol:
    symbol_id: str
    repo_id: str
    name: str
    kind: str  # function, class, method, interface, struct, endpoint, client_call
    file_path: str
    line_number: int
    signature: Optional[str] = None
    docstring: Optional[str] = None
    parent_symbol: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CallEdge:
    edge_id: str
    source_repo: str
    source_file: str
    source_symbol: str
    target_repo: Optional[str]
    target_file: Optional[str]
    target_symbol: str
    edge_type: str  # CALLS, IMPORTS, IMPLEMENTS, CROSS_REPO_API
    line_number: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SearchResult:
    chunk: CodeChunk
    score: float
    dense_rank: Optional[int] = None
    sparse_rank: Optional[int] = None
    matched_terms: List[str] = field(default_factory=list)
    related_callers: List[Dict[str, Any]] = field(default_factory=list)
    related_callees: List[Dict[str, Any]] = field(default_factory=list)
    repo_relation: str = "primary"
    relation_direction: Optional[str] = None
    relation_hops: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk": self.chunk.to_dict(),
            "score": self.score,
            "dense_rank": self.dense_rank,
            "sparse_rank": self.sparse_rank,
            "matched_terms": self.matched_terms,
            "related_callers": self.related_callers,
            "related_callees": self.related_callees,
            "repo_relation": self.repo_relation,
            "relation_direction": self.relation_direction,
            "relation_hops": self.relation_hops,
        }
