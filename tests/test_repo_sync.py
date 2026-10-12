import contextlib
import copy
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.cli.main import cmd_sync
from src.ingestion.repo_manager import (
    GitOperationError, RepoManager, RepositoryOperationError, _run_git,
)
from src.models.schema import RepoStatus
from src.mcp.server import MCPServer
from src.server.api import make_request_handler
from src.service import MultiRepoRAGService
from http_fixture import attach_http_context


SECRET = "SENTINEL_SECRET"
HEAD = "a" * 40


def completed(stdout=""):
    return subprocess.CompletedProcess(["git"], 0, stdout, "")


def git_failure(diagnostic="unknown " + SECRET):
    return subprocess.CalledProcessError(
        128, ["git", "https://user:" + SECRET + "@host/repo"],
        output=SECRET, stderr=diagnostic,
    )


class TestCheckedGit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = RepoManager(self.temp.name)
        self.repo = self.manager.register_repo(
            "git", "Git", "git", "https://user:" + SECRET + "@host/repo"
        )
        self.root = self.manager.get_repo_root_path(self.repo)

    def checkout(self):
        (self.root / ".git").mkdir(parents=True)

    def test_execution_contract_and_environment_isolation(self):
        inherited = {
            "SSH_AUTH_SOCK": "/agent", "HOME": self.temp.name,
            "GIT_CONFIG_GLOBAL": "/custom-config",
        }
        for launcher in ({}, {"GIT_SSH_COMMAND": "custom-ssh --flag"}, {"GIT_SSH": "/custom-ssh"}):
            with self.subTest(launcher=launcher), patch.dict(os.environ, {**inherited, **launcher}, clear=True):
                before = dict(os.environ)

                def execute(arguments, **options):
                    if not launcher:
                        fallback = Path(options["env"]["GIT_SSH"])
                        self.assertIn("BatchMode=yes", fallback.read_text())
                        self.assertIn("StrictHostKeyChecking=yes", fallback.read_text())
                        self.assertEqual(fallback.stat().st_mode & 0o777, 0o700)
                    return completed()

                with patch("src.ingestion.repo_manager.run_checked", side_effect=execute) as run:
                    for phase in ("clone", "fetch", "pull", "checkout", "set_remote", "inspect_checkout"):
                        _run_git(["operation", SECRET], phase, self.root, "git")
                    environments = []
                    for call, phase in zip(run.call_args_list, ("clone", "fetch", "pull", "checkout", "set_remote", "inspect_checkout")):
                        self.assertEqual(call.args, (["git", "operation", SECRET],))
                        options = call.kwargs
                        self.assertTrue(options["check"])
                        self.assertTrue(options["capture_output"])
                        self.assertTrue(options["text"])
                        self.assertNotIn("shell", options)
                        self.assertEqual(options["stdin"], subprocess.DEVNULL)
                        self.assertTrue(options["start_new_session"])
                        self.assertEqual(options["timeout"], 120 if phase in {"clone", "fetch", "pull"} else 15)
                        env = options["env"]
                        environments.append(env)
                        for key, value in inherited.items():
                            self.assertEqual(env[key], value)
                        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
                        self.assertEqual(env["SSH_ASKPASS_REQUIRE"], "never")
                        self.assertEqual(env["GIT_ASKPASS"], "/usr/bin/false")
                        for key, value in launcher.items():
                            self.assertEqual(env[key], value)
                        if not launcher:
                            self.assertNotIn("GIT_SSH_COMMAND", env)
                            self.assertFalse(Path(env["GIT_SSH"]).exists())
                    self.assertEqual(len({id(env) for env in environments}), 6)
                self.assertEqual(dict(os.environ), before)
        self.assertFalse((Path(self.temp.name) / ".gitconfig").exists())
        self.assertFalse((Path(self.temp.name) / ".ssh").exists())

    def test_whitelist_errors_are_bounded_and_secret_safe(self):
        cases = (
            (git_failure("Permission denied (publickey). " + SECRET), "git_authentication_failed"),
            (git_failure("Host key verification failed. " + SECRET), "git_host_key_failed"),
            (git_failure("Could not resolve hostname " + SECRET), "git_connection_failed"),
            (git_failure(), "git_command_failed"),
            (subprocess.TimeoutExpired(["git", SECRET], 120, output=SECRET, stderr=SECRET), "git_timeout"),
            (FileNotFoundError(2, SECRET, "git"), "git_unavailable"),
            (PermissionError(13, SECRET, "git"), "git_unavailable"),
            (OSError(5, SECRET), "git_command_failed"),
            (git_failure("X" * 8192 + "Authentication failed " + SECRET), "git_command_failed"),
        )
        for cause, code in cases:
            with self.subTest(code=code), patch("src.ingestion.repo_manager.run_checked", side_effect=cause):
                with self.assertLogs("rag.repository", level="ERROR") as logs:
                    with self.assertRaises(GitOperationError) as caught:
                        _run_git(["fetch", SECRET], "fetch", repo_id="git")
                error = caught.exception
                self.assertEqual(error.code, code)
                self.assertEqual(error.phase, "fetch")
                self.assertLessEqual(len(str(error)), 512)
                self.assertNotIn(SECRET, str(error.to_dict()) + "".join(logs.output))
                self.assertIsNone(error.__cause__)
                self.assertTrue(error.__suppress_context__)

    def test_successful_clone_and_existing_sequence(self):
        with patch("src.ingestion.repo_manager.run_checked", return_value=completed()) as run:
            self.assertEqual(self.manager.clone_or_pull_git_repo(self.repo), (True, None))
            self.assertEqual(run.call_args.args[0][1:3], ["clone", "--branch"])
        self.checkout()
        with patch("src.ingestion.repo_manager.run_checked", return_value=completed()) as run:
            self.assertEqual(self.manager.clone_or_pull_git_repo(self.repo), (True, None))
            self.assertEqual([call.args[0][1] for call in run.call_args_list], ["fetch", "checkout", "pull"])

    def test_each_failure_stops_dependent_commands(self):
        with patch("src.ingestion.repo_manager.run_checked", side_effect=git_failure()) as run:
            with self.assertRaises(GitOperationError) as caught:
                self.manager.clone_or_pull_git_repo(self.repo)
            self.assertEqual(caught.exception.phase, "clone")
            self.assertEqual(run.call_count, 1)
        self.checkout()
        for failing in range(3):
            with self.subTest(failing=failing), patch(
                "src.ingestion.repo_manager.run_checked",
                side_effect=[completed()] * failing + [git_failure()],
            ) as run:
                with self.assertRaises(GitOperationError) as caught:
                    self.manager.clone_or_pull_git_repo(self.repo)
                self.assertEqual(caught.exception.phase, ("fetch", "checkout", "pull")[failing])
                self.assertEqual(run.call_count, failing + 1)
        for failing in range(4):
            with self.subTest(update=failing), patch(
                "src.ingestion.repo_manager.run_checked",
                side_effect=[completed()] * failing + [git_failure()],
            ) as run:
                with self.assertRaises(GitOperationError) as caught:
                    self.manager.switch_git_branch_or_url(self.repo, "new", "feature")
                self.assertEqual(caught.exception.phase, ("set_remote", "fetch", "checkout", "pull")[failing])
                self.assertEqual(run.call_count, failing + 1)

    def test_only_expected_missing_branch_allows_tracking_fallback(self):
        self.checkout()
        missing = git_failure("error: pathspec 'feature' did not match any file(s) known to git")
        for tracking in (completed(), git_failure()):
            with self.subTest(tracking=tracking), patch(
                "src.ingestion.repo_manager.run_checked",
                side_effect=[completed(), missing, tracking, completed()],
            ) as run:
                if tracking.returncode:
                    with self.assertRaises(GitOperationError):
                        self.manager.switch_git_branch_or_url(self.repo, new_branch="feature")
                    self.assertEqual(run.call_count, 3)
                else:
                    self.assertEqual(self.manager.switch_git_branch_or_url(self.repo, new_branch="feature"), (True, None))
                    self.assertEqual(run.call_count, 4)
                self.assertEqual(run.call_args_list[2].args[0], ["git", "checkout", "-b", "feature", "origin/feature"])
        with patch("src.ingestion.repo_manager.run_checked", side_effect=[completed(), git_failure("local changes would be overwritten " + SECRET)]) as run:
            with self.assertRaises(GitOperationError):
                self.manager.switch_git_branch_or_url(self.repo, new_branch="feature")
            self.assertEqual(run.call_count, 2)


