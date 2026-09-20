"""
Unit tests for HybridRetriever and Reciprocal Rank Fusion.
"""

import unittest
import shutil
from pathlib import Path
from unittest.mock import MagicMock
from src.indexer.vector_store import VectorStore
from src.indexer.lexical_store import BM25LexicalStore
from src.indexer.graph_store import GraphStore
from src.retriever.hybrid_retriever import HybridRetriever
from src.models.schema import CodeChunk, ChunkType


class TestHybridRetriever(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("./test_data_retriever")
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

        self.vec_store = VectorStore(data_dir=str(self.test_dir))
        self.lex_store = BM25LexicalStore(data_dir=str(self.test_dir))
        self.graph_store = GraphStore(data_dir=str(self.test_dir))
        self.retriever = HybridRetriever(
            self.vec_store, self.lex_store, self.graph_store
        )

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_rrf_retrieval(self):
        chunks = [
            CodeChunk(
                chunk_id="chunk_1",
                repo_id="repo-a",
                file_path="src/auth.py",
                language="python",
                start_line=1,
                end_line=20,
                raw_content="def authenticate_user(token: str): pass",
                enriched_content="// repo-a src/auth.py\ndef authenticate_user(token: str): pass",
                symbol_name="authenticate_user",
                chunk_type=ChunkType.FUNCTION,
            ),
            CodeChunk(
                chunk_id="chunk_2",
                repo_id="repo-b",
                file_path="src/billing.py",
                language="python",
                start_line=1,
                end_line=20,
                raw_content="def process_payment(amount: float): pass",
                enriched_content="// repo-b src/billing.py\ndef process_payment(amount: float): pass",
                symbol_name="process_payment",
                chunk_type=ChunkType.FUNCTION,
            ),
        ]

        self.vec_store.add_chunks(chunks)
        self.lex_store.add_chunks(chunks)

        # Query auth
        results = self.retriever.search("authenticate_user token")
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0].chunk.chunk_id, "chunk_1")

        # Query billing
        b_results = self.retriever.search("process payment charge")
        self.assertTrue(len(b_results) > 0)
        self.assertEqual(b_results[0].chunk.chunk_id, "chunk_2")


def _make_chunk(chunk_id, file_path, symbol_name=None):
    return CodeChunk(
        chunk_id=chunk_id,
        repo_id="repo-a",
        file_path=file_path,
        language="python",
        start_line=1,
        end_line=10,
        raw_content=f"# {symbol_name or chunk_id}",
        enriched_content=f"# {symbol_name or chunk_id}",
        symbol_name=symbol_name,
        chunk_type=ChunkType.FUNCTION,
    )


def _mock_retriever(dense_results, sparse_results, **kwargs):
    """HybridRetriever over stubbed dense/sparse arms.

    dense_results: list of CodeChunk (rank = list position).
    sparse_results: list of CodeChunk (rank = list position).
    """
    vec_store = MagicMock()
    lex_store = MagicMock()
    graph_store = MagicMock()
    vec_store.search_vector.return_value = [(c, 1.0) for c in dense_results]
    by_id = {c.chunk_id: c for c in list(dense_results) + list(sparse_results)}
    lex_store.search_bm25.return_value = [
        (c.chunk_id, 1.0, []) for c in sparse_results
    ]
    vec_store.get_chunk.side_effect = lambda cid: by_id.get(cid)
    graph_store.get_symbol_centrality.return_value = 0.0
    graph_store.get_callers.return_value = []
    graph_store.get_callees.return_value = []
    # Equal arm weights so "rank 1 in dense" == "rank 1 in sparse".
    kwargs.setdefault("dense_weight", 1.0)
    kwargs.setdefault("sparse_weight", 1.0)
    return HybridRetriever(vec_store, lex_store, graph_store, **kwargs)


class TestClassPenaltyDemotion(unittest.TestCase):
    def test_equally_ranked_source_outranks_test(self):
        source = _make_chunk("src_chunk", "src/catalog.py", "create_catalog")
        test = _make_chunk("test_chunk", "tests/test_catalog.py", "test_create_catalog")
        # Equal base RRF: source is dense rank 1, test is sparse rank 1.
        retriever = _mock_retriever([source], [test])
        results = retriever.search("create catalog", top_k=8)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].chunk.chunk_id, "src_chunk")
        self.assertEqual(results[1].chunk.chunk_id, "test_chunk")
        self.assertAlmostEqual(
            results[1].score, round(results[0].score * 0.65, 6), places=5
        )

    def test_example_symbol_in_test_file_is_not_demoted(self):
        source = _make_chunk("src_chunk", "src/catalog.go", "CreateCatalog")
        example = _make_chunk(
            "example_chunk", "catalog_test.go", "ExampleCreateCatalog"
        )
        # Equal base RRF for both; Example* must keep the source penalty (1.0).
        retriever = _mock_retriever([source], [example])
        results = retriever.search("create catalog", top_k=8)
        self.assertEqual(len(results), 2)
        scores = {r.chunk.chunk_id: r.score for r in results}
        self.assertAlmostEqual(scores["src_chunk"], scores["example_chunk"], places=6)

    def test_dominant_test_chunk_still_appears(self):
        # Test chunk dominates both arms (rank 1 in each); source chunks are
        # far down the dense list only. Soft demotion must not filter it out.
        test = _make_chunk("test_chunk", "tests/test_catalog.py", "test_create_catalog")
        sources = [
            _make_chunk(f"src_{i}", f"src/module_{i}.py", f"helper_{i}")
            for i in range(10)
        ]
        retriever = _mock_retriever([test] + sources, [test])
        results = retriever.search("create catalog", top_k=8)
        result_ids = [r.chunk.chunk_id for r in results]
        self.assertIn("test_chunk", result_ids)


if __name__ == "__main__":
    unittest.main()
