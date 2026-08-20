import unittest
import tempfile
import shutil
from unittest.mock import MagicMock

from src.service import MultiRepoRAGService
from src.ingestion.repo_manager import UnknownGroupError
from src.models.schema import RelationDirection, CodeChunk, SearchResult, ChunkType


class TestScopeResolution(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.service = MultiRepoRAGService(data_dir=self.temp_dir)

        # Register repositories:
        # service-a -> shared-lib -> core-utils
        # service-b -> shared-lib
        # isolated-repo
        self.service.repo_manager.register_repo(
            "service-a", "Service A", "local", "/tmp/service-a"
        )
        self.service.repo_manager.register_repo(
            "service-b", "Service B", "local", "/tmp/service-b"
        )
        self.service.repo_manager.register_repo(
            "shared-lib", "Shared Lib", "local", "/tmp/shared-lib"
        )
        self.service.repo_manager.register_repo(
            "core-utils", "Core Utils", "local", "/tmp/core-utils"
        )
        self.service.repo_manager.register_repo(
            "isolated", "Isolated", "local", "/tmp/isolated"
        )

        # Set up relations:
        self.service.repo_manager.add_dependency("service-a", "shared-lib")
        self.service.repo_manager.add_dependency("shared-lib", "core-utils")
        self.service.repo_manager.add_dependency("service-b", "shared-lib")

        # Groups:
        self.service.repo_manager.create_group("platform", ["service-a", "service-b"])
        self.service.repo_manager.create_group("empty-group", [])

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_default_unscoped_behaviour(self):
        scope = self.service.resolve_scope()
        self.assertTrue(scope.is_unscoped)
        self.assertIsNone(scope.primary)
        self.assertIsNone(scope.all_repo_ids)
        self.assertEqual(scope.expanded, {})

    def test_group_expansion(self):
        scope = self.service.resolve_scope(groups=["platform"], expand="none")
        self.assertFalse(scope.is_unscoped)
        self.assertEqual(sorted(scope.primary), ["service-a", "service-b"])
        self.assertEqual(scope.expanded, {})
        self.assertEqual(sorted(scope.all_repo_ids), ["service-a", "service-b"])

    def test_unknown_group_rejected(self):
        with self.assertRaises(UnknownGroupError):
            self.service.resolve_scope(groups=["non-existent-group"])

    def test_empty_group_resolves_to_empty(self):
        scope = self.service.resolve_scope(groups=["empty-group"])
        self.assertTrue(scope.is_empty)
        self.assertEqual(scope.primary, [])
        self.assertEqual(scope.all_repo_ids, [])

    def test_deduplication_of_explicit_repos_and_groups(self):
        scope = self.service.resolve_scope(
            repo_ids=["service-b", "isolated"], groups=["platform"], expand="none"
        )
        self.assertEqual(sorted(scope.primary), ["isolated", "service-a", "service-b"])
        self.assertEqual(scope.expanded, {})

    def test_upstream_expansion_depth_1(self):
        # service-a -> shared-lib -> core-utils
        scope = self.service.resolve_scope(
            repo_ids=["service-a"], expand="upstream", expand_depth=1
        )
        self.assertEqual(scope.primary, ["service-a"])
        self.assertIn("shared-lib", scope.expanded)
        self.assertNotIn("core-utils", scope.expanded)
        self.assertEqual(scope.expanded["shared-lib"].hops, 1)
        self.assertEqual(
            scope.expanded["shared-lib"].direction, RelationDirection.UPSTREAM
        )
        self.assertEqual(sorted(scope.all_repo_ids), ["service-a", "shared-lib"])

    def test_upstream_expansion_depth_2(self):
        scope = self.service.resolve_scope(
            repo_ids=["service-a"], expand="upstream", expand_depth=2
        )
        self.assertEqual(scope.primary, ["service-a"])
        self.assertIn("shared-lib", scope.expanded)
        self.assertIn("core-utils", scope.expanded)
        self.assertEqual(scope.expanded["shared-lib"].hops, 1)
        self.assertEqual(scope.expanded["core-utils"].hops, 2)
        self.assertEqual(
            sorted(scope.all_repo_ids), ["core-utils", "service-a", "shared-lib"]
        )

    def test_downstream_expansion(self):
        # shared-lib has dependents: service-a, service-b
        scope = self.service.resolve_scope(
            repo_ids=["shared-lib"], expand="downstream", expand_depth=1
        )
        self.assertEqual(scope.primary, ["shared-lib"])
        self.assertIn("service-a", scope.expanded)
        self.assertIn("service-b", scope.expanded)
        self.assertEqual(
            scope.expanded["service-a"].direction, RelationDirection.DOWNSTREAM
        )
        self.assertEqual(
            sorted(scope.all_repo_ids), ["service-a", "service-b", "shared-lib"]
        )

    def test_both_expansion(self):
        # shared-lib: upstream -> core-utils, downstream -> service-a, service-b
        scope = self.service.resolve_scope(
            repo_ids=["shared-lib"], expand="both", expand_depth=1
        )
        self.assertEqual(scope.primary, ["shared-lib"])
        self.assertIn("core-utils", scope.expanded)
        self.assertIn("service-a", scope.expanded)
        self.assertIn("service-b", scope.expanded)
        self.assertEqual(
            sorted(scope.all_repo_ids),
            ["core-utils", "service-a", "service-b", "shared-lib"],
        )

    def test_depth_0_disables_expansion(self):
        scope = self.service.resolve_scope(
            repo_ids=["service-a"], expand="upstream", expand_depth=0
        )
        self.assertEqual(scope.primary, ["service-a"])
        self.assertEqual(scope.expanded, {})

    def test_primary_repo_never_marked_as_expanded(self):
        # platform has service-a, service-b. service-a depends on shared-lib.
        # If we include shared-lib in explicit repos, shared-lib is primary, not expanded.
        scope = self.service.resolve_scope(
            repo_ids=["shared-lib"],
            groups=["platform"],
            expand="upstream",
            expand_depth=2,
        )
        self.assertIn("shared-lib", scope.primary)
        self.assertNotIn("shared-lib", scope.expanded)
        self.assertIn("core-utils", scope.expanded)


class TestRelationRetrievalRanking(unittest.TestCase):
    def test_hop_decay_penalty_and_provenance(self):
        mock_vec = MagicMock()
        mock_lex = MagicMock()
        mock_graph = MagicMock()
        mock_graph.get_symbol_centrality.return_value = 0.0
        mock_graph.get_callers.return_value = []
        mock_graph.get_callees.return_value = []

        chunk_primary = CodeChunk(
            chunk_id="chk-1",
            repo_id="service-a",
            file_path="main.py",
            language="python",
            start_line=1,
            end_line=10,
            raw_content="def hello(): pass",
            enriched_content="def hello(): pass",
            chunk_type=ChunkType.FUNCTION,
        )
        chunk_expanded = CodeChunk(
            chunk_id="chk-2",
            repo_id="shared-lib",
            file_path="util.py",
            language="python",
            start_line=1,
            end_line=10,
            raw_content="def hello(): pass",
            enriched_content="def hello(): pass",
            chunk_type=ChunkType.FUNCTION,
        )

        # Vector store returns both chunks with same rank
        mock_vec.search_vector.return_value = [
            (chunk_primary, 0.9),
            (chunk_expanded, 0.9),
        ]
        mock_lex.search_bm25.return_value = [
            ("chk-1", 5.0, ["hello"]),
            ("chk-2", 5.0, ["hello"]),
        ]

        from src.retriever.hybrid_retriever import HybridRetriever

        retriever = HybridRetriever(
            vector_store=mock_vec,
            lexical_store=mock_lex,
            graph_store=mock_graph,
            hop_decay=0.85,
        )

        expanded_repos = {
            "shared-lib": MagicMock(direction=RelationDirection.UPSTREAM, hops=1)
        }

        results = retriever.search(
            query="hello",
            repo_ids=["service-a", "shared-lib"],
            expanded_repos=expanded_repos,
            top_k=2,
        )

        self.assertEqual(len(results), 2)
        # Primary chunk should outrank expanded chunk due to hop decay penalty
        self.assertEqual(results[0].chunk.repo_id, "service-a")
        self.assertEqual(results[0].repo_relation, "primary")
        self.assertIsNone(results[0].relation_direction)
        self.assertIsNone(results[0].relation_hops)

        self.assertEqual(results[1].chunk.repo_id, "shared-lib")
        self.assertEqual(results[1].repo_relation, "expanded")
        self.assertEqual(results[1].relation_direction, "upstream")
        self.assertEqual(results[1].relation_hops, 1)
        self.assertGreater(results[0].score, results[1].score)


if __name__ == "__main__":
    unittest.main()
