"""
Unit tests for HybridRetriever and Reciprocal Rank Fusion.
"""

import unittest
import shutil
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
