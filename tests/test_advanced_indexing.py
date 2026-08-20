"""
Unit tests for Advanced Multi-Vector & BM25F Indexing Engine.
"""

import unittest
import shutil
from pathlib import Path
from src.models.schema import CodeChunk, ChunkType, CallEdge
from src.indexer.embeddings import AdvancedSubwordNeuralEngine, EmbeddingFactory
from src.indexer.vector_store import VectorStore
from src.indexer.lexical_store import BM25LexicalStore
from src.indexer.graph_store import GraphStore
from src.retriever.hybrid_retriever import HybridRetriever


class TestAdvancedIndexing(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("./test_data_adv_index")
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

        self.embedding_engine = AdvancedSubwordNeuralEngine(dim=384)
        self.vec_store = VectorStore(
            data_dir=str(self.test_dir), embedding_engine=self.embedding_engine
        )
        self.bm25f_store = BM25LexicalStore(data_dir=str(self.test_dir))
        self.graph_store = GraphStore(data_dir=str(self.test_dir))
        self.retriever = HybridRetriever(
            self.vec_store, self.bm25f_store, self.graph_store
        )

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_advanced_subword_neural_engine(self):
        self.assertEqual(self.embedding_engine.dimension, 384)
        vec1 = self.embedding_engine.encode_text(
            "def authenticate_jwt_token(user_id: str)", field_type="signature"
        )
        vec2 = self.embedding_engine.encode_text(
            "jwt authentication user token verification", field_type="general"
        )
        vec3 = self.embedding_engine.encode_text(
            "database sql migration create table", field_type="general"
        )

        self.assertEqual(len(vec1), 384)
        self.assertEqual(len(vec2), 384)

        # Dot product / cosine similarity
        from src.indexer.vector_store import cosine_similarity

        sim_related = cosine_similarity(vec1, vec2)
        sim_unrelated = cosine_similarity(vec1, vec3)

        self.assertGreater(sim_related, sim_unrelated)

    def test_dual_vector_retrieval(self):
        chunk1 = CodeChunk(
            chunk_id="c_jwt",
            repo_id="auth-svc",
            file_path="src/jwt.py",
            language="python",
            start_line=1,
            end_line=15,
            raw_content="def issue_token(user: str): return 'token'",
            enriched_content="// auth-svc src/jwt.py\ndef issue_token(user: str): return 'token'",
            symbol_name="issue_token",
            chunk_type=ChunkType.FUNCTION,
            docstring="Generates cryptographic JWT token for user authentication",
        )
        self.vec_store.add_chunks([chunk1])

        # Search for conceptual intent matching docstring/signature
        results = self.vec_store.search_vector(
            "cryptographic user authentication token"
        )
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0][0].chunk_id, "c_jwt")

    def test_bm25f_field_weighting(self):
        # Chunk A has "billing" in symbol name
        chunk_a = CodeChunk(
            chunk_id="chunk_symbol_match",
            repo_id="repo-1",
            file_path="src/billing.py",
            language="python",
            start_line=1,
            end_line=10,
            raw_content="def billing_engine(): pass",
            enriched_content="def billing_engine(): pass",
            symbol_name="billing_engine",
            chunk_type=ChunkType.FUNCTION,
        )
        # Chunk B has "billing" only inside a generic comment in code body
        chunk_b = CodeChunk(
            chunk_id="chunk_body_match",
            repo_id="repo-2",
            file_path="src/misc.py",
            language="python",
            start_line=1,
            end_line=10,
            raw_content="def setup_queue(): # this is related to billing sometimes\n pass",
            enriched_content="def setup_queue(): # this is related to billing sometimes\n pass",
            symbol_name="setup_queue",
            chunk_type=ChunkType.FUNCTION,
        )

        self.bm25f_store.add_chunks([chunk_a, chunk_b])

        results = self.bm25f_store.search_bm25("billing")
        self.assertTrue(len(results) >= 2)
        # Symbol match must outrank body comment match due to BM25F field weighting
        self.assertEqual(results[0][0], "chunk_symbol_match")
        self.assertGreater(results[0][1], results[1][1])

    def test_graph_centrality_boosting(self):
        # Symbol with high in-degree / cross-repo calls
        edge1 = CallEdge(
            edge_id="e1",
            source_repo="frontend-app",
            source_file="src/client.ts",
            source_symbol="login",
            target_repo="auth-service",
            target_file="src/routes.py",
            target_symbol="authenticate_user",
            edge_type="CROSS_REPO_API",
            line_number=10,
        )
        edge2 = CallEdge(
            edge_id="e2",
            source_repo="mobile-app",
            source_file="src/auth.dart",
            source_symbol="login",
            target_repo="auth-service",
            target_file="src/routes.py",
            target_symbol="authenticate_user",
            edge_type="CROSS_REPO_API",
            line_number=25,
        )
        self.graph_store.add_symbols_and_edges([], [edge1, edge2])

        centrality = self.graph_store.get_symbol_centrality(
            "authenticate_user", owning_repo="auth-service"
        )
        self.assertGreater(centrality, 0.0)

        # Unused symbol
        unused_centrality = self.graph_store.get_symbol_centrality(
            "unused_helper_func", owning_repo="auth-service"
        )
        self.assertEqual(unused_centrality, 0.0)


if __name__ == "__main__":
    unittest.main()
