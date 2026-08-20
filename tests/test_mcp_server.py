"""
Unit tests for Model Context Protocol (MCP) server JSON-RPC tools.
"""

import unittest
import shutil
import json
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


if __name__ == "__main__":
    unittest.main()
