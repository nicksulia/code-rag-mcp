import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.ingestion.repo_manager import PROJECT_REQUEST_LIMIT, RepoManager
from src.server.api import make_request_handler
from src.service import MultiRepoRAGService
from test_project_transfer import document, repository
from http_fixture import attach_http_context


class TestAPIProjectTransfer(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Transfer must not need any model/index initialization.
        self.service = MultiRepoRAGService.__new__(MultiRepoRAGService)
        self.service.repo_manager = RepoManager(self.temp.name)
        self.service.embedding_engine = Mock()
        self.service.embedding_lifecycle = Mock()
        self.handler_cls = make_request_handler(self.service, Path(self.temp.name))

    def request(self, value=None, *, raw=None, length=None, method="POST", path=None):
        handler = self.handler_cls.__new__(self.handler_cls)
        handler.path = path or (
            "/api/v1/projects/export" if method == "GET" else "/api/v1/projects/import"
        )
        handler.headers = {}
        if raw is None:
            raw = json.dumps(value).encode()
        handler.headers["Content-Length"] = (
            str(len(raw)) if length is None else str(length)
        )
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.response_headers = {}
        handler.send_response = lambda code: setattr(handler, "code", code)
        handler.send_header = lambda key, val: handler.response_headers.update(
            {key: val}
        )
        handler.end_headers = lambda: None
        attach_http_context(handler)
        getattr(handler, f"do_{method}")()
        self.last_handler = handler
        return handler.code, json.loads(handler.wfile.getvalue())

    def assert_error(self, status, value=None, **kwargs):
        code, body = self.request(value, **kwargs)
        self.assertEqual(code, status, body)
        self.assertEqual(set(body), {"error", "status"})
        self.assertEqual(body["status"], status)
        self.assertNotIn("SECRET", body["error"])
        return body

    def test_export_canonical_bytes_headers_and_complete_relations(self):
        incoming = document(
            repository(),
            repository("web", "local", "/missing", None),
            groups=[{"name": "项目", "members": ["api", "web"]}],
            dependencies=[{"repo_id": "web", "depends_on_repo_id": "api"}],
        )
        self.service.import_projects(incoming)
        code, body = self.request(method="GET")
        self.assertEqual(code, 200)
        self.assertEqual(body, incoming)
        self.assertEqual(
            self.last_handler.wfile.getvalue(), self.service.export_projects()
        )
        self.assertEqual(
            self.last_handler.response_headers["Content-Disposition"],
            'attachment; filename="projects.json"',
        )
        self.assertEqual(
            self.last_handler.response_headers["Content-Type"],
            "application/json; charset=utf-8",
        )
        self.assertEqual(
            int(self.last_handler.response_headers["Content-Length"]),
            len(self.service.export_projects()),
        )

    def test_credential_safe_export_errors(self):
        self.service.repo_manager.register_repo(
            "api", "API", "git", "https://user:SECRET@host/a"
        )
        self.assertIn("url_or_path", self.assert_error(400, method="GET")["error"])
        self.assertNotIn("Content-Disposition", self.last_handler.response_headers)
        with patch.object(
            self.service,
            "export_projects",
            side_effect=sqlite3.OperationalError("SECRET"),
        ):
            with self.assertLogs("rag.api", level="ERROR") as logs:
                self.assert_error(500, method="GET")
            self.assertNotIn("SECRET", "".join(logs.output))

    def test_strict_json_and_envelope_parsing(self):
        for raw in (
            b"",
            b"{",
            b"\xff",
            b"[]",
            b"null",
            b"true",
            b'{"document": {}, "document": {}}',
            b'{"document":{"version":1,"version":1,"repositories":[],"groups":[],"dependencies":[]}}',
            b'{"document": NaN}',
            b"[" * 2000,
        ):
            with self.subTest(raw=raw):
                self.assert_error(400, raw=raw)
        for value in (
            {},
            {"dry_run": True},
            {"document": None},
            {"document": document(), "extra": True},
        ):
            with self.subTest(value=value):
                self.assert_error(400, value)
        for dry_run in ("false", 0, 1, None, [], {}):
            self.assert_error(400, {"document": document(), "dry_run": dry_run})
        self.assert_error(400, raw=b"{}", length=-1)
        self.assert_error(400, raw=b"{}", length="bad")
        self.assert_error(400, raw=b"{}", length=3)
        self.assertEqual(self.service.repo_manager.list_repos(), [])

    def test_request_limit_before_read_and_exact_boundary(self):
        self.assert_error(413, raw=b"", length=PROJECT_REQUEST_LIMIT + 1)
        self.assertEqual(self.last_handler.rfile.tell(), 0)
        raw = json.dumps({"document": document()}).encode()
        raw += b" " * (PROJECT_REQUEST_LIMIT - len(raw))
        code, body = self.request(raw=raw)
        self.assertEqual(code, 200)
        self.assertTrue(body["dry_run"])
        self.assertEqual(self.last_handler.rfile.tell(), PROJECT_REQUEST_LIMIT)

    def test_preview_default_confirmation_counts_and_no_models_or_sync(self):
        incoming = document(
            repository(),
            repository("web", "local", "/missing", None),
            groups=[{"name": "platform", "members": ["web"]}],
            dependencies=[{"repo_id": "web", "depends_on_repo_id": "api"}],
        )
        with (
            patch.object(self.service, "sync_repository") as sync,
            patch.object(self.service, "add_repository") as add,
        ):
            code, preview = self.request({"document": incoming})
            self.assertEqual(code, 200)
            self.assertEqual(
                preview,
                {"dry_run": True, **self.service.preview_project_import(incoming)},
            )
            self.assertEqual(self.service.repo_manager.list_repos(), [])
            code, actual = self.request({"document": incoming, "dry_run": False})
            self.assertEqual(code, 200)
            self.assertEqual(actual, {**preview, "dry_run": False, "success": True})
            self.assertEqual(
                actual["additions"]["repositories"], {"count": 2, "ids": ["api", "web"]}
            )
            self.assertEqual(
                actual["additions"]["groups"], {"count": 1, "names": ["platform"]}
            )
            self.assertEqual(
                actual["additions"]["memberships"],
                {"count": 1, "entries": [{"group": "platform", "repo_id": "web"}]},
            )
            self.assertEqual(
                actual["additions"]["dependencies"],
                {
                    "count": 1,
                    "entries": [{"repo_id": "web", "depends_on_repo_id": "api"}],
                },
            )
            self.assertEqual(
                actual["warnings"],
                [{"repo_id": "web", "code": "local_path_machine_specific"}],
            )
            sync.assert_not_called()
            add.assert_not_called()
        self.assertEqual(self.service.embedding_engine.mock_calls, [])
        self.assertEqual(self.service.embedding_lifecycle.mock_calls, [])
        self.assertEqual(len(RepoManager(self.temp.name).list_repos()), 2)

    def test_conflicting_preview_and_confirmation(self):
        self.service.import_projects(document(repository()))
        incoming = document(repository(branch="other"), repository("web"))
        before = self.service.export_projects()
        code, preview = self.request({"document": incoming})
        self.assertEqual(code, 200)
        self.assertFalse(preview["can_import"])
        self.assertEqual(
            preview["conflicts"], [{"repo_id": "api", "fields": ["branch"]}]
        )
        self.assertEqual(self.service.export_projects(), before)
        self.assert_error(409, {"document": incoming, "dry_run": False})
        self.assertEqual(self.service.export_projects(), before)

    def test_invalid_document_relations_and_persistence_failure(self):
        for incoming in (
            document(repository(address="https://user:SECRET@host/a")),
            document(repository(), groups=[{"name": "x", "members": ["unknown"]}]),
            {**document(), "version": True},
        ):
            self.assert_error(400, {"document": incoming, "dry_run": False})
        manager = self.service.repo_manager
        with contextlib.closing(sqlite3.connect(manager.db_path)) as conn, conn:
            conn.execute("""CREATE TRIGGER fail_group BEFORE INSERT ON repo_groups
                            BEGIN SELECT RAISE(ABORT, 'SECRET'); END""")
        with self.assertLogs("rag.api", level="ERROR") as logs:
            self.assert_error(
                500,
                {
                    "document": document(
                        repository(), groups=[{"name": "x", "members": []}]
                    ),
                    "dry_run": False,
                },
            )
        self.assertNotIn("SECRET", "".join(logs.output))
        self.assertEqual(RepoManager(self.temp.name).list_repos(), [])

    def test_imported_group_names_operate_through_encoded_routes(self):
        from urllib.parse import quote

        name = '/平台/members <tag> "quoted"'
        self.service.import_projects(
            document(repository("constructor"), groups=[{"name": name, "members": []}])
        )
        path = "/api/v1/groups/" + quote(name, safe="")
        status, body = self.request(
            {"repo_ids": ["constructor"]}, path=path + "/members"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["group"]["name"], name)
        self.assertEqual(body["group"]["members"], ["constructor"])
        status, body = self.request(method="DELETE", path=path + "/members/constructor")
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        status, body = self.request(method="DELETE", path=path)
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])


if __name__ == "__main__":
    unittest.main()
