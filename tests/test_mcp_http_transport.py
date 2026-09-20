"""
Tests for the MCP Streamable HTTP transport served by the `serve` process.

Covers request shaping on POST /mcp, 405s for GET/DELETE, HTTP/stdio parity,
Origin validation, CORS exclusion, optional bearer auth, concurrency with the
shared embedding-model lifecycle, and the reindex-busy error mapping.
"""

import unittest
import json
import io
import http.client
import shutil
import tempfile
import threading
from pathlib import Path

from src.indexer.lifecycle import EmbeddingLifecycle
from src.mcp.server import MCPServer
from src.server.api import ThreadedHTTPServer, make_request_handler
from src.service import MultiRepoRAGService


class MCPHandlerTestCase(unittest.TestCase):
    handler_kwargs = {}

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.service = MultiRepoRAGService(data_dir=self.temp_dir)
        self.service.repo_manager.register_repo(
            "repo-a", "Repo A", "local", "/tmp/repo-a"
        )
        self.service.repo_manager.register_repo(
            "repo-b", "Repo B", "local", "/tmp/repo-b"
        )
        self.mcp = MCPServer(service=self.service)
        self.handler_cls = make_request_handler(
            self.service, Path(self.temp_dir) / "web", **self.handler_kwargs
        )

    def tearDown(self):
        self.service.shutdown()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _request(self, method, path, data=None, raw_body=None, headers=None):
        handler = self.handler_cls.__new__(self.handler_cls)
        handler.path = path
        handler.command = method
        handler.headers = dict(headers or {})
        if raw_body is not None:
            data_bytes = raw_body.encode("utf-8")
        elif data is not None:
            data_bytes = json.dumps(data).encode("utf-8")
        else:
            data_bytes = b""
        handler.headers["Content-Length"] = str(len(data_bytes))
        handler.rfile = io.BytesIO(data_bytes)
        handler.wfile = io.BytesIO()
        handler.response_code = 200
        sent_headers = {}

        handler.send_response = lambda code, message=None: setattr(
            handler, "response_code", code
        )
        handler.send_header = lambda k, v: sent_headers.__setitem__(k, v)
        handler.end_headers = lambda: None

        getattr(handler, f"do_{method}")()

        res_bytes = handler.wfile.getvalue()
        body = json.loads(res_bytes.decode("utf-8")) if res_bytes else None
        return handler.response_code, body, sent_headers

    def _post_mcp(self, message, **kwargs):
        return self._request("POST", "/mcp", data=message, **kwargs)


