import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from http_fixture import attach_http_context
from src.ingestion.repo_manager import RepositoryOperationError
from src.server.api import make_request_handler
from src.service import MultiRepoRAGService
from test_ssh_keys import generate_key


class SSHAPIHandlerCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.service = MultiRepoRAGService(str(self.root / "data"), embedding_provider="subword")
        self.addCleanup(self.service.shutdown)
        self.manager = self.service.repo_manager
        self.handler_cls = make_request_handler(self.service, self.root / "web")
        self.key = generate_key(self.root / "key")

    def request(self, method="GET", path="/api/v1/ssh-keys", body=None, raw=None, headers=None,
                address=("127.0.0.1", 8000)):
        handler = self.handler_cls.__new__(self.handler_cls)
        handler.path = path
        raw = json.dumps(body).encode() if raw is None and body is not None else raw or b""
        handler.headers = {"Content-Length": str(len(raw)), **(headers or {})}
        attach_http_context(handler, address)
        handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
        response_headers = {}
        handler.send_response = lambda code: setattr(handler, "code", code)
        handler.send_header = lambda key, value: response_headers.update({key: value})
        handler.end_headers = lambda: None
        getattr(handler, "do_" + method)()
        self.last_handler, self.response_headers = handler, response_headers
        data = handler.wfile.getvalue()
        return handler.code, json.loads(data) if data else None

    def register(self):
        status, body = self.request("POST", body={"name": "Key", "private_key_path": str(self.key)})
        self.assertEqual(status, 201, body)
        return body["ssh_key"]

class TestAPISSHKeys(SSHAPIHandlerCase):
    def test_exact_crud_projections_sorted_associations_conflicts_and_file_preservation(self):
        self.assertEqual(self.request(), (200, {"ssh_keys": []}))
        profile = self.register()
        self.assertEqual(set(profile), {"key_id", "name", "private_key_path", "fingerprint", "algorithm", "repo_ids"})
        self.assertEqual(self.request()[1], {"ssh_keys": [profile]})
        path = "/api/v1/ssh-keys/" + profile["key_id"]
        self.assertEqual(self.request("PUT", path, {"name": "Renamed"})[1]["ssh_key"]["name"], "Renamed")
        self.assertEqual(self.request("POST", body={"name": "Duplicate", "private_key_path": str(self.key)})[0], 409)
        for rid in ("z", "a"):
            self.service.add_repository(rid, rid, "git", "git@fixture.invalid:repo", auto_sync=False,
                                        assignment={"ssh_key_id": profile["key_id"]})
        self.assertEqual(self.request()[1]["ssh_keys"][0]["repo_ids"], ["a", "z"])
        status, body = self.request("DELETE", path)
        self.assertEqual(status, 409)
        self.assertEqual(body["repo_ids"], ["a", "z"])
        for rid in ("a", "z"):
            self.service.update_repository(rid, auto_sync=False, assignment={"ssh_key_id": None})
        self.assertEqual(self.request("DELETE", path), (200, {"success": True, "deleted_key_id": profile["key_id"]}))
        self.assertTrue(self.key.exists())
        self.assertTrue(Path(str(self.key) + ".pub").exists())
        self.assertEqual(self.request("DELETE", path)[0], 404)
        self.assertNotIn("Access-Control-Allow-Origin", self.response_headers)

    def test_strict_json_fields_utf8_duplicate_keys_types_and_exact_body_limit(self):
        invalid = [
            b"{", b"\xff", b"null", b"[]", b'{"name":"a","name":"b","private_key_path":"/key"}',
            b'{"name":NaN,"private_key_path":"/key"}',
        ]
        invalid += [json.dumps(body).encode() for body in (
            {"name": "Key", "private_key_path": str(self.key), "passphrase": "SENTINEL_SECRET"},
            {"name": "Key", "private_key_path": str(self.key), "private_key": "SENTINEL_SECRET"},
            {"name": False, "private_key_path": str(self.key)},
            {"name": "Key", "private_key_path": []}, {"name": "Key"},
        )]
        for raw in invalid:
            with self.subTest(raw=raw):
                status, body = self.request("POST", raw=raw)
                self.assertEqual(status, 400)
                self.assertEqual(body["code"], "ssh_key_invalid")
                self.assertNotIn("SENTINEL_SECRET", json.dumps(body))
                self.assertEqual(self.manager.list_ssh_keys(), [])
        valid = json.dumps({"name": "Key", "private_key_path": str(self.key)}).encode()
        self.assertEqual(self.request("POST", raw=valid + b" " * (8192 - len(valid)))[0], 201)
        self.assertEqual(self.last_handler.rfile.tell(), 8192)
        self.assertEqual(self.request("POST", raw=valid + b" " * (8193 - len(valid)))[0], 413)
        self.assertEqual(self.last_handler.rfile.tell(), 0)

    def test_error_status_code_phase_and_secret_safety(self):
        expected = {
            "ssh_key_invalid": 400, "ssh_key_not_found": 404, "ssh_key_conflict": 409,
            "ssh_key_in_use": 409, "ssh_tool_unavailable": 503, "ssh_key_timeout": 504,
            "ssh_http_forbidden": 403, "ssh_key_body_too_large": 413,
            "ssh_key_changed": 409, "ssh_override_conflict": 400, "ssh_config_unsupported": 400,
        }
        for code, status in expected.items():
            error = RepositoryOperationError(code, "ssh_validation")
            with patch.object(self.manager, "register_ssh_key", side_effect=error):
                actual, body = self.request("POST", body={"name": "Key", "private_key_path": str(self.key)})
            self.assertEqual(actual, status)
            self.assertEqual(body["code"], code)
            self.assertEqual(body["phase"], "ssh_validation")
            self.assertNotIn("config_saved", body)
        with patch.object(self.manager, "register_ssh_key", side_effect=RuntimeError("SENTINEL_SECRET")):
            with self.assertLogs("rag", level="ERROR") as logs:
                status, body = self.request("POST", body={"name": "Key", "private_key_path": str(self.key)})
        self.assertEqual(status, 500)
        self.assertNotIn("SENTINEL_SECRET", repr(body) + repr(logs.output))

    def test_add_edit_assignment_semantics_and_invalid_input_before_mutation(self):
        profile = self.register()
        payload = {"repo_id": "repo", "source_type": "git", "url_or_path": "git@fixture.invalid:repo",
                   "auto_sync": False, "ssh_key_id": profile["key_id"]}
        status, body = self.request("POST", "/api/v1/repos", payload)
        self.assertEqual(status, 201, body)
        self.assertEqual(body["repository"]["ssh_key_id"], profile["key_id"])
        path = "/api/v1/repos/repo"
        status, body = self.request("PUT", path, {"name": "Edited", "auto_sync": False})
        self.assertEqual(body["repo"]["ssh_key_id"], profile["key_id"])
        before = self.manager.get_repo("repo").to_dict()
        for raw in (b"{", b'{"ssh_key_id":null,"ssh_key_id":"other"}', b'{"ssh_key_id":3}'):
            self.assertEqual(self.request("PUT", path, raw=raw)[0], 400)
            self.assertEqual(self.manager.get_repo("repo").to_dict(), before)
        status, body = self.request("PUT", path, {"ssh_key_id": "missing", "auto_sync": False})
        self.assertEqual(status, 404)
        self.assertFalse(body["config_saved"])
        self.assertEqual(self.manager.get_repo("repo").to_dict(), before)
        status, body = self.request("PUT", path, {"ssh_key_id": None, "auto_sync": False})
        self.assertEqual(status, 200)
        self.assertIsNone(body["repo"]["ssh_key_id"])
