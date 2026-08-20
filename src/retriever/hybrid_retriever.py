"""
Advanced Hybrid Search Retriever.
Combines Multi-Vector Dense Similarity, Field-Weighted BM25F Lexical Scores,
Code Graph Centrality, and 1-hop Caller/Callee neighborhoods via Reciprocal Rank Fusion (RRF).
"""

import logging
from typing import List, Dict, Tuple, Optional, Any
from ..models.schema import CodeChunk, SearchResult
from ..indexer.vector_store import VectorStore
from ..indexer.lexical_store import BM25LexicalStore
from ..indexer.graph_store import GraphStore

logger = logging.getLogger("rag.retriever")


class HybridRetriever:
    def __init__(
        self,
        vector_store: VectorStore,
        lexical_store: BM25LexicalStore,
        graph_store: GraphStore,
        rrf_k: int = 60,
        dense_weight: float = 1.0,
        sparse_weight: float = 0.85,
        hop_decay: float = 0.85,
    ):
        self.vector_store = vector_store
        self.lexical_store = lexical_store
        self.graph_store = graph_store
        self.rrf_k = rrf_k
        self.dense_weight = dense_weight
        self.sparse_weight = sparse_weight
        self.hop_decay = hop_decay

    def search(
        self,
        query: str,
        repo_ids: Optional[List[str]] = None,
        expanded_repos: Optional[Dict[str, Any]] = None,
        top_k: int = 8,
    ) -> List[SearchResult]:
        if not query or not query.strip():
            return []

        try:
            top_k_int = int(top_k) if top_k is not None else 8
            if top_k_int <= 0:
                top_k_int = 8
        except (ValueError, TypeError):
            top_k_int = 8

        logger.debug(
            f"Executing hybrid search: query='{query}', repo_filter={repo_ids}, expanded={expanded_repos}, top_k={top_k_int}"
        )

        # 1. Fetch multi-vector dense candidates
        dense_results = self.vector_store.search_vector(
            query, repo_ids=repo_ids, limit=top_k_int * 3, sig_weight=0.6
        )
        logger.debug(f"Dense vector retrieval returned {len(dense_results)} candidates")

        # 2. Fetch BM25F field-weighted lexical candidates
        sparse_results = self.lexical_store.search_bm25(
            query, repo_ids=repo_ids, limit=top_k_int * 3
        )
        logger.debug(
            f"BM25F lexical retrieval returned {len(sparse_results)} candidates"
        )

        # Mapping chunk_id -> scores & ranks
        chunk_map: Dict[str, CodeChunk] = {}
        dense_rank_map: Dict[str, int] = {}
        sparse_rank_map: Dict[str, int] = {}
        matched_terms_map: Dict[str, List[str]] = {}

        for rank, (chunk, score) in enumerate(dense_results, start=1):
            chunk_map[chunk.chunk_id] = chunk
            dense_rank_map[chunk.chunk_id] = rank

        for rank, (chunk_id, bm_score, terms) in enumerate(sparse_results, start=1):
            sparse_rank_map[chunk_id] = rank
            matched_terms_map[chunk_id] = terms
            if chunk_id not in chunk_map:
                c = self.vector_store.get_chunk(chunk_id)
                if c:
                    chunk_map[chunk_id] = c

        # 3. Calculate Reciprocal Rank Fusion (RRF) with Graph Centrality
        fused_scores: Dict[str, float] = {}
        for chunk_id, chunk in chunk_map.items():
            dense_r = dense_rank_map.get(chunk_id)
            sparse_r = sparse_rank_map.get(chunk_id)

            rrf = 0.0
            if dense_r is not None:
                rrf += self.dense_weight / (self.rrf_k + dense_r)
            if sparse_r is not None:
                rrf += self.sparse_weight / (self.rrf_k + sparse_r)

            # Boost if query matches symbol name exactly or partially
            if chunk.symbol_name:
                q_lower = query.lower()
                sym_lower = chunk.symbol_name.lower()
                if sym_lower in q_lower or q_lower in sym_lower:
                    rrf += 0.020

                # Code Graph Centrality boost (shared libraries & cross-repo endpoints)
                centrality = self.graph_store.get_symbol_centrality(
                    chunk.symbol_name, owning_repo=chunk.repo_id
                )
                rrf += centrality

            # Apply multiplicative hop decay penalty for expanded repositories (D5)
            if expanded_repos and chunk.repo_id in expanded_repos:
                exp_info = expanded_repos[chunk.repo_id]
                hops = (
                    exp_info.hops
                    if hasattr(exp_info, "hops")
                    else (exp_info[1] if isinstance(exp_info, tuple) else 1)
                )
                rrf *= self.hop_decay ** max(1, hops)

            fused_scores[chunk_id] = rrf

        # Sort by final score descending
        sorted_chunks = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)[
            :top_k
        ]

        # 4. Enrich with graph call relationships & relation provenance
        results: List[SearchResult] = []
        for chunk_id, score in sorted_chunks:
            chunk = chunk_map[chunk_id]
            callers: List[Dict[str, Any]] = []
            callees: List[Dict[str, Any]] = []

            if chunk.symbol_name:
                callers = self.graph_store.get_callers(chunk.symbol_name, repo_id=None)[
                    :3
                ]
                callees = self.graph_store.get_callees(
                    chunk.symbol_name, repo_id=chunk.repo_id
                )[:3]

            repo_relation = "primary"
            relation_dir = None
            relation_hops = None
            if expanded_repos and chunk.repo_id in expanded_repos:
                repo_relation = "expanded"
                exp_info = expanded_repos[chunk.repo_id]
                if hasattr(exp_info, "direction"):
                    relation_dir = (
                        exp_info.direction.value
                        if hasattr(exp_info.direction, "value")
                        else str(exp_info.direction)
                    )
                    relation_hops = exp_info.hops
                elif isinstance(exp_info, tuple):
                    relation_dir = (
                        exp_info[0].value
                        if hasattr(exp_info[0], "value")
                        else str(exp_info[0])
                    )
                    relation_hops = exp_info[1]

            results.append(
                SearchResult(
                    chunk=chunk,
                    score=round(score, 6),
                    dense_rank=dense_rank_map.get(chunk_id),
                    sparse_rank=sparse_rank_map.get(chunk_id),
                    matched_terms=matched_terms_map.get(chunk_id, []),
                    related_callers=callers,
                    related_callees=callees,
                    repo_relation=repo_relation,
                    relation_direction=relation_dir,
                    relation_hops=relation_hops,
                )
            )

        return results
