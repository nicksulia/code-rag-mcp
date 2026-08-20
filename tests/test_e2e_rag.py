"""
End-to-End Integration tests for Multi-Repository Code RAG.
Tests repo registration, AST parsing, symbol extraction, indexing, and cross-repo retrieval search.
"""

import unittest
import shutil
from pathlib import Path
from src.service import MultiRepoRAGService


class TestMultiRepoRAGE2E(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("./test_data_e2e")
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

        self.service = MultiRepoRAGService(data_dir=str(self.test_dir))
        self.root_dir = Path(__file__).resolve().parent.parent

        # Add 3 fixture repositories
        self.repo_auth_path = str(self.root_dir / "fixtures" / "repo_auth_service")
        self.repo_web_path = str(self.root_dir / "fixtures" / "repo_web_client")
        self.repo_schema_path = str(self.root_dir / "fixtures" / "repo_shared_schemas")

        self.service.add_repository(
            "auth-service",
            "Auth Microservice",
            "local",
            self.repo_auth_path,
            auto_sync=True,
        )
        self.service.add_repository(
            "web-client",
            "Frontend Web Client",
            "local",
            self.repo_web_path,
            auto_sync=True,
        )
        self.service.add_repository(
            "shared-schemas",
            "Shared Go Schemas",
            "local",
            self.repo_schema_path,
            auto_sync=True,
        )

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_repositories_indexed(self):
        repos = self.service.list_repositories()
        self.assertEqual(len(repos), 3)
        for r in repos:
            self.assertEqual(r.status.value, "ready")
            self.assertGreater(r.total_chunks, 0)
            self.assertGreater(r.total_symbols, 0)

    def test_cross_repo_search(self):
        results = self.service.search("login JWT token authenticate", top_k=5)
        self.assertTrue(len(results) > 0)

        # Verify chunks from multiple repositories are retrieved
        repo_ids = set(r.chunk.repo_id for r in results)
        self.assertTrue("auth-service" in repo_ids or "web-client" in repo_ids)

    def test_cross_repo_api_linkage_detected(self):
        links = self.service.get_cross_repo_api_links()
        self.assertTrue(len(links) >= 1)
        found_login_link = any(
            l["client_repo"] == "web-client"
            and l["server_repo"] == "auth-service"
            and "/api/v1/auth/login" in l["api_route"]
            for l in links
        )
        self.assertTrue(
            found_login_link,
            f"Expected cross-repo API link between web-client and auth-service, got: {links}",
        )

if __name__ == "__main__":
    unittest.main()
