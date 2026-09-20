"""
Unit tests for Model Context Protocol (MCP) server JSON-RPC tools.
"""

import unittest
import shutil
import json
import tempfile
from pathlib import Path
from src.service import MultiRepoRAGService
from src.mcp.server import MCPServer


class TestMCPServer(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("./test_data_mcp")
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

        self.service = MultiRepoRAGService(data_dir=str(self.test_dir))
        self.mcp = MCPServer(service=self.service, data_dir=str(self.test_dir))

        # Register sample repositories
        self.service.repo_manager.register_repo(
            "repo-a", "Repo A", "local", "/tmp/repo-a"
        )
        self.service.repo_manager.register_repo(
            "repo-b", "Repo B", "local", "/tmp/repo-b"
        )
        self.service.repo_manager.register_repo(
            "repo-c", "Repo C", "local", "/tmp/repo-c"
        )

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def _call_tool(self, name: str, arguments: dict = None):
        req = {
            "jsonrpc": "2.0",
            "id": 100,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        res = self.mcp.handle_request(req)
        return res["result"]

    def test_mcp_initialize_and_tools_list(self):
        init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        res = self.mcp.handle_request(init_req)
        self.assertEqual(res["result"]["serverInfo"]["name"], "multi-repo-code-rag")

        tools_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        res = self.mcp.handle_request(tools_req)
        tools = res["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        self.assertIn("search_codebases", tool_names)
        self.assertIn("manage_repository_relations", tool_names)
        self.assertIn("get_repository_relations", tool_names)
        self.assertIn("read_file", tool_names)

    def test_initialize_protocol_version_negotiation(self):
        # Supported 2025-03-26 is echoed back
        res = self.mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }
        )
        self.assertEqual(res["result"]["protocolVersion"], "2025-03-26")

        # Unknown versions fall back to the server's preferred version
        res = self.mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {"protocolVersion": "1999-01-01"},
            }
        )
        self.assertEqual(res["result"]["protocolVersion"], "2025-03-26")

        # 2024-11-05 clients get a response identical to pre-negotiation behavior
        res = self.mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        )
        self.assertEqual(
            res["result"],
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "multi-repo-code-rag", "version": "1.0.0"},
            },
        )

    def test_manage_repository_relations_groups(self):
        # 1. Create group
        res = self._call_tool(
            "manage_repository_relations",
            {"action": "create_group", "group": "platform", "repos": ["repo-a"]},
        )
        self.assertNotIn("isError", res)
        data = json.loads(res["content"][0]["text"])
        self.assertEqual(data["group"]["name"], "platform")
        self.assertEqual(data["group"]["members"], ["repo-a"])

        # 2. Add to group
        res = self._call_tool(
            "manage_repository_relations",
            {"action": "add_to_group", "group": "platform", "repos": ["repo-b"]},
        )
        self.assertNotIn("isError", res)
        data = json.loads(res["content"][0]["text"])
        self.assertEqual(sorted(data["group"]["members"]), ["repo-a", "repo-b"])

        # 3. Unknown group error
        res = self._call_tool(
            "manage_repository_relations",
            {"action": "add_to_group", "group": "non-existent", "repos": ["repo-a"]},
        )
        self.assertTrue(res.get("isError"))

        # 4. Remove from group
        res = self._call_tool(
            "manage_repository_relations",
            {"action": "remove_from_group", "group": "platform", "repos": ["repo-a"]},
        )
        self.assertNotIn("isError", res)
        data = json.loads(res["content"][0]["text"])
        self.assertTrue(data["success"])

        # 5. Delete group
        res = self._call_tool(
            "manage_repository_relations",
            {"action": "delete_group", "group": "platform"},
        )
        self.assertNotIn("isError", res)
        data = json.loads(res["content"][0]["text"])
        self.assertTrue(data["success"])

    def test_manage_repository_relations_dependencies_and_cycles(self):
        # 1. Add dependency repo-a -> repo-b
        res = self._call_tool(
            "manage_repository_relations",
            {"action": "add_dependency", "repo": "repo-a", "depends_on": "repo-b"},
        )
        self.assertNotIn("isError", res)
        data = json.loads(res["content"][0]["text"])
        self.assertEqual(data["dependency"]["repo_id"], "repo-a")
        self.assertEqual(data["dependency"]["depends_on_repo_id"], "repo-b")

        # 2. Add dependency repo-b -> repo-c
        res = self._call_tool(
            "manage_repository_relations",
            {"action": "add_dependency", "repo": "repo-b", "depends_on": "repo-c"},
        )
        self.assertNotIn("isError", res)

        # 3. Cycle rejection: repo-c -> repo-a
        res = self._call_tool(
            "manage_repository_relations",
            {"action": "add_dependency", "repo": "repo-c", "depends_on": "repo-a"},
        )
        self.assertTrue(res.get("isError"))
        self.assertIn("cycle", res["content"][0]["text"])

        # 4. Get relations for single repo
        res = self._call_tool("get_repository_relations", {"repo": "repo-a"})
        self.assertNotIn("isError", res)
        data = json.loads(res["content"][0]["text"])
        self.assertEqual(data["dependencies"], ["repo-b"])

        # 5. Get relations for whole graph
        res = self._call_tool("get_repository_relations", {})
        self.assertNotIn("isError", res)
        data = json.loads(res["content"][0]["text"])
        self.assertIn("groups", data)
        self.assertIn("dependencies", data)

        # 6. Remove dependency
        res = self._call_tool(
            "manage_repository_relations",
            {"action": "remove_dependency", "repo": "repo-a", "depends_on": "repo-b"},
        )
        self.assertNotIn("isError", res)

    def test_read_file_tool(self):
        # On-disk fixture repo so reads can succeed.
        repo_root = self.test_dir / "fixture-repo"
        (repo_root / "src").mkdir(parents=True)
        (repo_root / "src" / "hello.py").write_text("print('hello')\n")
        self.service.repo_manager.register_repo(
            "repo-f", "Repo F", "local", str(repo_root)
        )

        # 1. Successful read returns {repo_id, file_path, content}
        res = self._call_tool(
            "read_file", {"repo_id": "repo-f", "path": "src/hello.py"}
        )
        self.assertNotIn("isError", res)
        data = json.loads(res["content"][0]["text"])
        self.assertEqual(data["repo_id"], "repo-f")
        self.assertEqual(data["file_path"], "src/hello.py")
        self.assertEqual(data["content"], "print('hello')\n")

        # 2. Missing arguments are rejected naming the argument
        res = self._call_tool("read_file", {"path": "src/hello.py"})
        self.assertTrue(res.get("isError"))
        self.assertIn("repo_id", res["content"][0]["text"])

        res = self._call_tool("read_file", {"repo_id": "repo-f"})
        self.assertTrue(res.get("isError"))
        self.assertIn("path", res["content"][0]["text"])

        # 3. Unknown repository returns a structured not-found result
        res = self._call_tool(
            "read_file", {"repo_id": "nope", "path": "src/hello.py"}
        )
        self.assertNotIn("isError", res)
        data = json.loads(res["content"][0]["text"])
        self.assertIn("error", data)
        self.assertNotIn("content", data)

        # 4. Nonexistent file returns a structured not-found result
        res = self._call_tool(
            "read_file", {"repo_id": "repo-f", "path": "src/missing.py"}
        )
        self.assertNotIn("isError", res)
        data = json.loads(res["content"][0]["text"])
        self.assertIn("error", data)
        self.assertNotIn("content", data)

        # 5. Path escape is rejected as not-found without leaking content
        (self.test_dir / "secret.txt").write_text("top secret")
        res = self._call_tool(
            "read_file", {"repo_id": "repo-f", "path": "../secret.txt"}
        )
        self.assertNotIn("isError", res)
        data = json.loads(res["content"][0]["text"])
        self.assertIn("error", data)
        self.assertNotIn("content", data)

    def test_search_relation_arguments(self):
        self.service.repo_manager.create_group("core", ["repo-a", "repo-b"])
        self.service.repo_manager.add_dependency("repo-a", "repo-c")

        # 1. Unchanged pre-existing call
        res = self._call_tool(
            "search_codebases", {"query": "auth", "repos": ["repo-a"]}
        )
        self.assertNotIn("isError", res)
        data = json.loads(res["content"][0]["text"])
        self.assertIn("results", data)
        self.assertNotIn("scope", data)

        # 2. Search scoped to group with expansion
        res = self._call_tool(
            "search_codebases",
            {
                "query": "auth",
                "groups": ["core"],
                "expand": "upstream",
                "expand_depth": 1,
            },
        )
        self.assertNotIn("isError", res)
        data = json.loads(res["content"][0]["text"])
        self.assertIn("scope", data)
        self.assertEqual(sorted(data["scope"]["primary"]), ["repo-a", "repo-b"])
        self.assertIn("repo-c", [e["repo_id"] for e in data["scope"]["expanded"]])


