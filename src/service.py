"""
Unified Multi-Repository Code RAG Service.
Coordinates repo ingestion, AST chunking, symbol graph generation, advanced multi-vector indexing, and RAG queries.
"""

import os
import time
import threading
import collections
import logging
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any, Set

from .models.schema import (
    Repository,
    CodeChunk,
    SearchResult,
    RepoStatus,
    RepoSourceType,
    RelationDirection,
    ExpandedRepo,
    ResolvedScope,
    RepoGroup,
    RepoDependency,
)
from .ingestion.repo_manager import RepoManager
from .parser.langchain_chunker import LangChainCodeChunker, ASTChunker
from .parser.symbol_extractor import SymbolExtractor
from .indexer.embeddings import (
    BaseEmbeddingEngine,
    EmbeddingFactory,
    DEFAULT_OLLAMA_EMBEDDING_MODEL,
)
from .indexer.lifecycle import EmbeddingLifecycle, KeepAlivePolicy
from .indexer.index_meta import IndexProvenanceStore
from .indexer.vector_store import VectorStore
from .indexer.lexical_store import BM25LexicalStore
from .indexer.graph_store import GraphStore
from .retriever.hybrid_retriever import HybridRetriever
from .instance_lock import acquire_instance_lock


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"

logger = logging.getLogger("rag.service")


def resolve_data_dir(data_dir: Optional[str] = None) -> str:
    """
    Resolves data_dir:
    1. If explicit custom data_dir is provided (and not defaulted to './data'), use it directly.
    2. If RAG_DATA_DIR is set in env, use it.
    3. If default/None, use canonical project data directory.
    """
    if data_dir is not None and str(data_dir) != "./data" and str(data_dir) != "":
        return str(Path(data_dir).resolve())

    if os.environ.get("RAG_DATA_DIR"):
        return str(Path(os.environ["RAG_DATA_DIR"]).resolve())

    if DEFAULT_DATA_DIR.exists():
        return str(DEFAULT_DATA_DIR.resolve())

    return str(Path("./data").resolve())


class ReindexInProgressError(RuntimeError):
    """Raised when a request arrives while the dense index is being rebuilt."""