class TestMCPHttpTransport(MCPHandlerTestCase):
    def test_initialize_tools_list_and_notification_shapes(self):
        # initialize -> 200 JSON-RPC result
        status, body, headers = self._post_mcp(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(body["result"]["serverInfo"]["name"], "multi-repo-code-rag")
        self.assertIn("tools", body["result"]["capabilities"])
        self.assertIn("application/json", headers["Content-Type"])

        # tools/list -> 200 with the tool spec
        status, body, _ = self._post_mcp(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        self.assertEqual(status, 200)
        tool_names = [t["name"] for t in body["result"]["tools"]]
        self.assertIn("search_codebases", tool_names)

        # notification -> 202 Accepted with empty body
        status, body, _ = self._post_mcp(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        self.assertEqual(status, 202)
        self.assertIsNone(body)

    def test_malformed_json_rejected_with_parse_error(self):
        status, body, _ = self._request("POST", "/mcp", raw_body="this is not json")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], -32700)

    def test_batch_array_rejected(self):
        status, body, _ = self._request(
            "POST",
            "/mcp",
            raw_body=json.dumps(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                ]
            ),
        )
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_get_and_delete_mcp_return_405(self):
        status, _, headers = self._request("GET", "/mcp")
        self.assertEqual(status, 405)
        self.assertEqual(headers.get("Allow"), "POST")

        status, _, _ = self._request("DELETE", "/mcp")
        self.assertEqual(status, 405)

    def test_stateless_tool_call_without_initialize(self):
        status, body, _ = self._post_mcp(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "list_repositories", "arguments": {}},
            }
        )
        self.assertEqual(status, 200)
        data = json.loads(body["result"]["content"][0]["text"])
        self.assertEqual(len(data["repositories"]), 2)

    def test_read_file_over_http(self):
        # On-disk fixture repo so reads can succeed.
        repo_root = Path(self.temp_dir) / "fixture-repo"
        (repo_root / "src").mkdir(parents=True)
        (repo_root / "src" / "hello.py").write_text("print('hello')\n")
        self.service.repo_manager.register_repo(
            "repo-f", "Repo F", "local", str(repo_root)
        )

        # Successful read over HTTP transport
        status, body, _ = self._post_mcp(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {
                    "name": "read_file",
                    "arguments": {"repo_id": "repo-f", "path": "src/hello.py"},
                },
            }
        )
        self.assertEqual(status, 200)
        self.assertNotIn("isError", body["result"])
        data = json.loads(body["result"]["content"][0]["text"])
        self.assertEqual(data["repo_id"], "repo-f")
        self.assertEqual(data["file_path"], "src/hello.py")
        self.assertEqual(data["content"], "print('hello')\n")

        # Error case: missing required argument surfaces as isError
        status, body, _ = self._post_mcp(
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {"name": "read_file", "arguments": {"repo_id": "repo-f"}},
            }
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["result"]["isError"])
        self.assertIn("path", body["result"]["content"][0]["text"])

    def test_http_stdio_parity(self):
        req = {
            "jsonrpc": "2.0",
            "id": 42,
            "method": "tools/call",
            "params": {"name": "list_repositories", "arguments": {}},
        }
        stdio_result = self.mcp.handle_request(req)["result"]

        status, body, _ = self._post_mcp(req)
        self.assertEqual(status, 200)
        self.assertEqual(body["result"], stdio_result)


