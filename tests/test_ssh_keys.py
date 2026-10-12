import contextlib
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.ingestion.repo_manager import RepoManager, RepositoryOperationError
from src.models.schema import RepoStatus
from src.service import MultiRepoRAGService
from src.ingestion.ssh_support import SSHError, inspect_private_key, ProtectedFiles


def generate_key(path, passphrase=""):
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", passphrase, "-f", str(path)],
        check=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=15,
    )
    return path


def add_profile(manager, key_id="key-a", name="Key A"):
    path = Path(manager.data_dir) / key_id
    path.write_text("fixture reference")
    with contextlib.closing(manager._connect()) as conn, conn:
        conn.execute(
            "INSERT INTO ssh_key_profiles VALUES (?, ?, ?, ?, ?, ?)",
            (key_id, name, str(path), "SHA256:" + key_id, "ED25519", 123),
        )
    return manager.get_ssh_key(key_id)


class TestSSHKeyCatalog(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = RepoManager(self.temp.name)
        self.repo = self.manager.register_repo("a", "A", "git", "git@offline.invalid:team/a")
        self.manager.register_repo("b", "B", "git", "ssh://git@offline.invalid/b")

    def test_legacy_upgrade_twice_preserves_all_catalog_and_index_state(self):
        repo = self.repo
        repo.status, repo.error_message = RepoStatus.ERROR, "safe error"
        repo.commit_hash, repo.last_synced_at = "a" * 40, 1234
        repo.total_files, repo.total_chunks, repo.total_symbols = 2, 3, 4
        self.manager.save_repo(repo)
        self.manager.create_group("Group", ["a", "b"])
        self.manager.add_dependency("a", "b")
        self.manager.commit_file_hashes("a", {"app.py": ("hash", 42, 10)}, [])
        index = Path(self.temp.name) / "untouched.index"
        index.write_bytes(b"index sentinel")
        with contextlib.closing(self.manager._connect()) as conn, conn:
            conn.execute("DROP TABLE repo_ssh_keys")
            conn.execute("DROP TABLE ssh_key_profiles")
            before = {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in
                      ("repositories", "file_hashes", "repo_groups", "repo_group_members", "repo_dependencies")}
        for _ in range(2):
            reopened = RepoManager(self.temp.name)
            self.assertIsNone(reopened.get_repo("a").ssh_key_id)
            with contextlib.closing(reopened._connect()) as conn:
                for table, rows in before.items():
                    self.assertEqual(conn.execute(f"SELECT * FROM {table}").fetchall(), rows)
            self.assertEqual(index.read_bytes(), b"index sentinel")

    def test_binding_survives_runtime_saves_stale_objects_and_reopening(self):
        profile = add_profile(self.manager)
        self.manager.save_configuration(self.repo, {"ssh_key_id": profile["key_id"]})
        stale = self.manager.get_repo("a")
        add_profile(self.manager, "key-b", "Key B")
        current = self.manager.get_repo("a")
        self.manager.save_configuration(current, {"ssh_key_id": "key-b"})
        stale.status, stale.ssh_key_id = RepoStatus.INDEXING, None
        self.manager.save_repo(stale)
        reopened = RepoManager(self.temp.name)
        self.assertEqual(reopened.get_repo("a").ssh_key_id, "key-b")
        self.assertEqual(reopened.list_repos()[0].to_dict()["ssh_key_id"], "key-b")
        self.assertNotIn("private_key_path", json.dumps([r.to_dict() for r in reopened.list_repos()]))
        self.assertTrue(reopened.delete_repo("a"))
        self.assertEqual(reopened.get_ssh_key("key-b")["repo_ids"], [])
        self.assertTrue(Path(profile["private_key_path"]).exists())

    def test_profile_lifecycle_order_uniqueness_integrity_and_file_preservation(self):
        profile = add_profile(self.manager)
        second = add_profile(self.manager, "key-b", "AAA")
        self.assertEqual([p["key_id"] for p in self.manager.list_ssh_keys()], ["key-b", "key-a"])
        self.assertEqual(self.manager.rename_ssh_key("key-a", " Renamed ")["name"], "Renamed")
        for rid in ("b", "a"):
            self.manager.save_configuration(self.manager.get_repo(rid), {"ssh_key_id": "key-a"})
        self.assertEqual(self.manager.get_ssh_key("key-a")["repo_ids"], ["a", "b"])
        with self.assertRaises(RepositoryOperationError) as caught:
            self.manager.delete_ssh_key("key-a")
        self.assertEqual(caught.exception.code, "ssh_key_in_use")
        self.assertEqual(caught.exception.to_dict()["repo_ids"], ["a", "b"])
        with contextlib.closing(self.manager._connect()) as conn, conn:
            for statement, args in (
                ("INSERT INTO repo_ssh_keys VALUES (?, ?)", ("missing", "key-a")),
                ("DELETE FROM ssh_key_profiles WHERE key_id = ?", ("key-a",)),
                ("INSERT INTO ssh_key_profiles VALUES (?, ?, ?, ?, ?, ?)",
                 ("duplicate", "D", profile["private_key_path"], "other", "ED25519", 1)),
                ("INSERT INTO ssh_key_profiles VALUES (?, ?, ?, ?, ?, ?)",
                 ("duplicate", "D", "/other", profile["fingerprint"], "ED25519", 1)),
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(statement, args)
        self.manager.delete_ssh_key("key-b")
        self.assertTrue(Path(second["private_key_path"]).exists())
        with self.assertRaises(RepositoryOperationError) as caught:
            self.manager.get_ssh_key("key-b")
        self.assertEqual(caught.exception.code, "ssh_key_not_found")

    def test_assignment_semantics_validation_and_rollback(self):
        add_profile(self.manager)
        self.manager.save_configuration(self.repo, {"ssh_key_id": "key-a"})
        self.assertEqual(self.manager.update_repo("a", name="Preserved").ssh_key_id, "key-a")
        before = self.manager.get_repo("a").to_dict()
        for assignment in ({"ssh_key_id": "missing"}, {"ssh_key_id": 1}, {"ssh_key_id": False},
                           {"ssh_key_id": []}, {}, {"ssh_key_id": ""}):
            with self.subTest(assignment=assignment), self.assertRaises(RepositoryOperationError):
                self.manager.update_repo("a", name="Invalid", assignment=assignment)
            self.assertEqual(self.manager.get_repo("a").to_dict(), before)
        for address in ("https://offline.invalid/a", "git://offline.invalid/a", "/local/a"):
            with self.assertRaises(RepositoryOperationError):
                self.manager.update_repo("a", url_or_path=address)
            self.assertEqual(self.manager.get_repo("a").to_dict(), before)
        with patch.object(self.manager, "_save_repo", side_effect=sqlite3.OperationalError("sentinel")):
            with self.assertRaises(RepositoryOperationError) as caught:
                self.manager.update_repo("a", name="Rollback", assignment={"ssh_key_id": None})
            self.assertFalse(caught.exception.config_saved)
        self.assertEqual(self.manager.get_repo("a").to_dict(), before)
        with patch.object(self.manager, "switch_git_branch_or_url"):
            cleared = self.manager.update_repo("a", url_or_path="https://offline.invalid/a",
                                               assignment={"ssh_key_id": None})
        self.assertIsNone(cleared.ssh_key_id)
        local = self.manager.register_repo("local", "Local", "local", self.temp.name)
        with self.assertRaises(RepositoryOperationError):
            self.manager.save_configuration(local, {"ssh_key_id": "key-a"})

    def test_configuration_and_binding_committed_before_git_failure(self):
        service = MultiRepoRAGService(self.temp.name, embedding_provider="subword")
        self.addCleanup(service.shutdown)
        add_profile(self.manager)

        def preparation(repo, *args, **kwargs):
            saved = RepoManager(self.temp.name).get_repo(repo.repo_id)
            self.assertEqual(saved.ssh_key_id, "key-a")
            self.assertEqual(saved.branch, repo.branch)
            raise RepositoryOperationError("git_authentication_failed", "clone", repo.repo_id)

        with patch.object(service.repo_manager, "clone_or_pull_git_repo", side_effect=preparation):
            with self.assertRaises(RepositoryOperationError) as caught:
                service.add_repository("first", "First", "git", "git@offline.invalid:first",
                                       assignment={"ssh_key_id": "key-a"})
            self.assertTrue(caught.exception.config_saved)
        with patch.object(service.repo_manager, "switch_git_branch_or_url", side_effect=preparation):
            with self.assertRaises(RepositoryOperationError) as caught:
                service.update_repository("a", branch="feature", auto_sync=False,
                                          assignment={"ssh_key_id": "key-a"})
            self.assertTrue(caught.exception.config_saved)

    def test_transfer_bytes_and_insert_only_import_exclude_keys(self):
        self.manager.create_group("Group", ["a", "b"])
        self.manager.add_dependency("a", "b")
        before = self.manager.export_projects()
        add_profile(self.manager)
        self.manager.save_configuration(self.repo, {"ssh_key_id": "key-a"})
        self.manager.rename_ssh_key("key-a", "Renamed")
        self.assertEqual(self.manager.export_projects(), before)
        self.assertNotIn(b"ssh_key", before)
        repo = self.manager.get_repo("a")
        repo.status, repo.last_synced_at, repo.total_chunks = RepoStatus.DIRTY, 123, 10
        self.manager.save_repo(repo)
        self.manager.commit_file_hashes("a", {"app.py": ("sha", 1, 2)}, [])
        self.manager.import_projects(json.loads(before))
        self.assertEqual(self.manager.get_repo("a").to_dict(), repo.to_dict())
        self.assertEqual(self.manager.get_all_indexed_files("a"), ["app.py"])
        self.assertEqual(self.manager.get_group_members("Group"), ["a", "b"])
        self.assertEqual(self.manager.get_dependencies("a"), ["b"])
        with tempfile.TemporaryDirectory() as destination:
            fresh = RepoManager(destination)
            fresh.import_projects(json.loads(before))
            self.assertTrue(all(r.ssh_key_id is None for r in fresh.list_repos()))
            self.assertEqual(fresh.list_ssh_keys(), [])
            self.assertEqual(fresh.export_projects(), before)


class TestSSHKeyValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = RepoManager(str(Path(self.temp.name) / "data"))
        self.key = generate_key(Path(self.temp.name).resolve() / "selected key")

    def test_real_unencrypted_and_encrypted_public_metadata_and_path_formats(self):
        for index, phrase in enumerate(("", "synthetic-unlock-fixture")):
            path = self.key if not phrase else generate_key(Path(self.temp.name) / "locked", phrase)
            expected = subprocess.run(["ssh-keygen", "-l", "-E", "sha256", "-f", str(path)],
                                      check=True, capture_output=True, text=True, timeout=15).stdout.split()[1]
            profile = self.manager.register_ssh_key("x" * 80, str(path))
            self.assertEqual(profile["fingerprint"], expected)
            self.assertEqual(profile["algorithm"], "ED25519")
            self.assertEqual(set(profile), {"key_id", "name", "private_key_path", "fingerprint", "algorithm", "repo_ids"})
            with contextlib.closing(self.manager._connect()) as conn:
                stored = repr(conn.execute("SELECT * FROM ssh_key_profiles").fetchall())
            self.assertNotIn("PRIVATE KEY", stored)
            self.assertNotIn(phrase if phrase else "PRIVATE KEY", stored)
        with patch.dict(os.environ, {"HOME": self.temp.name}):
            self.assertEqual(inspect_private_key("~/selected key")[0], self.key)
        padded = "/" * (4096 - len(str(self.key)) + 1) + str(self.key)[1:]
        self.assertEqual(len(padded.encode()), 4096)
        self.assertEqual(inspect_private_key(padded)[0], self.key)
        with self.assertRaises(SSHError):
            inspect_private_key("/" + padded)

    def test_invalid_paths_names_format_permissions_ownership_no_mutation(self):
        for name in ("", "   ", "x" * 81, "line\nbreak", "control\x7f", "control\x85", "\ud800"):
            with self.subTest(name=repr(name)), self.assertRaises(RepositoryOperationError):
                self.manager.register_ssh_key(name, str(self.key))
        for path in ("selected key", "~other/key", "", str(self.key) + "\x00", str(self.key) + "\n",
                     str(self.key) + ".pub", self.temp.name, str(self.key) + "-missing", "\ud800"):
            with self.subTest(path=repr(path)), self.assertRaises(RepositoryOperationError):
                self.manager.register_ssh_key("Key", path)
        before = self.key.read_bytes()
        for mode in (0o640, 0o601, 0o000):
            self.key.chmod(mode)
            with self.assertRaises(RepositoryOperationError):
                self.manager.register_ssh_key("Key", str(self.key))
            self.assertEqual(self.key.stat().st_mode & 0o777, mode)
        self.key.chmod(0o600)
        with patch("src.ingestion.ssh_support.os.geteuid", return_value=self.key.stat().st_uid + 1):
            with self.assertRaises(RepositoryOperationError):
                self.manager.register_ssh_key("Key", str(self.key))
        for content in (b"-----BEGIN RSA PRIVATE KEY-----\nSENTINEL_SECRET\n", b"arbitrary text"):
            self.key.write_bytes(content)
            with self.assertRaises(RepositoryOperationError):
                self.manager.register_ssh_key("Key", str(self.key))
        self.key.write_bytes(before)
        self.assertEqual(self.manager.list_ssh_keys(), [])
        self.assertEqual(self.key.read_bytes(), before)

    def test_duplicates_tools_deadline_and_unsupported_output_are_safe(self):
        profile = self.manager.register_ssh_key("First", str(self.key))
        alias = Path(self.temp.name) / "alias"
        alias.symlink_to(self.key)
        copy = Path(self.temp.name) / "copy"
        copy.write_bytes(self.key.read_bytes())
        copy.chmod(0o600)
        for path in (self.key, alias, copy):
            with self.assertRaises(RepositoryOperationError) as caught:
                self.manager.register_ssh_key("Duplicate", str(path))
            self.assertEqual(caught.exception.code, "ssh_key_conflict")
        second = generate_key(Path(self.temp.name) / "second")
        for cause, code in (
            (FileNotFoundError("SENTINEL_SECRET"), "ssh_tool_unavailable"),
            (subprocess.TimeoutExpired(["ssh-keygen", "SENTINEL_SECRET"], 15), "ssh_key_timeout"),
            (subprocess.CalledProcessError(1, ["ssh-keygen"], stderr="SENTINEL_SECRET"), "ssh_key_invalid"),
        ):
            with patch("src.ingestion.ssh_support.subprocess.run", side_effect=cause) as run:
                with self.assertRaises(RepositoryOperationError) as caught:
                    self.manager.register_ssh_key("Second", str(second))
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("SENTINEL_SECRET", str(caught.exception.to_dict()))
                self.assertEqual(run.call_args.kwargs["timeout"], 15)
                self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        with patch("src.ingestion.ssh_support.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, "SENTINEL_SECRET", "")):
            with self.assertRaises(RepositoryOperationError):
                self.manager.register_ssh_key("Second", str(second))
        self.assertEqual(self.manager.list_ssh_keys(), [profile])

    def test_revalidate_missing_changed_and_insecure_key(self):
        profile = self.manager.register_ssh_key("Key", str(self.key))
        self.assertEqual(inspect_private_key(str(self.key), profile["fingerprint"])[1], profile["fingerprint"])
        replacement = generate_key(Path(self.temp.name) / "replacement")
        self.key.write_bytes(replacement.read_bytes())
        with self.assertRaises(SSHError) as caught:
            inspect_private_key(str(self.key), profile["fingerprint"])
        self.assertEqual(caught.exception.code, "ssh_key_changed")
        self.key.chmod(0o644)
        with self.assertRaises(SSHError) as caught:
            inspect_private_key(str(self.key), profile["fingerprint"])
        self.assertEqual(caught.exception.code, "ssh_key_unavailable")
        self.key.unlink()
        with self.assertRaises(SSHError) as caught:
            inspect_private_key(str(self.key), profile["fingerprint"])
        self.assertEqual(caught.exception.code, "ssh_key_unavailable")
        self.assertTrue(ProtectedFiles([str(self.key)]).contains(self.key))

    def test_unavailable_reference_directory_does_not_block_ordinary_cached_files(self):
        ordinary = Path(self.temp.name) / "ordinary.py"
        ordinary.write_text("def ordinary(): pass\n")
        resolve = Path.resolve
        stat = Path.stat

        def unavailable_resolve(path, *args, **kwargs):
            if path == self.key:
                raise PermissionError("SENTINEL_SECRET")
            return resolve(path, *args, **kwargs)

        def unavailable_stat(path, *args, **kwargs):
            if path == self.key:
                raise PermissionError("SENTINEL_SECRET")
            return stat(path, *args, **kwargs)

        with patch.object(Path, "resolve", unavailable_resolve), patch.object(Path, "stat", unavailable_stat):
            with self.assertLogs("rag.repository", level="WARNING") as logs:
                protection = ProtectedFiles([str(self.key)])
            self.assertFalse(protection.contains(ordinary))
            self.assertTrue(protection.contains(self.key))
        self.assertNotIn("SENTINEL_SECRET", repr(logs.output))