class MultiRepoRAGService:
    def __init__(
        self,
        data_dir: str = "./data",
        embedding_provider: Optional[str] = None,
        embedding_model: Optional[str] = None,
        ollama_host: Optional[str] = None,
        keep_alive: Optional[Any] = None,
        allow_multi_instance: bool = False,
    ):
        self.data_dir = resolve_data_dir(data_dir)

        # Single-instance ownership of the embedding model lifecycle.
        self.instance_lock = acquire_instance_lock(
            self.data_dir, allow_multi_instance=allow_multi_instance
        )

        self.repo_manager = RepoManager(data_dir=self.data_dir)
        self.chunker = LangChainCodeChunker()
        self.ast_chunker = self.chunker  # Backwards compatibility
        self.symbol_extractor = SymbolExtractor()

        # Pluggable embedding engine (defaults to Ollama with Qwen3-Embedding-0.6B)
        self.embedding_provider = (
            embedding_provider or os.environ.get("EMBEDDING_PROVIDER") or "ollama"
        ).lower()
        self.embedding_engine = EmbeddingFactory.create(
            provider=self.embedding_provider,
            model=embedding_model
            or os.environ.get("OLLAMA_EMBEDDING_MODEL")
            or DEFAULT_OLLAMA_EMBEDDING_MODEL,
            host=ollama_host or os.environ.get("OLLAMA_HOST"),
        )

        # Embedding model residency: loaded on demand, released after use.
        self.embedding_lifecycle = EmbeddingLifecycle(
            engine=self.embedding_engine, policy=KeepAlivePolicy.parse(keep_alive)
        )

        self.provenance = IndexProvenanceStore(self.data_dir)
        if hasattr(self.embedding_engine, "set_dimension_provider"):
            self.embedding_engine.set_dimension_provider(
                lambda model: self.provenance.known_dimension(
                    self.embedding_provider, model
                )
            )

        self.vector_store = VectorStore(
            data_dir=self.data_dir,
            embedding_engine=self.embedding_engine,
            dimension_provider=lambda: self.provenance.known_dimension(
                self.embedding_provider, self.embedding_model_name
            ),
        )
        self._reindex_lock = threading.Lock()
        self._reindexing = False
        self.lexical_store = BM25LexicalStore(data_dir=self.data_dir)
        self.graph_store = GraphStore(data_dir=self.data_dir)
        self.retriever = HybridRetriever(
            vector_store=self.vector_store,
            lexical_store=self.lexical_store,
            graph_store=self.graph_store,
        )

    # ------------------------------------------------------------------
    # Embedding model lifecycle & dense index provenance
    # ------------------------------------------------------------------

    @property
    def embedding_model_name(self) -> Optional[str]:
        return getattr(self.embedding_engine, "model", None)

    def embedding_session(self, reason: str = "embedding"):
        """Context manager keeping the embedding model resident for one operation."""
        return self.embedding_lifecycle.session(reason)

    def embedding_runtime_state(self) -> Dict[str, Any]:
        """Current residency state plus dense index provenance."""
        state = self.embedding_lifecycle.state()
        state["provider"] = self.embedding_provider
        state["index_provenance"] = self.provenance.all()
        return state

    def _record_provenance(self, repo_id: str, dimension: Optional[int] = None):
        dim = dimension
        if dim is None:
            try:
                dim = self.embedding_engine.dimension
            except Exception:
                dim = None
        self.provenance.record(
            repo_id=repo_id,
            provider=self.embedding_provider,
            model=self.embedding_model_name,
            dimension=dim,
        )

    def get_stale_repo_ids(self) -> List[str]:
        """
        Repositories whose stored vectors were produced by a different
        provider/model than the one currently configured.

        Uses provider+model identity only, so this never loads the model.
        """
        indexed = self.vector_store.list_indexed_repo_ids()
        known_dim = self.provenance.known_dimension(
            self.embedding_provider, self.embedding_model_name
        )
        stale = []
        for repo_id in indexed:
            entry = self.provenance.get(repo_id)
            if entry is None:
                # Unknown provenance: adopt current configuration when the
                # recorded dimension for this model is already consistent,
                # otherwise the vectors must be rebuilt.
                if known_dim:
                    self._record_provenance(repo_id, dimension=known_dim)
                    continue
                stale.append(repo_id)
                continue
            if (
                entry.get("provider") != self.embedding_provider
                or entry.get("model") != self.embedding_model_name
            ):
                stale.append(repo_id)
        return stale

    @property
    def is_reindexing(self) -> bool:
        return self._reindexing

    def reindex_stale_repos(
        self, progress_callback: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Re-embeds every repository whose dense vectors came from another model.
        Chunk text, symbol graph, and lexical index are preserved.
        """
        stale = self.get_stale_repo_ids()
        if not stale:
            return {"reindexed": [], "status": "current"}

        if not self._reindex_lock.acquire(blocking=False):
            raise ReindexInProgressError(
                "Dense index rebuild is already in progress for this instance."
            )

        self._reindexing = True
        rebuilt = []
        try:
            logger.info(
                f"Embedding model changed to '{self.embedding_model_name}': "
                f"re-embedding {len(stale)} repository(ies)."
            )
            with self.embedding_session("reindex"):
                for repo_id in stale:
                    result = self.vector_store.reembed_repo(
                        repo_id, progress_callback=progress_callback
                    )
                    # Provenance is written per repository, so an interrupted
                    # rebuild resumes with the repositories still outstanding.
                    self._record_provenance(repo_id, dimension=result.get("dimension"))
                    rebuilt.append(repo_id)
        finally:
            self._reindexing = False
            self._reindex_lock.release()

        return {"reindexed": rebuilt, "status": "rebuilt"}

    def ensure_dense_index_current(self, progress_callback: Optional[Any] = None):
        """
        Guarantees queries never score against vectors from another model.
        The first caller performs the rebuild; concurrent callers are rejected
        with `ReindexInProgressError`.
        """
        if self._reindexing:
            raise ReindexInProgressError(
                "Dense index is being rebuilt for the new embedding model. Please retry shortly."
            )
        if not self.get_stale_repo_ids():
            return
        self.reindex_stale_repos(progress_callback=progress_callback)

    def shutdown(self):
        """Releases the embedding model, pending timers, and the instance lock."""
        self.embedding_lifecycle.shutdown(release=True)
        if self.instance_lock is not None:
            self.instance_lock.release()

    def add_repository(
        self,
        repo_id: str,
        name: str,
        source_type: str,
        url_or_path: str,
        branch: str = "main",
        auto_sync: bool = True,
    ) -> Repository:
        repo = self.repo_manager.register_repo(
            repo_id=repo_id,
            name=name,
            source_type=source_type,
            url_or_path=url_or_path,
            branch=branch,
        )
        if auto_sync:
            self.sync_repository(repo_id)
        return self.repo_manager.get_repo(repo_id) or repo

    def update_repository(
        self,
        repo_id: str,
        url_or_path: Optional[str] = None,
        branch: Optional[str] = None,
        name: Optional[str] = None,
        auto_sync: bool = True,
    ) -> Dict[str, Any]:
        """
        Updates repository branch, URL/path, or display name, and optionally synchronizes.
        """
        resolved = self.resolve_repo_ids([repo_id])
        canonical_id = resolved[0] if resolved else repo_id

        updated_repo = self.repo_manager.update_repo(
            repo_id=canonical_id, url_or_path=url_or_path, branch=branch, name=name
        )

        sync_result = None
        if auto_sync:
            sync_result = self.sync_repository(canonical_id, force=False)
            updated_repo = self.repo_manager.get_repo(canonical_id) or updated_repo

        return {"repo": updated_repo.to_dict(), "sync_result": sync_result}

    def resolve_repo_ids(self, repo_inputs: Optional[List[str]]) -> Optional[List[str]]:
        """
        Resolves repository inputs (which could be slugs, names, or full GitHub URLs)
        to actual registered repo_ids.
        """
        if not repo_inputs:
            return None

        all_repos = self.repo_manager.list_repos()
        resolved: List[str] = []

        for inp in repo_inputs:
            inp_clean = inp.strip().rstrip("/")
            match_found = False
            for r in all_repos:
                # 1. Exact match on repo_id
                if r.repo_id == inp_clean:
                    resolved.append(r.repo_id)
                    match_found = True
                    break
                # 2. Match on name
                if r.name and r.name.lower() == inp_clean.lower():
                    resolved.append(r.repo_id)
                    match_found = True
                    break
                # 3. Match on URL or path suffix
                if r.url_or_path and (
                    inp_clean in r.url_or_path
                    or r.url_or_path in inp_clean
                    or inp_clean.endswith(r.repo_id)
                ):
                    resolved.append(r.repo_id)
                    match_found = True
                    break
                # 4. Partial slug match
                if (
                    inp_clean.lower() in r.repo_id.lower()
                    or r.repo_id.lower() in inp_clean.lower()
                ):
                    resolved.append(r.repo_id)
                    match_found = True
                    break

            if not match_found:
                resolved.append(inp_clean)

        return list(dict.fromkeys(resolved))

    def resolve_scope(
        self,
        repo_ids: Optional[List[str]] = None,
        groups: Optional[List[str]] = None,
        expand: Optional[str] = "none",
        expand_depth: int = 1,
    ) -> ResolvedScope:
        """
        Turns a caller's repository request into a concrete ResolvedScope object.
        Unions explicit repos with group members (primary set), and traverses
        repo_dependencies (expanded set) up to expand_depth.
        """
        if repo_ids is None and not groups:
            return ResolvedScope(primary=None, expanded={})

        primary_repos: List[str] = []

        if repo_ids is not None:
            resolved_explicit = self.resolve_repo_ids(repo_ids) or []
            for rid in resolved_explicit:
                if self.repo_manager.get_repo(rid):
                    if rid not in primary_repos:
                        primary_repos.append(rid)

        if groups:
            for g in groups:
                clean_g = g.strip()
                if not clean_g:
                    continue
                # Raises UnknownGroupError if group does not exist
                members = self.repo_manager.get_group_members(clean_g)
                for m in members:
                    if self.repo_manager.get_repo(m):
                        if m not in primary_repos:
                            primary_repos.append(m)

        # If scope was explicitly restricted but resolved to nothing
        if not primary_repos:
            return ResolvedScope(primary=[], expanded={})

        if not expand or expand_depth <= 0:
            return ResolvedScope(primary=primary_repos, expanded={})

        expand_str = (
            expand.value if hasattr(expand, "value") else str(expand).lower().strip()
        )
        if expand_str in ("none", ""):
            return ResolvedScope(primary=primary_repos, expanded={})

        try:
            direction = RelationDirection(expand_str)
        except ValueError:
            direction = RelationDirection.NONE

        if direction == RelationDirection.NONE:
            return ResolvedScope(primary=primary_repos, expanded={})

        # Bounded BFS expansion over repo_dependencies
        visited: Set[str] = set(primary_repos)
        expanded_map: Dict[str, ExpandedRepo] = {}
        queue: collections.deque[Tuple[str, int]] = collections.deque(
            [(rid, 0) for rid in primary_repos]
        )

        while queue:
            curr_id, curr_hop = queue.popleft()
            if curr_hop < expand_depth:
                neighbors: List[Tuple[str, RelationDirection]] = []
                if direction in (RelationDirection.UPSTREAM, RelationDirection.BOTH):
                    for dep in self.repo_manager.get_dependencies(curr_id):
                        neighbors.append((dep, RelationDirection.UPSTREAM))
                if direction in (RelationDirection.DOWNSTREAM, RelationDirection.BOTH):
                    for dep in self.repo_manager.get_dependents(curr_id):
                        neighbors.append((dep, RelationDirection.DOWNSTREAM))

                for nxt_id, edge_dir in neighbors:
                    if not self.repo_manager.get_repo(nxt_id):
                        continue
                    if nxt_id not in visited:
                        visited.add(nxt_id)
                        eff_dir = (
                            RelationDirection.BOTH
                            if direction == RelationDirection.BOTH
                            else edge_dir
                        )
                        expanded_map[nxt_id] = ExpandedRepo(
                            repo_id=nxt_id, direction=eff_dir, hops=curr_hop + 1
                        )
                        queue.append((nxt_id, curr_hop + 1))

        return ResolvedScope(primary=primary_repos, expanded=expanded_map)

    def sync_repository(
        self, repo_id: str, force: bool = False, progress_callback: Optional[Any] = None
    ) -> Dict[str, Any]:
        """Indexes a repository, holding the embedding model resident for the run."""
        with self.embedding_session(f"index:{repo_id}"):
            result = self._sync_repository_impl(
                repo_id=repo_id, force=force, progress_callback=progress_callback
            )
            self._record_provenance(result["repo_id"])
            return result

    def _sync_repository_impl(
        self, repo_id: str, force: bool = False, progress_callback: Optional[Any] = None
    ) -> Dict[str, Any]:
        repo = self.repo_manager.get_repo(repo_id)
        if not repo:
            # Try to resolve by alias/URL
            resolved_ids = self.resolve_repo_ids([repo_id])
            if resolved_ids and resolved_ids[0] != repo_id:
                repo = self.repo_manager.get_repo(resolved_ids[0])
            if not repo:
                raise ValueError(f"Repository '{repo_id}' not found.")

        repo_id = repo.repo_id
        repo.status = RepoStatus.INDEXING
        self.repo_manager.save_repo(repo)

        # Auto-heal: If chunks are 0, force full scan
        if self.vector_store.count_chunks(repo_id) == 0:
            force = True

        start_time = time.time()

        try:
            if repo.source_type == RepoSourceType.GIT:
                if progress_callback:
                    progress_callback(0, 1, "Checking git repository status...", "git")
                ok, err = self.repo_manager.clone_or_pull_git_repo(repo)
                if not ok:
                    raise RuntimeError(err)

            root_path = self.repo_manager.get_repo_root_path(repo)
            commit = self.repo_manager.get_current_git_commit(root_path)
            if commit:
                repo.commit_hash = commit

            if progress_callback:
                progress_callback(
                    0, 1, "Scanning files and calculating hashes...", "scan"
                )

            added_files, modified_files, deleted_files = (
                self.repo_manager.scan_repository_files(repo, force=force)
            )

            for del_f in deleted_files:
                del_chunk_ids = self.vector_store.delete_file_chunks(repo_id, del_f)
                if del_chunk_ids:
                    self.lexical_store.delete_file_chunks(repo_id, del_chunk_ids)
                self.graph_store.delete_file_data(repo_id, del_f)

            for mod_f in modified_files:
                mod_chunk_ids = self.vector_store.delete_file_chunks(repo_id, mod_f)
                if mod_chunk_ids:
                    self.lexical_store.delete_file_chunks(repo_id, mod_chunk_ids)
                self.graph_store.delete_file_data(repo_id, mod_f)

            files_to_process = added_files + list(modified_files.keys())
            all_chunks: List[CodeChunk] = []
            all_symbols = []
            all_edges = []
            current_hashes: Dict[str, Tuple[str, float, int]] = {}
            total_files = len(files_to_process)

            for idx, rel_file in enumerate(files_to_process, start=1):
                if progress_callback:
                    progress_callback(idx, total_files, rel_file, "chunk_and_embed")

                full_path = root_path / rel_file
                if not full_path.exists():
                    continue

                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()

                    chunks = self.chunker.chunk_file(repo_id, rel_file, content)
                    all_chunks.extend(chunks)

                    lang = self.chunker.detect_language(rel_file)
                    syms, edges = self.symbol_extractor.extract_symbols_and_edges(
                        repo_id, rel_file, content, lang
                    )
                    all_symbols.extend(syms)
                    all_edges.extend(edges)

                    stat = full_path.stat()
                    import hashlib

                    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    current_hashes[rel_file] = (sha, stat.st_mtime, stat.st_size)

                except Exception as ex:
                    print(f"Error processing file {rel_file}: {ex}")
                    continue

            if progress_callback:
                progress_callback(
                    total_files,
                    total_files,
                    f"Persisting {len(all_chunks)} chunks to vector index...",
                    "index",
                )

            if all_chunks:
                self.vector_store.add_chunks(all_chunks)
                self.lexical_store.add_chunks(all_chunks)
            if all_symbols or all_edges:
                self.graph_store.add_symbols_and_edges(all_symbols, all_edges)

            self.repo_manager.commit_file_hashes(repo_id, current_hashes, deleted_files)

            repo.total_files = len(self.repo_manager.get_all_indexed_files(repo_id))
            repo.total_chunks = self.vector_store.count_chunks(repo_id)
            repo.total_symbols = self.graph_store.count_symbols(repo_id)
            repo.status = RepoStatus.READY
            repo.last_synced_at = time.time()
            repo.error_message = None
            self.repo_manager.save_repo(repo)

            elapsed = round(time.time() - start_time, 2)

            return {
                "repo_id": repo_id,
                "status": "synced",
                "added_files": len(added_files),
                "modified_files": len(modified_files),
                "deleted_files": len(deleted_files),
                "total_chunks": repo.total_chunks,
                "total_symbols": repo.total_symbols,
                "embedding_dimension": self.embedding_engine.dimension,
                "elapsed_seconds": elapsed,
            }

        except Exception as e:
            repo.status = RepoStatus.ERROR
            repo.error_message = str(e)
            self.repo_manager.save_repo(repo)
            raise e

    def delete_repository(self, repo_id: str) -> bool:
        self.vector_store.delete_repo_chunks(repo_id)
        self.lexical_store.delete_repo_chunks(repo_id)
        self.graph_store.delete_repo_data(repo_id)
        return self.repo_manager.delete_repo(repo_id)

    def list_repositories(self) -> List[Repository]:
        return self.repo_manager.list_repos()

    def search(
        self,
        query: str,
        repo_ids: Optional[List[str]] = None,
        groups: Optional[List[str]] = None,
        expand: Optional[str] = "none",
        expand_depth: int = 1,
        top_k: int = 8,
    ) -> List[SearchResult]:
        try:
            top_k_int = int(top_k) if top_k is not None else 8
            if top_k_int <= 0:
                top_k_int = 8
        except (ValueError, TypeError):
            top_k_int = 8

        scope = self.resolve_scope(
            repo_ids=repo_ids, groups=groups, expand=expand, expand_depth=expand_depth
        )
        if scope.is_empty:
            return []

        self.ensure_dense_index_current()
        with self.embedding_session("search"):
            return self.retriever.search(
                query=query,
                repo_ids=scope.all_repo_ids,
                expanded_repos=scope.expanded,
                top_k=top_k_int,
            )

    def get_symbol_info(
        self, symbol_name: str, repo_id: Optional[str] = None
    ) -> Dict[str, Any]:
        defs = self.graph_store.get_symbol_definition(symbol_name, repo_id)
        callers = self.graph_store.get_callers(symbol_name, repo_id)
        callees = self.graph_store.get_callees(symbol_name, repo_id) if repo_id else []
        return {
            "symbol_name": symbol_name,
            "definitions": [d.to_dict() for d in defs],
            "callers": callers,
            "callees": callees,
        }

    def get_cross_repo_api_links(self) -> List[Dict[str, Any]]:
        return self.graph_store.get_cross_repo_api_links()

    def get_file_content(self, repo_id: str, file_path: str) -> Optional[str]:
        repo = self.repo_manager.get_repo(repo_id)
        if not repo:
            return None
        root = self.repo_manager.get_repo_root_path(repo)
        target = root / file_path
        if not target.exists() or not target.is_file():
            return None
        with open(target, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    def unload_models(self, force: bool = False) -> Dict[str, Any]:
        """
        Manual release of the embedding and LLM models from Ollama / GPU VRAM.

        An in-flight embedding or search operation is never interrupted: the
        result reports `busy` instead (unless `force` is set, e.g. on shutdown).
        """
        results: Dict[str, Any] = {}
        if self.embedding_lifecycle.is_busy and not force:
            results["embedding_unloaded"] = False
            results["busy"] = True
            results["active_operations"] = self.embedding_lifecycle.active_operations
        else:
            results["embedding_unloaded"] = self.embedding_lifecycle.release(
                reason="manual", force=force
            )
            results["busy"] = False
        return results