class TestMCPOriginValidation(MCPHandlerTestCase):
    def test_disallowed_origin_rejected_before_processing(self):
        status, body, _ = self._post_mcp(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(status, 403)
        self.assertNotIn("result", body)

    def test_localhost_origin_accepted(self):
        status, body, _ = self._post_mcp(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Origin": "http://localhost:8000"},
        )
        self.assertEqual(status, 200)
        self.assertIn("result", body)

    def test_bind_host_origin_accepted_on_non_localhost_bind(self):
        handler_cls = make_request_handler(
            self.service,
            Path(self.temp_dir) / "web",
            bind_host="192.168.1.10",
        )
        self.handler_cls = handler_cls
        status, body, _ = self._post_mcp(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Origin": "http://192.168.1.10:8000"},
        )
        self.assertEqual(status, 200)
        self.assertIn("result", body)

    def test_missing_origin_accepted(self):
        status, body, _ = self._post_mcp(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertEqual(status, 200)
        self.assertIn("result", body)


class TestMCPCORSExclusion(MCPHandlerTestCase):
    def test_mcp_response_carries_no_cors_headers(self):
        status, _, headers = self._post_mcp(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertEqual(status, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_rest_routes_still_send_cors_headers(self):
        status, _, headers = self._request("GET", "/api/v1/repos")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")


class TestMCPBearerAuth(MCPHandlerTestCase):
    handler_kwargs = {"mcp_token": "secret-token"}

    def test_missing_token_rejected_with_401(self):
        status, _, headers = self._post_mcp(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertEqual(status, 401)
        self.assertEqual(headers.get("WWW-Authenticate"), "Bearer")

    def test_invalid_token_rejected_with_401(self):
        status, _, _ = self._post_mcp(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Authorization": "Bearer wrong-token"},
        )
        self.assertEqual(status, 401)

    def test_valid_token_accepted(self):
        status, body, _ = self._post_mcp(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Authorization": "Bearer secret-token"},
        )
        self.assertEqual(status, 200)
        self.assertIn("result", body)

    def test_unconfigured_token_requires_no_auth(self):
        self.handler_cls = make_request_handler(
            self.service, Path(self.temp_dir) / "web"
        )
        status, body, _ = self._post_mcp(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertEqual(status, 200)
        self.assertIn("result", body)


class CountingEngine:
    """Engine double counting actual model loads across overlapping sessions."""

    model = "counting-engine"

    def __init__(self):
        self.lifecycle = None
        self.loads = 0
        self.unloads = 0
        self._loaded = False

    def attach_lifecycle(self, lifecycle):
        self.lifecycle = lifecycle

    def embed(self, texts):
        if not self._loaded:
            self._loaded = True
            self.loads += 1
            if self.lifecycle:
                self.lifecycle.note_loaded(0.001)
        return [[0.1, 0.2] for _ in texts]

    def unload_model(self):
        self.unloads += 1
        self._loaded = False
        if self.lifecycle:
            self.lifecycle.note_unloaded()
        return True


class TestMCPHttpConcurrency(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.service = MultiRepoRAGService(data_dir=self.temp_dir)
        self.service.repo_manager.register_repo(
            "repo-a", "Repo A", "local", "/tmp/repo-a"
        )
        self.engine = CountingEngine()
        self.service.embedding_engine = self.engine
        self.service.embedding_lifecycle = EmbeddingLifecycle(
            engine=self.engine, policy="0"
        )

        # Overlap both searches on a barrier so their embedding sessions
        # are in flight at the same time.
        self.barrier = threading.Barrier(2)

        def stub_search(query, repo_ids=None, expanded_repos=None, top_k=8):
            self.engine.embed([query])
            self.barrier.wait(timeout=15)
            return []

        self.service.retriever.search = stub_search

        handler_cls = make_request_handler(
            self.service, Path(self.temp_dir) / "web"
        )
        self.server = ThreadedHTTPServer(("127.0.0.1", 0), handler_cls)
        self.port = self.server.server_address[1]
        self.server_thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)
        self.service.shutdown()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _post_mcp(self, message):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request(
                "POST",
                "/mcp",
                body=json.dumps(message),
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
        finally:
            conn.close()

    def test_concurrent_tool_calls_share_one_model_load(self):
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_codebases", "arguments": {"query": "auth"}},
        }
        outcomes = {}

        def call(key):
            outcomes[key] = self._post_mcp(req)

        threads = [
            threading.Thread(target=call, args=("a",)),
            threading.Thread(target=call, args=("b",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(set(outcomes.keys()), {"a", "b"})
        for status, body in outcomes.values():
            self.assertEqual(status, 200)
            self.assertNotIn("isError", body["result"])
            data = json.loads(body["result"]["content"][0]["text"])
            self.assertEqual(data["results"], [])

        # The overlapping window produced a single load and a single release.
        self.assertEqual(self.engine.loads, 1)
        self.assertEqual(self.engine.unloads, 1)


class TestMCPReindexBusy(MCPHandlerTestCase):
    def test_tool_call_during_reindex_returns_is_error(self):
        self.service.vector_store.list_indexed_repo_ids = lambda: ["repo-a"]
        self.service.provenance.record("repo-a", "ollama", "qwen3-embedding:4b", 2560)

        started = threading.Event()
        release = threading.Event()

        def slow_reembed(repo_id, progress_callback=None, batch_size=64):
            started.set()
            release.wait(timeout=15)
            return {"repo_id": repo_id, "chunks": 1, "dimension": 1024}

        self.service.vector_store.reembed_repo = slow_reembed

        worker = threading.Thread(target=self.service.reindex_stale_repos)
        worker.start()
        self.assertTrue(started.wait(timeout=15))
        try:
            status, body, _ = self._post_mcp(
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {
                        "name": "search_codebases",
                        "arguments": {"query": "auth"},
                    },
                }
            )
            self.assertEqual(status, 200)
            self.assertTrue(body["result"]["isError"])
            self.assertIn(
                "rebuilt", body["result"]["content"][0]["text"].lower()
            )
        finally:
            release.set()
            worker.join(timeout=15)


if __name__ == "__main__":
    unittest.main()