class TestGetFileContentContainment(unittest.TestCase):
    """Unit tests for repository-root containment in service.get_file_content()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.service = MultiRepoRAGService(data_dir=self.temp_dir)

        # Secret file outside the repo root, one level up from it.
        self.outside_dir = Path(self.temp_dir) / "outside"
        self.outside_dir.mkdir()
        (self.outside_dir / "secret.txt").write_text("top secret")

        self.repo_root = self.outside_dir / "repo-f"
        (self.repo_root / "src").mkdir(parents=True)
        (self.repo_root / "src" / "hello.py").write_text("print('hello')\n")

        self.service.repo_manager.register_repo(
            "repo-f", "Repo F", "local", str(self.repo_root)
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_nested_path_inside_root_still_reads(self):
        content = self.service.get_file_content("repo-f", "src/hello.py")
        self.assertEqual(content, "print('hello')\n")

    def test_parent_escape_returns_none(self):
        self.assertIsNone(self.service.get_file_content("repo-f", "../secret.txt"))
        self.assertIsNone(self.service.get_file_content("repo-f", "../../etc/passwd"))

    def test_absolute_path_returns_none(self):
        self.assertIsNone(self.service.get_file_content("repo-f", "/etc/passwd"))
        self.assertIsNone(
            self.service.get_file_content(
                "repo-f", str(self.outside_dir / "secret.txt")
            )
        )

    def test_unknown_repo_returns_none(self):
        self.assertIsNone(self.service.get_file_content("nope", "src/hello.py"))


if __name__ == "__main__":
    unittest.main()
