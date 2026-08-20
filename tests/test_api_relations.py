import unittest
import json
import io
import tempfile
import shutil
from pathlib import Path

from src.service import MultiRepoRAGService
from src.server.api import make_request_handler


class TestAPIRelations(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.service = MultiRepoRAGService(data_dir=self.temp_dir)

        # Register repositories
        self.service.repo_manager.register_repo(
            "repo-a", "Repo A", "local", "/tmp/repo-a"
        )
        self.service.repo_manager.register_repo(
            "repo-b", "Repo B", "local", "/tmp/repo-b"
        )
        self.service.repo_manager.register_repo(
            "repo-c", "Repo C", "local", "/tmp/repo-c"
        )

        self.handler_cls = make_request_handler(
            self.service, Path(self.temp_dir) / "web"
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _request(self, method: str, path: str, data: dict = None):
        handler = self.handler_cls.__new__(self.handler_cls)
        handler.path = path
        handler.command = method
        handler.headers = {}
        if data is not None:
            data_bytes = json.dumps(data).encode("utf-8")
            handler.headers["Content-Length"] = str(len(data_bytes))
            handler.rfile = io.BytesIO(data_bytes)
        else:
            handler.rfile = io.BytesIO(b"")

        handler.wfile = io.BytesIO()
        handler.response_code = 200

        def fake_send_response(code, message=None):
            handler.response_code = code

        handler.send_response = fake_send_response
        handler.send_header = lambda k, v: None
        handler.end_headers = lambda: None

        if method == "GET":
            handler.do_GET()
        elif method == "POST":
            handler.do_POST()
        elif method == "DELETE":
            handler.do_DELETE()
        elif method == "PUT":
            handler.do_PUT()

        res_bytes = handler.wfile.getvalue()
        body = json.loads(res_bytes.decode("utf-8")) if res_bytes else {}
        return handler.response_code, body

    def test_group_crud_and_errors(self):
        # 1. Create group
        status, body = self._request(
            "POST", "/api/v1/groups", {"name": "platform", "repo_ids": ["repo-a"]}
        )
        self.assertEqual(status, 201)
        self.assertEqual(body["group"]["name"], "platform")
        self.assertEqual(body["group"]["members"], ["repo-a"])

        # 2. Duplicate group name returns 4xx
        status, body = self._request("POST", "/api/v1/groups", {"name": "platform"})
        self.assertEqual(status, 400)
        self.assertIn("already exists", body["error"])

        # 3. Add unknown repo to group returns 4xx
        status, body = self._request(
            "POST", "/api/v1/groups/platform/members", {"repo_ids": ["unknown-repo"]}
        )
        self.assertEqual(status, 400)
        self.assertIn("not registered", body["error"])

        # 4. Add valid member to group
        status, body = self._request(
            "POST", "/api/v1/groups/platform/members", {"repo_ids": ["repo-b"]}
        )
        self.assertEqual(status, 200)
        self.assertEqual(sorted(body["group"]["members"]), ["repo-a", "repo-b"])

        # 5. List groups
        status, body = self._request("GET", "/api/v1/groups")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["groups"]), 1)
        self.assertEqual(body["groups"][0]["name"], "platform")

        # 6. Delete member from group
        status, body = self._request("DELETE", "/api/v1/groups/platform/members/repo-a")
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])

        # 7. Delete non-existent member returns 404
        status, body = self._request("DELETE", "/api/v1/groups/platform/members/repo-a")
        self.assertEqual(status, 404)

        # 8. Delete group
        status, body = self._request("DELETE", "/api/v1/groups/platform")
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])

        # 9. Delete non-existent group returns 404
        status, body = self._request("DELETE", "/api/v1/groups/platform")
        self.assertEqual(status, 404)

    def test_dependencies_and_cycle_error(self):
        # 1. Add dependency repo-a -> repo-b
        status, body = self._request(
            "POST", "/api/v1/repos/repo-a/dependencies", {"depends_on": "repo-b"}
        )
        self.assertEqual(status, 201)
        self.assertEqual(body["dependency"]["repo_id"], "repo-a")
        self.assertEqual(body["dependency"]["depends_on_repo_id"], "repo-b")

        # 2. Add dependency repo-b -> repo-c
        status, body = self._request(
            "POST", "/api/v1/repos/repo-b/dependencies", {"depends_on": "repo-c"}
        )
        self.assertEqual(status, 201)

        # 3. Adding cycle repo-c -> repo-a returns 4xx
        status, body = self._request(
            "POST", "/api/v1/repos/repo-c/dependencies", {"depends_on": "repo-a"}
        )
        self.assertEqual(status, 400)
        self.assertIn("cycle", body["error"])

        # 4. Get repo relations
        status, body = self._request("GET", "/api/v1/repos/repo-a/relations")
        self.assertEqual(status, 200)
        self.assertEqual(body["dependencies"], ["repo-b"])
        self.assertEqual(body["dependents"], [])

        # 5. Remove dependency
        status, body = self._request(
            "DELETE", "/api/v1/repos/repo-a/dependencies/repo-b"
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])

        # 6. Remove non-existent dependency returns 404
        status, body = self._request(
            "DELETE", "/api/v1/repos/repo-a/dependencies/repo-b"
        )
        self.assertEqual(status, 404)

    def test_backwards_compatible_search_response(self):
        # Requests omitting relation parameters return only {"query": ..., "results": [...]}
        status, body = self._request(
            "POST",
            "/api/v1/search",
            {"query": "test query", "repo_ids": ["repo-a"], "top_k": 5},
        )
        self.assertEqual(status, 200)
        self.assertEqual(set(body.keys()), {"query", "results"})

    def test_search_with_groups_reports_scope(self):
        self._request(
            "POST",
            "/api/v1/groups",
            {"name": "test-group", "repo_ids": ["repo-a", "repo-b"]},
        )
        status, body = self._request(
            "POST",
            "/api/v1/search",
            {
                "query": "test query",
                "groups": ["test-group"],
                "expand": "upstream",
                "expand_depth": 1,
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("scope", body)
        self.assertEqual(sorted(body["scope"]["primary"]), ["repo-a", "repo-b"])


if __name__ == "__main__":
    unittest.main()
