import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.ingestion.repo_manager import RepositoryOperationError
from src.server.api import make_request_handler
from http_fixture import attach_http_context


class TestAPIRepoSync(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = Mock()
        self.success = {
            "repo_id": "repo", "status": "synced", "added_files": 1,
            "modified_files": 0, "deleted_files": 0, "total_chunks": 2,
            "total_symbols": 1, "embedding_dimension": 64, "elapsed_seconds": 0.2,
        }
        self.service.sync_repository.return_value = self.success
        self.handler_cls = make_request_handler(self.service, Path(self.temp.name))

    def request(self, value=None, *, raw=None, length=None, method="POST", path="/api/v1/repos/repo/sync"):
        handler = self.handler_cls.__new__(self.handler_cls)
        handler.path = path
        handler.headers = {}
        raw = json.dumps(value).encode() if raw is None else raw
        handler.headers["Content-Length"] = str(len(raw) if length is None else length)
        handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
        handler.response_headers = {}
        handler.send_response = lambda code: setattr(handler, "code", code)
        handler.send_header = lambda key, value: handler.response_headers.update({key: value})
        handler.end_headers = lambda: None
        attach_http_context(handler)
        getattr(handler, f"do_{method}")()
        self.last_handler = handler
        return handler.code, json.loads(handler.wfile.getvalue())

    def test_defaults_and_boolean_options_keep_success_shape(self):
        for raw in (b"", b"{}"):
            code, body = self.request(raw=raw)
            self.assertEqual((code, body), (200, self.success))
            self.service.sync_repository.assert_called_with("repo", force=False, local_only=False)
        for force in (False, True):
            for local_only in (False, True):
                self.service.sync_repository.return_value = {**self.success, "status": "indexed_local" if local_only else "synced"}
                code, body = self.request({"force": force, "local_only": local_only})
                self.assertEqual(code, 200)
                self.assertEqual(body, self.service.sync_repository.return_value)
                self.service.sync_repository.assert_called_with("repo", force=force, local_only=local_only)
        self.assertNotIn("Access-Control-Allow-Origin", self.last_handler.response_headers)

    def test_invalid_requests_do_not_invoke_sync(self):
        invalid = [
            b"{", b"\xff", b"[]", b"null", b"true", b"1",
            b'{"force":false,"force":true}', b'{"local_only":NaN}', b'{"other":false}',
            b"[" * 2000,
        ]
        for field in ("force", "local_only"):
            invalid.extend(json.dumps({field: value}).encode() for value in ("true", 0, 1, None, [], {}))
        for raw in invalid:
            with self.subTest(raw=raw):
                self.service.sync_repository.reset_mock()
                code, body = self.request(raw=raw)
                self.assertEqual(code, 400)
                self.assertEqual(body["status"], 400)
                self.assertEqual(body["code"], "invalid_sync_options")
                self.service.sync_repository.assert_not_called()
        for length in (-1, "bad", 3):
            self.service.sync_repository.reset_mock()
            self.assertEqual(self.request(raw=b"{}", length=length)[0], 400)
            self.service.sync_repository.assert_not_called()

    def test_exact_body_size_limit_and_no_oversized_read(self):
        raw = b"{}" + b" " * 4094
        self.assertEqual(self.request(raw=raw)[0], 200)
        self.assertEqual(self.last_handler.rfile.tell(), 4096)
        self.service.sync_repository.reset_mock()
        code, body = self.request(raw=b"{}" + b" " * 4095)
        self.assertEqual(code, 413)
        self.assertEqual(body["status"], 413)
        self.assertEqual(self.last_handler.rfile.tell(), 0)
        self.service.sync_repository.assert_not_called()

    def test_error_status_mapping_and_exact_safe_shapes(self):
        codes = {
            "invalid_sync_options": 400, "checkout_unavailable": 400,
            "repository_not_found": 404, "git_timeout": 504,
            "git_unavailable": 503, "git_authentication_failed": 500,
            "git_host_key_failed": 500, "git_connection_failed": 500,
            "git_command_failed": 500, "repository_operation_failed": 500,
        }
        for code, status in codes.items():
            error = RepositoryOperationError(code, "fetch", "repo")
            self.service.sync_repository.side_effect = error
            actual_status, body = self.request(raw=b"")
            self.assertEqual(actual_status, status)
            self.assertEqual(body, {**error.to_dict(), "status": status})
            self.assertNotIn("config_saved", body)
            self.assertLessEqual(len(body["error"]), 512)
        self.service.sync_repository.side_effect = RepositoryOperationError("git_timeout", "fetch", "repo", config_saved=True)
        self.assertNotIn("config_saved", self.request(raw=b"")[1])
        self.service.sync_repository.side_effect = RuntimeError("SENTINEL_SECRET")
        with self.assertLogs("rag", level="ERROR") as logs:
            status, body = self.request(raw=b"")
        self.assertEqual(status, 500)
        self.assertEqual(body, {
            **RepositoryOperationError(phase="index", repo_id="repo").to_dict(),
            "status": 500,
        })
        self.assertNotIn("SENTINEL_SECRET", str(body) + "".join(logs.output))

    def test_add_edit_success_and_failure_persistence_context(self):
        repository = Mock()
        repository.to_dict.return_value = {"repo_id": "repo", "status": "ready"}
        self.service.add_repository.return_value = repository
        self.service.update_repository.return_value = {"repo": repository.to_dict(), "sync_result": self.success}
        payload = {"repo_id": "repo", "url_or_path": "https://user:SENTINEL_SECRET@host/repo"}
        self.assertEqual(self.request(payload, path="/api/v1/repos"), (201, {"repository": repository.to_dict()}))
        self.assertEqual(self.request(payload, method="PUT", path="/api/v1/repos/repo"), (200, self.service.update_repository.return_value))
        for method, path, operation in (
            ("POST", "/api/v1/repos", self.service.add_repository),
            ("PUT", "/api/v1/repos/repo", self.service.update_repository),
        ):
            for config_saved in (False, True):
                error = RepositoryOperationError(
                    "git_authentication_failed" if config_saved else "repository_operation_failed",
                    "fetch" if config_saved else "save_configuration",
                    "repo", config_saved,
                )
                operation.side_effect = error
                status, body = self.request(payload, method=method, path=path)
                self.assertEqual((status, body), (500, {**error.to_dict(), "status": 500}))
                self.assertNotIn("SENTINEL_SECRET", str(body))
            operation.side_effect = RuntimeError("SENTINEL_SECRET")
            with self.assertLogs("rag", level="ERROR") as logs:
                status, body = self.request(payload, method=method, path=path)
            self.assertEqual(status, 500)
            self.assertNotIn("config_saved", body)
            self.assertNotIn("SENTINEL_SECRET", str(body) + "".join(logs.output))

    def test_encoded_repository_id_is_decoded_for_sync_edit_and_delete(self):
        self.request({}, path="/api/v1/repos/repo%20%27%3Cname%3E/sync")
        self.service.sync_repository.assert_called_with("repo '<name>", force=False, local_only=False)
        self.service.update_repository.return_value = {}
        self.request({}, method="PUT", path="/api/v1/repos/repo%20%27%3Cname%3E")
        self.assertEqual(self.service.update_repository.call_args.kwargs["repo_id"], "repo '<name>")
        self.service.delete_repository.return_value = True
        self.request({}, method="DELETE", path="/api/v1/repos/repo%20%27%3Cname%3E")
        self.service.delete_repository.assert_called_with("repo '<name>")


if __name__ == "__main__":
    unittest.main()
