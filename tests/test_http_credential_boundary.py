import contextlib
import io
import json
import os
import sqlite3
import socket
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from test_api_ssh_keys import SSHAPIHandlerCase
from src.ingestion.repo_manager import RepoManager, RepositoryOperationError
from src.server.api import listener_is_loopback, make_request_handler, start_server
from src.mcp.server import MCPServer


class TestHTTPCredentialBoundary(SSHAPIHandlerCase):
    def test_listener_classification_and_pre_activation_rejection(self):
        for address in ("127.0.0.1", "127.2.3.4", "::1", "localhost"):
            self.assertTrue(listener_is_loopback(address))
        for address in ("0.0.0.0", "::", "192.0.2.1"):
            self.assertFalse(listener_is_loopback(address))
        resolved = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 0))]
        with patch("src.server.api.socket.getaddrinfo", return_value=resolved):
            self.assertFalse(listener_is_loopback("mixed.invalid"))
        self.register()
        for address in ("0.0.0.0", "::", "192.0.2.1"):
            with patch("src.server.api.ThreadedHTTPServer") as server:
                with self.assertRaises(RepositoryOperationError) as caught:
                    start_server(self.service, address, 0)
                self.assertEqual(caught.exception.code, "ssh_http_forbidden")
                server.assert_not_called()

    def test_every_route_and_options_enforce_same_origin_and_host_before_work(self):
        hostile = [
            {"Host": "attacker.invalid:8000"}, {"Host": "localhost:9000"},
            {"Host": "localhost:"}, {"Host": "localhost:0"}, {"Host": "user@localhost:8000"},
            {"Host": "localhost:8000/path"}, {"Host": "localhost:8000,localhost:8000"},
            {"Host": "[::1]junk:8000"},
            {"Host": ""}, {"Origin": "null"}, {"Origin": "http://localhost:9000"},
            {"Origin": "http://localhost:8000/"}, {"Origin": "http://user@localhost:8000"},
            {"Origin": "http://localhost:8000 http://localhost:8000"},
            {"Origin": "http://localhost:8000?"}, {"Origin": "https://localhost:8000"},
            {"Origin": "http://@localhost:8000"}, {"Origin": "http://localhost:8000:"},
            {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
        ]
        for method, path in (("GET", "/"), ("GET", "/api/v1/repos"), ("POST", "/mcp"),
                             ("POST", "/api/v1/repos"), ("PUT", "/api/v1/repos/repo"),
                             ("DELETE", "/api/v1/ssh-keys/key"), ("OPTIONS", "/api/v1/ssh-keys")):
            for headers in hostile:
                with self.subTest(method=method, path=path, headers=headers):
                    status, _ = self.request(method, path, {}, headers=headers)
                    self.assertEqual(status, 403)
                    self.assertEqual(self.last_handler.rfile.tell(), 0)
                    self.assertNotIn("Access-Control-Allow-Origin", self.response_headers)
        self.assertEqual(self.request(headers={"Origin": "http://localhost:8000", "Sec-Fetch-Site": "same-origin"})[0], 200)
        self.assertEqual(self.request(headers={"Host": "[::1]:8123"}, address=("::1", 8123))[0], 200)
        self.assertEqual(self.request(headers={"Host": "[::1]:8123", "Origin": "http://[::1]junk:8123"},
                                      address=("::1", 8123))[0], 403)
        self.assertEqual(self.request("POST", "/mcp",
                                     {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                     headers={"Host": "127.2.3.4:8000", "Origin": "http://127.2.3.4:8000"})[0], 200)
        for header_name in ("Host", "Origin"):
            handler = self.handler_cls.__new__(self.handler_cls)
            handler.server = type("Server", (), {"server_address": ("127.0.0.1", 8000)})()
            handler.headers = Message()
            if header_name == "Origin":
                handler.headers["Host"] = "localhost:8000"
            for _ in range(2):
                handler.headers[header_name] = "localhost:8000" if header_name == "Host" else "http://localhost:8000"
            handler.wfile = io.BytesIO()
            handler.send_response = lambda code: setattr(handler, "code", code)
            handler.send_header = lambda *args: None
            handler.end_headers = lambda: None
            self.assertFalse(handler._request_allowed())
            self.assertEqual(handler.code, 403)
        del handler.headers["Host"]
        self.assertFalse(handler._request_allowed())

    def test_post_bind_check_prevents_activation_and_closes_socket(self):
        self.register()
        with patch("src.server.api.ThreadedHTTPServer") as server:
            server.return_value.server_address = ("0.0.0.0", 41234)
            with self.assertRaises(RepositoryOperationError) as caught:
                start_server(self.service, "127.0.0.1", 0)
            self.assertEqual(caught.exception.code, "ssh_http_forbidden")
            server.return_value.server_bind.assert_called_once()
            server.return_value.server_activate.assert_not_called()
            server.return_value.server_close.assert_called_once()

    def test_real_ephemeral_listener_uses_bound_port_for_requests_and_discovery(self):
        from src.server.api import ThreadedHTTPServer

        captured = {}
        ready = threading.Event()

        def create(*args, **kwargs):
            server = ThreadedHTTPServer(*args, **kwargs)
            captured["server"] = server
            activate = server.server_activate

            def activating():
                activate()
                ready.set()

            server.server_activate = activating
            return server

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with patch("src.server.api.ThreadedHTTPServer", side_effect=create), contextlib.redirect_stdout(io.StringIO()) as output:
            thread = threading.Thread(target=start_server, args=(self.service, "127.0.0.1", 0))
            thread.start()
            try:
                self.assertTrue(ready.wait(timeout=5))
                server = captured["server"]
                port = server.server_address[1]
                self.assertGreater(port, 0)
                url = f"http://127.0.0.1:{port}/api/v1/ssh-keys"
                with opener.open(url, timeout=5) as response:
                    self.assertEqual(json.load(response), {"ssh_keys": []})
                request = urllib.request.Request(url, headers={"Origin": f"http://127.0.0.1:{port}"})
                with opener.open(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                request = urllib.request.Request(url, headers={"Host": "127.0.0.1:0"})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    opener.open(request, timeout=5)
                self.assertEqual(caught.exception.code, 403)
                caught.exception.close()
                self.assertIn(f"http://127.0.0.1:{port}", output.getvalue())
            finally:
                if "server" in captured:
                    captured["server"].shutdown()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())

    def test_remote_profile_and_network_policy_cannot_be_bypassed_by_mcp_or_git_rewrite(self):
        self.handler_cls = make_request_handler(self.service, self.root / "web", bind_host="0.0.0.0")
        remote = ("0.0.0.0", 8000)
        for method in ("GET", "POST", "PUT", "DELETE"):
            path = "/api/v1/ssh-keys" + ("/key" if method in {"PUT", "DELETE"} else "")
            self.assertEqual(self.request(method, path, {}, address=remote)[0], 403)
        self.manager.register_repo("remote", "Remote", "git", "git@fixture.invalid:repo")
        status, body = self.request("POST", "/api/v1/repos/remote/sync", {}, address=remote)
        self.assertEqual((status, body["code"]), (403, "ssh_http_forbidden"))
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "sync_repository", "arguments": {"repo_id": "remote"}}}
        status, body = self.request("POST", "/mcp", message, address=remote)
        self.assertEqual(status, 200)
        self.assertTrue(body["result"]["isError"])
        self.assertEqual(json.loads(body["result"]["content"][0]["text"])["code"], "ssh_http_forbidden")
        self.handler_cls = make_request_handler(self.service, self.root / "web", bind_host="0.0.0.0",
                                                mcp_token="fixture-token")
        message["params"] = {"name": "update_repository",
                             "arguments": {"repo_id": "remote", "ssh_key_id": None}}
        status, body = self.request("POST", "/mcp", message, address=remote,
                                    headers={"Authorization": "Bearer fixture-token"})
        self.assertEqual(status, 200)
        error = json.loads(body["result"]["content"][0]["text"])
        self.assertEqual(error["code"], "ssh_http_forbidden")
        self.assertFalse(error["config_saved"])
        self.handler_cls = make_request_handler(self.service, self.root / "web", bind_host="0.0.0.0")
        config = self.root / "git-config"
        config.write_text('[url "ssh://git@fixture.invalid/"]\n insteadOf = https://fixture.invalid/\n')
        self.manager.register_repo("rewrite", "Rewrite", "git", "https://fixture.invalid/repo")
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1"}):
            status, body = self.request("POST", "/api/v1/repos/rewrite/sync", {}, address=remote)
        self.assertEqual((status, body["code"]), (403, "ssh_http_forbidden"))
        trusted = RepoManager(str(self.manager.data_dir))
        self.assertTrue(trusted.ssh_network_allowed)
        local = self.root / "source"
        local.mkdir()
        (local / "ordinary.py").write_text("def ordinary(): pass\n")
        self.manager.register_repo("local", "Local", "local", str(local))
        self.assertEqual(self.request("POST", "/api/v1/repos/local/sync", {}, address=remote)[0], 200)
        self.manager.register_repo("https", "HTTPS", "git", "https://fixture.invalid/repo")

        def git(arguments, **options):
            if arguments[1] == "clone":
                root = Path(arguments[-1])
                (root / ".git").mkdir(parents=True)
                (root / "ordinary.py").write_text("def ordinary(): pass\n")
                self.assertEqual(options["env"]["GIT_ALLOW_PROTOCOL"], "https:http:file:git")
            return subprocess.CompletedProcess(arguments, 0, "a" * 40, "")

        with patch("src.ingestion.repo_manager.run_checked", side_effect=git):
            self.assertEqual(self.request("POST", "/api/v1/repos/https/sync", {}, address=remote)[0], 200)

    def test_static_and_source_containment_private_markers_and_aliases_before_indexing(self):
        web = self.root / "web"
        web.mkdir()
        (web / "ordinary.js").write_text("const ordinary = 1;")
        outside = self.root / "outside.js"
        outside.write_text("outside")
        (web / "escape.js").symlink_to(outside)
        (web / "loop.js").symlink_to(web / "loop.js")
        profile = self.register()
        (web / "alias.js").symlink_to(self.key)
        os.link(self.key, web / "hardlink.js")
        (web / "private.js").write_text("-----BEGIN RSA PRIVATE KEY-----\nSENTINEL_SECRET\n")
        for path in ("/../outside.js", "/%2e%2e/outside.js", "/escape.js", "/loop.js", "/alias.js", "/hardlink.js", "/private.js"):
            status, body = self.request("GET", path)
            self.assertEqual(status, 404)
            self.assertNotIn("SENTINEL_SECRET", repr(body))
        repo = self.manager.register_repo("source", "Source", "local", str(web))
        added, _, _ = self.manager.scan_repository_files(repo)
        self.assertNotIn("alias.js", added)
        self.assertNotIn("hardlink.js", added)
        self.assertNotIn("private.js", added)
        for name in ("alias.js", "hardlink.js", "private.js"):
            self.assertIsNone(self.service.get_file_content("source", name))
            self.assertEqual(self.request("GET", f"/api/v1/file?repo_id=source&path={name}")[0], 404)
            result = MCPServer(service=self.service).execute_tool("read_file", {"repo_id": "source", "path": name})
            self.assertNotIn("SENTINEL_SECRET", repr(result))
        self.assertEqual(self.service.get_file_content("source", "ordinary.js"), "const ordinary = 1;")
        self.manager.delete_ssh_key(profile["key_id"])
        self.assertIsNone(self.service.get_file_content("source", "alias.js"))

    def test_registration_rejects_previously_indexed_file_identity_without_claiming_cleanup(self):
        source = self.root / "source"
        source.mkdir()
        alias = source / "alias.py"
        os.link(self.key, alias)
        self.manager.register_repo("source", "Source", "local", str(source))
        self.manager.commit_file_hashes("source", {"alias.py": ("prior-index", 1, 2)}, [])
        status, body = self.request("POST", body={"name": "Key", "private_key_path": str(self.key)})
        self.assertEqual((status, body["code"]), (409, "ssh_key_indexed"))
        self.assertEqual(self.manager.get_all_indexed_files("source"), ["alias.py"])
        self.assertIn("does not clean up", body["error"])
        self.manager.commit_file_hashes("source", {}, ["alias.py"])
        self.assertEqual(self.request("POST", body={"name": "Key", "private_key_path": str(self.key)})[0], 201)

    def test_registration_checks_indexed_chunks_without_catalog_hashes(self):
        source = self.root / "source"
        source.mkdir()
        alias = source / "alias.py"
        alias.write_text("def old_source(): return 1\n")
        self.manager.register_repo("source", "Source", "local", str(source))
        self.service.sync_repository("source")
        self.manager.commit_file_hashes("source", {}, ["alias.py"])
        self.assertEqual(self.manager.get_all_indexed_files("source"), [])
        self.assertEqual(self.service.vector_store.list_indexed_files(), [("source", "alias.py")])
        alias.unlink()
        os.link(self.key, alias)
        status, body = self.request("POST", body={"name": "Key", "private_key_path": str(self.key)})
        self.assertEqual((status, body["code"]), (409, "ssh_key_indexed"))
        self.assertGreater(self.service.vector_store.count_chunks("source"), 0)

    def test_force_index_removes_newly_protected_entries_before_hashing_or_chunking(self):
        source = self.root / "source"
        source.mkdir()
        sensitive = source / "previous.py"
        sensitive.write_text("def old_source(): return 1\n")
        ordinary = source / "ordinary.py"
        ordinary.write_text("def ordinary(): return 2\n")
        repo = self.manager.register_repo("source", "Source", "local", str(source))
        self.service.sync_repository("source")
        sensitive.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nSENTINEL_SECRET\n")
        added, _, deleted = self.manager.scan_repository_files(repo, force=True)
        self.assertEqual(added, ["ordinary.py"])
        self.assertEqual(deleted, ["previous.py"])
        with patch.object(self.service.chunker, "chunk_file", wraps=self.service.chunker.chunk_file) as chunk:
            self.service.sync_repository("source", force=True)
        self.assertEqual([call.args[1] for call in chunk.call_args_list], ["ordinary.py"])
        self.assertEqual(self.manager.get_all_indexed_files("source"), ["ordinary.py"])
        self.assertEqual(self.service.vector_store.list_indexed_files(), [("source", "ordinary.py")])
        status, body = self.request("GET", "/api/v1/file?repo_id=source&path=ordinary.py")
        self.assertEqual((status, body["content"]), (200, ordinary.read_text()))
        self.assertEqual(self.service.get_file_content("source", "ordinary.py"), ordinary.read_text())
