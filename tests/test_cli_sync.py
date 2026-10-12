import contextlib
import io
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.cli.main import cmd_add, cmd_sync, cmd_update, main
from src.ingestion.repo_manager import GitOperationError, RepositoryOperationError
from src.models.schema import Repository, RepoSourceType


SECRET = "SENTINEL_SECRET"


class TestCLISync(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.repos = [
            Repository(repo_id=name, name=name, source_type=kind, url_or_path="https://user:" + SECRET + "@host/repo")
            for name, kind in (("first", RepoSourceType.GIT), ("middle", RepoSourceType.LOCAL), ("last", RepoSourceType.GIT))
        ]
        self.service.list_repositories.return_value = self.repos
        self.service.embedding_engine.dimension = 64
        self.service.embedding_engine.model = "subword"
        self.service.resolve_repo_ids.side_effect = lambda ids: ids
        self.service.repo_manager.get_repo.side_effect = lambda name: next((repo for repo in self.repos if repo.repo_id == name), None)
        self.args = SimpleNamespace(repo_id="all", all=False, interactive=False, force=False, local_only=False)
        self.success = {
            "status": "synced", "added_files": 1, "modified_files": 0,
            "total_chunks": 1, "total_symbols": 1, "embedding_dimension": 64,
            "elapsed_seconds": 0.1,
        }
        self.service.sync_repository.return_value = self.success

    def capture(self, callback):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = callback()
        text = out.getvalue() + err.getvalue()
        self.assertNotIn(SECRET, text)
        return code, text

    def test_bulk_success_mixed_and_total_failures_continue(self):
        for outcomes, counts, expected in (
            ([self.success] * 3, "3 succeeded, 0 failed", 0),
            ([self.success, GitOperationError("git_timeout", "fetch", "middle"), self.success], "2 succeeded, 1 failed", 1),
            ([RuntimeError(SECRET)] * 3, "0 succeeded, 3 failed", 1),
        ):
            self.service.sync_repository.reset_mock()
            self.service.sync_repository.side_effect = outcomes
            code, text = self.capture(lambda: cmd_sync(self.service, self.args))
            self.assertEqual(code, expected)
            self.assertIn(counts, text)
            self.assertEqual([call.kwargs["repo_id"] for call in self.service.sync_repository.call_args_list], ["first", "middle", "last"])

    def test_local_only_and_force_forward_independently(self):
        self.args.repo_id = "first"
        for force, mode in ((False, True), (True, True), (True, False)):
            self.args.force, self.args.local_only = force, mode
            self.service.sync_repository.return_value = {**self.success, "status": "indexed_local" if mode else "synced"}
            code, text = self.capture(lambda: cmd_sync(self.service, self.args))
            self.assertEqual(code, 0)
            options = self.service.sync_repository.call_args.kwargs
            self.assertEqual(options["force"], force)
            self.assertEqual(options["local_only"], mode)
            if mode:
                self.assertIn("Checkout Indexed (no remote update)", text)
                self.assertNotIn("Sync Complete", text)
            else:
                self.assertIn("Sync Complete", text)

    def test_local_only_mixed_selection_and_unknown_repo(self):
        self.args.local_only = True
        self.service.sync_repository.side_effect = [
            {**self.success, "status": "indexed_local"}, self.success,
            {**self.success, "status": "indexed_local"},
        ]
        self.assertEqual(self.capture(lambda: cmd_sync(self.service, self.args))[0], 0)
        self.assertTrue(all(call.kwargs["local_only"] for call in self.service.sync_repository.call_args_list))
        self.service.sync_repository.reset_mock()
        self.args.repo_id = "https://user:" + SECRET + "@other/missing"
        code, text = self.capture(lambda: cmd_sync(self.service, self.args))
        self.assertEqual(code, 1)
        self.assertIn("repository_not_found", text)
        self.service.sync_repository.assert_not_called()
        self.service.list_repositories.return_value = []
        self.assertEqual(self.capture(lambda: cmd_sync(self.service, self.args))[0], 1)

    def test_add_update_safe_failure_context_and_success(self):
        args = SimpleNamespace(
            repo_id="first", path="https://user:" + SECRET + "@host/repo",
            type="git", name=None, branch="main", no_sync=False, url=SECRET,
        )
        for command, operation in ((cmd_add, self.service.add_repository), (cmd_update, self.service.update_repository)):
            for saved in (False, True):
                error = RepositoryOperationError("git_authentication_failed", "fetch", "first", saved)
                operation.side_effect = error
                code, text = self.capture(lambda command=command: command(self.service, args))
                self.assertEqual(code, 1)
                self.assertIn("Configuration saved; execution failed" if saved else "Configuration was not saved", text)
            operation.side_effect = RuntimeError(SECRET)
            self.assertEqual(self.capture(lambda command=command: command(self.service, args))[0], 1)
            operation.side_effect = None
        self.service.add_repository.return_value = self.repos[0]
        self.assertEqual(self.capture(lambda: cmd_add(self.service, args))[0], 0)
        self.service.update_repository.return_value = {"repo": self.repos[0].to_dict(), "sync_result": self.success}
        self.assertEqual(self.capture(lambda: cmd_update(self.service, args))[0], 0)

    def test_entrypoint_cleanup_before_failed_exit_and_on_success(self):
        for argv, operation in (
            (["main.py", "sync", "--all", "--local-only", "--force"], "sync"),
            (["main.py", "add", "first", SECRET, "--type", "git"], "add"),
            (["main.py", "update", "first", "--url", SECRET], "update"),
        ):
            for fail in (False, True):
                self.service.shutdown.reset_mock()
                self.service.sync_repository.side_effect = [
                    self.success, GitOperationError("git_timeout", "fetch", "middle") if fail else self.success, self.success,
                ]
                self.service.add_repository.side_effect = RepositoryOperationError(config_saved=True) if fail else None
                self.service.update_repository.side_effect = RepositoryOperationError(config_saved=True) if fail else None
                self.service.add_repository.return_value = self.repos[0]
                self.service.update_repository.return_value = {"repo": self.repos[0].to_dict(), "sync_result": self.success}
                with self.subTest(operation=operation, fail=fail), patch.object(sys, "argv", argv), patch("src.cli.main.MultiRepoRAGService", return_value=self.service):
                    def invoke():
                        try:
                            main()
                        except SystemExit as error:
                            self.service.shutdown.assert_called_once()
                            return error.code
                        return 0
                    self.assertEqual(self.capture(invoke)[0], 1 if fail else 0)
                    self.service.shutdown.assert_called_once()

    def test_help_exposes_local_only_without_service_creation(self):
        with patch.object(sys, "argv", ["main.py", "sync", "--help"]), patch("src.cli.main.MultiRepoRAGService") as create:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as caught:
                main()
            self.assertEqual(caught.exception.code, 0)
            self.assertIn("--local-only", out.getvalue())
            create.assert_not_called()

    def test_assignment_flags_forward_omission_selection_and_clearing(self):
        self.service.add_repository.return_value = self.repos[0]
        self.service.update_repository.return_value = {"repo": self.repos[0].to_dict(), "sync_result": None}
        for argv, operation, assignment in (
            (["main.py", "add", "first", "git@fixture.invalid:first", "--type", "git", "--no-sync", "--ssh-key", "key-a"],
             self.service.add_repository, {"ssh_key_id": "key-a"}),
            (["main.py", "update", "first", "--no-sync", "--ssh-key", "key-a"],
             self.service.update_repository, {"ssh_key_id": "key-a"}),
            (["main.py", "update", "first", "--no-sync", "--system-ssh"],
             self.service.update_repository, {"ssh_key_id": None}),
            (["main.py", "update", "first", "--no-sync"], self.service.update_repository, None),
        ):
            with patch.object(sys, "argv", argv), patch("src.cli.main.MultiRepoRAGService", return_value=self.service):
                self.assertEqual(self.capture(main)[0], None)
            self.assertEqual(operation.call_args.kwargs["assignment"], assignment)
        with patch.object(sys, "argv", ["main.py", "update", "first", "--ssh-key", "key-a", "--system-ssh"]), patch("src.cli.main.MultiRepoRAGService") as create:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main()
            self.assertEqual(caught.exception.code, 2)
            create.assert_not_called()
    def test_invalid_interactive_selection_and_catalog_errors_are_safe(self):
        self.args.repo_id, self.args.interactive = None, True
        for selection in ("not-a-number", "99", "-1"):
            with patch("builtins.input", return_value=selection):
                self.assertEqual(self.capture(lambda: cmd_sync(self.service, self.args))[0], 1)
        self.service.sync_repository.assert_not_called()
        self.service.list_repositories.side_effect = RuntimeError(SECRET)
        self.assertEqual(self.capture(lambda: cmd_sync(self.service, self.args))[0], 1)
        self.service.list_repositories.side_effect = None
        self.args.repo_id, self.args.interactive = "first", False
        self.service.resolve_repo_ids.side_effect = RuntimeError(SECRET)
        self.assertEqual(self.capture(lambda: cmd_sync(self.service, self.args))[0], 1)


if __name__ == "__main__":
    unittest.main()
