"""
Unit and integration tests for dynamic repository updates (URL, branch, and name changes).
"""

import unittest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.service import MultiRepoRAGService
from src.mcp.server import MCPServer
from src.models.schema import RepoSourceType, RepoStatus


class TestRepositoryUpdate(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.data_dir = Path(self.test_dir) / "data"
        self.service = MultiRepoRAGService(
            data_dir=str(self.data_dir), embedding_provider="subword"
        )

        # Create a mock repository directory with dummy file
        self.mock_repo_dir = Path(self.test_dir) / "mock_repo"
        self.mock_repo_dir.mkdir(parents=True, exist_ok=True)
        with open(self.mock_repo_dir / "app.py", "w") as f:
            f.write("def hello(): return 'world'\n")

        self.service.add_repository(
            repo_id="test-repo",
            name="Original Repo Name",
            source_type="local",
            url_or_path=str(self.mock_repo_dir),
            branch="main",
            auto_sync=True,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_update_repository_metadata(self):
        result = self.service.update_repository(
            repo_id="test-repo",
            name="New Display Name",
            branch="feature/v2",
            auto_sync=False,
        )

        repo = self.service.repo_manager.get_repo("test-repo")
        self.assertIsNotNone(repo)
        self.assertEqual(repo.name, "New Display Name")
        self.assertEqual(repo.branch, "feature/v2")

    def test_update_repository_auto_sync(self):
        # Modify file in mock repo
        with open(self.mock_repo_dir / "app.py", "w") as f:
            f.write("def hello(): return 'world v2'\ndef new_func(): pass\n")

        result = self.service.update_repository(
            repo_id="test-repo", branch="release-1.0", auto_sync=True
        )

        repo = self.service.repo_manager.get_repo("test-repo")
        self.assertEqual(repo.branch, "release-1.0")
        self.assertIsNotNone(result.get("sync_result"))

    def test_mcp_update_repository_tool(self):
        mcp = MCPServer(service=self.service, data_dir=str(self.data_dir))

        resp = mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 101,
                "method": "tools/call",
                "params": {
                    "name": "update_repository",
                    "arguments": {
                        "repo_id": "test-repo",
                        "branch": "develop",
                        "name": "MCP Updated Name",
                        "auto_sync": False,
                    },
                },
            }
        )

        self.assertIsNotNone(resp)
        self.assertEqual(resp["id"], 101)
        self.assertNotIn("error", resp)

        repo = self.service.repo_manager.get_repo("test-repo")
        self.assertEqual(repo.branch, "develop")
        self.assertEqual(repo.name, "MCP Updated Name")

    @patch("subprocess.run")
    def test_switch_git_branch_mock(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(
            returncode=0, stdout="Switched to branch 'staging'", stderr=""
        )

        # Register git source repo
        git_repo = self.service.repo_manager.register_repo(
            repo_id="git-test",
            name="Git Test",
            source_type="git",
            url_or_path="https://github.com/example/repo.git",
            branch="main",
        )

        # Create mock git destination dir
        dest = self.service.repo_manager.repos_dir / "git-test"
        dest.mkdir(parents=True, exist_ok=True)

        ok, err = self.service.repo_manager.switch_git_branch_or_url(
            git_repo,
            new_url="https://github.com/example/repo-new.git",
            new_branch="staging",
        )

        self.assertTrue(ok)
        self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main()