class TestServiceSync(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        self.service = MultiRepoRAGService(str(self.data), embedding_provider="subword")
        self.addCleanup(self.service.shutdown)
        self.manager = self.service.repo_manager
        self.repo = self.manager.register_repo("git", "Git", "git", "https://user:" + SECRET + "@host/repo")
        self.root = self.manager.get_repo_root_path(self.repo)
        (self.root / ".git").mkdir(parents=True)
        (self.root / "app.py").write_text("def authenticate_user(token):\n    return token\n")
        with patch("src.ingestion.repo_manager.run_checked", return_value=completed(HEAD)):
            self.initial = self.service.sync_repository("git")

    def snapshot(self, repo_id="git"):
        manager = RepoManager(str(self.data))
        repo = manager.get_repo(repo_id).to_dict()
        repo.pop("status")
        repo.pop("error_message")
        databases = {}
        for path in (manager.db_path, self.service.vector_store.db_path, self.service.lexical_store.db_path, self.service.graph_store.db_path):
            with contextlib.closing(sqlite3.connect(path)) as conn:
                tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('repositories', 'sqlite_sequence') ORDER BY name").fetchall()
                databases[path.name] = {table: sorted(conn.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr) for (table,) in tables}
        return repo, databases, copy.deepcopy(self.service.provenance.all())

    def test_pre_index_failures_preserve_all_persisted_indexes_and_search(self):
        before = self.snapshot()
        (self.root / "app.py").write_text("def replacement():\n    return 'new'\n")
        for phase, prefix in (("fetch", []), ("checkout", [completed()]), ("pull", [completed(), completed()]), ("inspect_checkout", [completed()] * 3)):
            with self.subTest(phase=phase), patch("src.ingestion.repo_manager.run_checked", side_effect=prefix + [git_failure()]), patch.object(self.manager, "scan_repository_files") as scan:
                with self.assertLogs("rag", level="ERROR") as logs:
                    with self.assertRaises(RepositoryOperationError) as caught:
                        self.service.sync_repository("git", force=True)
                self.assertEqual(caught.exception.phase, phase)
                self.assertIsNone(caught.exception.config_saved)
                self.assertNotIn(SECRET, str(caught.exception.to_dict()) + "".join(logs.output))
                scan.assert_not_called()
                self.assertEqual(self.snapshot(), before)
                persisted = RepoManager(str(self.data)).get_repo("git")
                self.assertEqual(persisted.status, RepoStatus.ERROR)
                self.assertNotIn(SECRET, persisted.error_message)
                self.assertTrue(self.service.search("authenticate_user", repo_ids=["git"]))
        # A clone failure also preserves a previously indexed catalog.
        with patch.object(self.manager, "clone_or_pull_git_repo", side_effect=GitOperationError("git_command_failed", "clone", "git")), patch.object(self.manager, "scan_repository_files") as scan:
            with self.assertRaises(RepositoryOperationError):
                self.service.sync_repository("git")
            scan.assert_not_called()
            self.assertEqual(self.snapshot(), before)

    def test_explicit_local_index_and_remote_retry(self):
        before = self.manager.get_repo("git")
        provenance = self.service.provenance.get("git")
        (self.root / "app.py").write_text("def offline_change():\n    return 'local'\n")
        with patch("src.ingestion.repo_manager.run_checked", return_value=completed("b" * 40)) as run:
            result = self.service.sync_repository("git", local_only=True, force=True)
        self.assertEqual(result["status"], "indexed_local")
        self.assertEqual([call.args[0][1] for call in run.call_args_list], ["rev-parse"])
        current = RepoManager(str(self.data)).get_repo("git")
        self.assertEqual(current.last_synced_at, before.last_synced_at)
        self.assertEqual(current.commit_hash, "b" * 40)
        self.assertEqual(current.total_chunks, result["total_chunks"])
        self.assertEqual(current.total_symbols, result["total_symbols"])
        self.assertGreater(self.service.provenance.get("git")["embedded_at"], provenance["embedded_at"])
        self.assertTrue(self.service.search("offline_change", repo_ids=["git"]))
        with patch("src.ingestion.repo_manager.run_checked", side_effect=git_failure()):
            with self.assertRaises(RepositoryOperationError):
                self.service.sync_repository("git")
        with patch("src.ingestion.repo_manager.run_checked", return_value=completed("c" * 40)) as run:
            retry = self.service.sync_repository("git", force=True)
        self.assertEqual(retry["status"], "synced")
        self.assertEqual([call.args[0][1] for call in run.call_args_list], ["fetch", "checkout", "pull", "rev-parse"])
        current = RepoManager(str(self.data)).get_repo("git")
        self.assertGreater(current.last_synced_at, before.last_synced_at)
        self.assertEqual(current.status, RepoStatus.READY)
        self.assertIsNone(current.error_message)
        self.assertEqual(set(retry), set(self.initial))

    def test_unusable_or_missing_local_checkout_never_mutates_index(self):
        before = self.snapshot()
        for output in (completed(""), git_failure()):
            with patch("src.ingestion.repo_manager.run_checked", side_effect=[output]), patch.object(self.manager, "scan_repository_files") as scan:
                with self.assertRaises(RepositoryOperationError) as caught:
                    self.service.sync_repository("git", local_only=True)
                self.assertEqual(caught.exception.code, "checkout_unavailable")
                scan.assert_not_called()
                self.assertEqual(self.snapshot(), before)
        with patch.object(self.manager, "get_repo_root_path", return_value=self.root / "missing"), patch("src.ingestion.repo_manager.run_checked") as run:
            with self.assertRaises(RepositoryOperationError) as caught:
                self.service.sync_repository("git", local_only=True)
            self.assertEqual(caught.exception.code, "checkout_unavailable")
            run.assert_not_called()
            self.assertEqual(self.snapshot(), before)

    def test_options_rejected_before_work_and_unknown_selection_safe(self):
        for entrypoint in (self.service.sync_repository, self.service._sync_repository_impl):
            for field in ("force", "local_only"):
                for value in ("true", 0, 1, None, [], {}):
                    with self.subTest(field=field, value=value), patch.object(self.manager, "get_repo") as get, patch.object(self.service, "embedding_session") as session:
                        with self.assertRaises(RepositoryOperationError) as caught:
                            entrypoint("git", **{field: value})
                        self.assertEqual(caught.exception.code, "invalid_sync_options")
                        get.assert_not_called()
                        session.assert_not_called()
        with self.assertRaises(RepositoryOperationError) as caught:
            self.service.sync_repository("https://user:" + SECRET + "@other/missing")
        self.assertEqual(caught.exception.code, "repository_not_found")
        self.assertNotIn(SECRET, str(caught.exception.to_dict()))

    def test_local_directory_shape_unchanged(self):
        local = Path(self.temp.name) / "local"
        local.mkdir()
        (local / "app.py").write_text("def local_function(): pass\n")
        self.manager.register_repo("local", "Local", "local", str(local))
        for mode in (False, True):
            with patch("src.ingestion.repo_manager.run_checked") as run:
                result = self.service.sync_repository("local", local_only=mode)
            run.assert_not_called()
            self.assertEqual(result["status"], "synced")
            self.assertEqual(set(result), set(self.initial))

    def test_raw_file_hashes_prevent_reindexing_unchanged_text(self):
        fixtures = {
            "lf.py": b"def lf_fixture():\n    return 1\n",
            "crlf.py": b"def crlf_fixture():\r\n    return 1\r\n",
            "cr.py": b"def cr_fixture():\r    return 1\r",
            "mixed.py": b"# mixed\r\ndef mixed_fixture():\r    return 1\n",
            "invalid_utf8.py": b"# ignored: \xff\ndef invalid_fixture():\n    return 1\n",
        }
        for name, raw in fixtures.items():
            (self.root / name).write_bytes(raw)

        with patch("src.ingestion.repo_manager.run_checked", return_value=completed(HEAD)), patch.object(
            self.service.chunker, "chunk_file", wraps=self.service.chunker.chunk_file,
        ) as chunk:
            first = self.service.sync_repository("git")
        self.assertEqual(first["added_files"], len(fixtures))
        self.assertEqual(first["modified_files"], 0)
        decoded = {call.args[1]: call.args[2] for call in chunk.call_args_list}
        for name in fixtures:
            self.assertEqual(decoded[name], (self.root / name).read_text(encoding="utf-8", errors="ignore"))
        with contextlib.closing(sqlite3.connect(self.manager.db_path)) as conn:
            stored = dict(conn.execute("SELECT file_path, sha256 FROM file_hashes WHERE repo_id = ?", ("git",)))
        for name, raw in fixtures.items():
            self.assertEqual(stored[name], hashlib.sha256(raw).hexdigest(), name)

        def assert_unchanged():
            with patch("src.ingestion.repo_manager.run_checked", return_value=completed(HEAD)), patch.object(
                self.service.embedding_engine, "encode_text", wraps=self.service.embedding_engine.encode_text,
            ) as embed, patch.object(self.service.chunker, "chunk_file") as chunk:
                result = self.service.sync_repository("git")
            self.assertEqual((result["added_files"], result["modified_files"], result["deleted_files"]), (0, 0, 0))
            self.assertEqual(result["total_chunks"], first["total_chunks"])
            embed.assert_not_called()
            chunk.assert_not_called()

        assert_unchanged()
        for name, raw in (
            ("crlf.py", fixtures["crlf.py"].replace(b"\r\n", b"\n")),
            ("invalid_utf8.py", fixtures["invalid_utf8.py"].replace(b"\xff", b"\xfe")),
        ):
            with self.subTest(name=name):
                (self.root / name).write_bytes(raw)
                with patch("src.ingestion.repo_manager.run_checked", return_value=completed(HEAD)), patch.object(
                    self.service.embedding_engine, "encode_text", wraps=self.service.embedding_engine.encode_text,
                ) as embed:
                    changed = self.service.sync_repository("git")
                self.assertEqual((changed["added_files"], changed["modified_files"]), (0, 1))
                self.assertGreater(embed.call_count, 0)
                assert_unchanged()

        with patch("src.ingestion.repo_manager.run_checked", return_value=completed(HEAD)), patch.object(
            self.service.embedding_engine, "encode_text", wraps=self.service.embedding_engine.encode_text,
        ) as embed:
            forced = self.service.sync_repository("git", force=True)
        self.assertEqual(forced["added_files"], len(fixtures) + 1)
        self.assertGreater(embed.call_count, 0)
        assert_unchanged()

    def test_add_and_update_persistence_context(self):
        before = self.snapshot()
        with patch("src.ingestion.repo_manager.run_checked", side_effect=git_failure()):
            with self.assertRaises(RepositoryOperationError) as caught:
                self.service.add_repository("new", "New", "git", "https://user:" + SECRET + "@host/new")
            self.assertTrue(caught.exception.config_saved)
            self.assertEqual(caught.exception.phase, "clone")
            current = RepoManager(str(self.data)).get_repo("new")
            self.assertEqual(current.status, RepoStatus.ERROR)
            self.assertEqual(current.total_chunks, 0)
        for phase, kwargs in (("set_remote", {"url_or_path": "changed"}), ("fetch", {"branch": "feature"})):
            with self.subTest(phase=phase), patch("src.ingestion.repo_manager.run_checked", side_effect=git_failure()), patch.object(self.service, "sync_repository") as sync:
                with self.assertRaises(RepositoryOperationError) as caught:
                    self.service.update_repository("git", auto_sync=False, **kwargs)
                self.assertTrue(caught.exception.config_saved)
                self.assertEqual(caught.exception.phase, phase)
                sync.assert_not_called()
            persisted = RepoManager(str(self.data)).get_repo("git")
            self.assertEqual(persisted.url_or_path if phase == "set_remote" else persisted.branch, next(iter(kwargs.values())))
            self.assertEqual(self.snapshot()[1:], before[1:])
            self.assertEqual(persisted.commit_hash, HEAD)
            self.assertEqual(persisted.last_synced_at, before[0]["last_synced_at"])
        with patch.object(self.manager, "save_configuration", side_effect=sqlite3.OperationalError(SECRET)):
            for call in (
                lambda: self.service.add_repository("failed", "Failed", "git", SECRET),
                lambda: self.service.update_repository("git", name="New name", auto_sync=False),
            ):
                with self.assertLogs("rag", level="ERROR") as logs, self.assertRaises(RepositoryOperationError) as caught:
                    call()
                self.assertFalse(caught.exception.config_saved)
                self.assertEqual(caught.exception.phase, "save_configuration")
                self.assertNotIn(SECRET, str(caught.exception.to_dict()) + "".join(logs.output))
        with patch("src.ingestion.repo_manager.run_checked", side_effect=git_failure()):
            with self.assertRaises(RepositoryOperationError) as caught:
                self.service.update_repository("git", name="Saved name")
            self.assertTrue(caught.exception.config_saved)
            self.assertEqual(RepoManager(str(self.data)).get_repo("git").name, "Saved name")

    def test_update_all_preparation_phases_preserve_index_and_desired_configuration(self):
        before = self.snapshot()
        for failing, phase in enumerate(("set_remote", "fetch", "checkout", "pull")):
            desired_url, desired_branch = f"changed-{failing}", f"branch-{failing}"
            with self.subTest(phase=phase), patch(
                "src.ingestion.repo_manager.run_checked",
                side_effect=[completed()] * failing + [git_failure()],
            ) as run, patch.object(self.manager, "scan_repository_files") as scan:
                with self.assertRaises(RepositoryOperationError) as caught:
                    self.service.update_repository("git", url_or_path=desired_url, branch=desired_branch)
                self.assertEqual(caught.exception.phase, phase)
                self.assertTrue(caught.exception.config_saved)
                self.assertEqual(run.call_count, failing + 1)
                scan.assert_not_called()
            current = RepoManager(str(self.data)).get_repo("git")
            self.assertEqual(current.url_or_path, desired_url)
            self.assertEqual(current.branch, desired_branch)
            actual = self.snapshot()
            self.assertEqual(actual[1:], before[1:])
            for field in ("commit_hash", "total_files", "total_chunks", "total_symbols", "last_synced_at"):
                self.assertEqual(actual[0][field], before[0][field])
            self.assertTrue(self.service.search("authenticate_user", repo_ids=["git"]))

    def test_unexpected_index_error_is_explicit_safe_and_saved(self):
        with patch.object(self.manager, "scan_repository_files", side_effect=RuntimeError(SECRET)), patch("src.ingestion.repo_manager.run_checked", return_value=completed(HEAD)):
            with self.assertLogs("rag", level="ERROR") as logs, self.assertRaises(RepositoryOperationError) as caught:
                self.service.sync_repository("git")
        self.assertEqual(caught.exception.code, "repository_operation_failed")
        self.assertEqual(caught.exception.phase, "index")
        self.assertNotIn(SECRET, str(caught.exception.to_dict()) + "".join(logs.output))
        self.assertNotIn(SECRET, self.manager.get_repo("git").error_message)

    def test_real_git_offline_index_retry_lifecycle_on_every_surface(self):
        upstream = Path(self.temp.name) / "upstream"
        remote = Path(self.temp.name) / "remote.git"
        offline_remote = Path(self.temp.name) / "offline.git"
        subprocess.run(["git", "init", "--initial-branch=main", str(upstream)], check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=15)
        (upstream / "auth.py").write_text("def authenticate_fixture(token):\n    return token\n")

        def commit(root, message):
            subprocess.run(["git", "add", "auth.py"], cwd=root, check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=15)
            subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "-m", message], cwd=root, check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=15)

        commit(upstream, "fixture")
        subprocess.run(["git", "clone", "--bare", str(upstream), str(remote)], check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=15)
        self.service.add_repository("real", "Real Git", "git", str(remote))
        checkout = self.manager.get_repo_root_path(self.manager.get_repo("real"))
        handler_cls = make_request_handler(self.service, Path(self.temp.name))
        mcp = MCPServer(service=self.service)

        def rest(options):
            handler = handler_cls.__new__(handler_cls)
            handler.path = "/api/v1/repos/real/sync"
            raw = json.dumps(options).encode()
            handler.headers = {"Content-Length": str(len(raw))}
            handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
            handler.send_response = lambda code: setattr(handler, "code", code)
            handler.send_header = lambda *args: None
            handler.end_headers = lambda: None
            attach_http_context(handler)
            handler.do_POST()
            return handler.code, json.loads(handler.wfile.getvalue())

        def invoke(surface, local_only=False):
            options = {"local_only": local_only}
            if surface == "service":
                try:
                    return True, self.service.sync_repository("real", **options)
                except RepositoryOperationError as error:
                    return False, error.to_dict()
            if surface == "rest":
                status, data = rest(options)
                return status == 200, data
            if surface == "mcp":
                response = mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "sync_repository", "arguments": {"repo_id": "real", **options}}})["result"]
                return not response.get("isError", False), json.loads(response["content"][0]["text"])
            args = SimpleNamespace(repo_id="real", force=False, local_only=local_only, all=False, interactive=False)
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                exit_code = cmd_sync(self.service, args)
            text = output.getvalue()
            if exit_code == 0:
                self.assertIn("Checkout Indexed (no remote update)" if local_only else "Sync Complete", text)
                if local_only:
                    self.assertNotIn("Sync Complete", text)
            return exit_code == 0, text

        for index, surface in enumerate(("service", "rest", "cli", "mcp"), 1):
            with self.subTest(surface=surface):
                before = self.snapshot("real")
                (checkout / "auth.py").write_text(f"def authenticate_fixture(token):\n    return token + {index}\n")
                commit(checkout, f"local {index}")
                remote.rename(offline_remote)
                success, error = invoke(surface)
                self.assertFalse(success)
                self.assertEqual(self.snapshot("real"), before)
                self.assertEqual(RepoManager(str(self.data)).get_repo("real").status, RepoStatus.ERROR)
                self.assertTrue(self.service.search("authenticate_fixture", repo_ids=["real"]))
                with patch.object(self.manager, "clone_or_pull_git_repo") as prepare:
                    success, result = invoke(surface, local_only=True)
                    self.assertTrue(success)
                    prepare.assert_not_called()
                if isinstance(result, dict):
                    self.assertEqual(result["status"], "indexed_local")
                current = RepoManager(str(self.data)).get_repo("real")
                self.assertEqual(current.last_synced_at, before[0]["last_synced_at"])
                self.assertNotEqual(current.commit_hash, before[0]["commit_hash"])
                self.assertEqual(current.status, RepoStatus.READY)
                self.assertIsNone(current.error_message)
                offline_remote.rename(remote)
                success, result = invoke(surface)
                self.assertTrue(success)
                if isinstance(result, dict):
                    self.assertEqual(result["status"], "synced")
                current = RepoManager(str(self.data)).get_repo("real")
                self.assertGreater(current.last_synced_at, before[0]["last_synced_at"])
                self.assertIsNone(current.error_message)


if __name__ == "__main__":
    unittest.main()
