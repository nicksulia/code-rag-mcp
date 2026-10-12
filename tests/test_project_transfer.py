import contextlib
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.ingestion.repo_manager import (
    PROJECT_FILE_LIMIT,
    DependencyCycleError,
    RepoManager,
    SelfDependencyError,
    TransferConflictError,
    TransferValidationError,
    UnknownRepositoryError,
    normalize_project_document,
)
from src.models.schema import RepoStatus


def repository(
    rid="api", source="git", address="git@offline.invalid:team/api.git", branch="main"
):
    return {
        "repo_id": rid,
        "name": f"Repository {rid}",
        "source_type": source,
        "url_or_path": address,
        "branch": branch,
    }


def document(*repos, groups=None, dependencies=None):
    return {
        "version": 1,
        "repositories": list(repos),
        "groups": groups or [],
        "dependencies": dependencies or [],
    }


class TestProjectTransfer(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = RepoManager(self.temp.name)

    def sql(self, statement, values=()):
        with contextlib.closing(sqlite3.connect(self.manager.db_path)) as conn, conn:
            return conn.execute(statement, values).fetchall()

    def representative(self):
        return document(
            repository("web", "local", " ~/missing/项目 ", None),
            repository(),
            groups=[
                {"name": "平台 <tag> ' \"", "members": ["web", "api"]},
                {"name": "empty", "members": []},
            ],
            dependencies=[{"repo_id": "web", "depends_on_repo_id": "api"}],
        )

    def test_normalization_and_supported_addresses(self):
        for address in (
            "https://example.com/org/a.git",
            "http://example.com/a",
            "ssh://git@example.com:2222/org/a.git",
            "git://example.com/org/a.git",
            "git@example.com:org/a.git",
            "example.com:org/a.git",
        ):
            with self.subTest(address=address):
                result = normalize_project_document(
                    document(repository(address=f" {address} "))
                )
                self.assertEqual(result["repositories"][0]["url_or_path"], address)
        local = repository(" constructor ", " local ", " ../missing/path ", None)
        local["name"] = " 项目 "
        result = normalize_project_document(document(local))
        self.assertEqual(
            result["repositories"][0],
            {
                "repo_id": "constructor",
                "name": "项目",
                "source_type": "local",
                "url_or_path": " ../missing/path ",
                "branch": None,
            },
        )
        self.assertEqual(
            self.manager.import_projects(document())["additions"]["repositories"][
                "count"
            ],
            0,
        )

    def test_invalid_shapes_types_duplicates_and_ids(self):
        invalid = [
            None,
            [],
            {},
            document(repository("../escape")),
            document(repository("-option")),
        ]
        for version in (True, False, 2, "1", 1.0, None):
            invalid.append({**document(), "version": version})
        for field in ("repositories", "groups", "dependencies"):
            invalid.append({**document(), field: {}})
        for field in repository():
            for value in ("", "   ", 3, True, [], {}):
                if field == "branch" and value is None:
                    continue
                invalid.append(document({**repository(), field: value}))
            entry = repository()
            del entry[field]
            invalid.append(document(entry))
        invalid.extend(
            [
                {**document(), "extra": 1},
                document({**repository(), "status": "ready"}),
                document(repository(), repository()),
                document(
                    groups=[
                        {"name": "x", "members": []},
                        {"name": " x ", "members": []},
                    ]
                ),
                document(groups=[{"name": "x", "members": ["api", " api "]}]),
                document(groups=[{"name": "x", "members": "api"}]),
                document(groups=[{"name": "x", "members": [], "extra": 1}]),
                document(
                    dependencies=[{"repo_id": "api", "depends_on_repo_id": "web"}] * 2
                ),
                document(dependencies=[{"repo_id": "api"}]),
                document(repository(branch="\ud800")),
            ]
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(TransferValidationError):
                    self.manager.preview_project_import(value)
        self.assertEqual(self.manager.list_repos(), [])

    def test_credentials_and_unsupported_addresses_never_echo(self):
        for address in (
            "https://user:SECRET@example.com/a.git",
            "https://user@example.com/a",
            "https://example.com/a?token=SECRET",
            "https://example.com/a#SECRET",
            "ssh://git:SECRET@example.com/a",
            "git://user:SECRET@example.com/a",
            "ext::SECRET",
            "file:///SECRET",
            "/local/SECRET",
            "--upload-pack=SECRET",
            "https://example.com",
            "ssh://example.com:bad/a",
            "git@host::SECRET",
            "https://example.com/a?",
            "https://example.com/a#",
            "https://exa\nmple.com/a",
        ):
            with self.subTest(address=address):
                with self.assertRaises(TransferValidationError) as error:
                    self.manager.preview_project_import(
                        document(repository(address=address))
                    )
                self.assertIn("url_or_path", str(error.exception))
                self.assertNotIn(address, str(error.exception))
                self.assertNotIn("SECRET", str(error.exception))

    def test_canonical_export_roundtrip_runtime_and_restart(self):
        incoming = self.representative()
        self.manager.import_projects(incoming)
        first = self.manager.export_projects()
        self.assertTrue(first.endswith(b"\n"))
        self.assertIn("平台".encode(), first)
        decoded = json.loads(first)
        self.assertEqual(
            [repo["repo_id"] for repo in decoded["repositories"]], ["api", "web"]
        )
        self.assertEqual(decoded["groups"][1]["members"], ["api", "web"])
        repo = self.manager.get_repo("api")
        repo.total_files, repo.total_chunks, repo.total_symbols = 3, 7, 2
        repo.commit_hash, repo.last_synced_at = "abc123", 1234
        repo.status, repo.error_message = RepoStatus.DIRTY, "runtime"
        self.manager.save_repo(repo)
        self.manager.commit_file_hashes("api", {"src/a.py": ("sha", 2, 4)}, [])
        index = Path(self.temp.name) / "untouched.index"
        index.write_bytes(b"index contents")
        self.assertEqual(first, self.manager.export_projects())
        before = repo.to_dict()
        self.manager.import_projects(decoded)
        reopened = RepoManager(self.temp.name)
        self.assertEqual(reopened.get_repo("api").to_dict(), before)
        self.assertEqual(reopened.get_all_indexed_files("api"), ["src/a.py"])
        self.assertEqual(index.read_bytes(), b"index contents")
        with tempfile.TemporaryDirectory() as destination:
            fresh = RepoManager(destination)
            summary = fresh.import_projects(decoded)
            self.assertEqual(
                summary["additions"]["repositories"]["ids"], ["api", "web"]
            )
            self.assertEqual(RepoManager(destination).export_projects(), first)
            again = fresh.import_projects(decoded)
            self.assertTrue(
                all(category["count"] == 0 for category in again["additions"].values())
            )
            conflict = copy.deepcopy(decoded)
            conflict["repositories"][0]["branch"] = "different"
            with self.assertRaises(TransferConflictError):
                fresh.import_projects(conflict)
            self.assertEqual(fresh.export_projects(), first)

    def test_empty_export_and_file_limit(self):
        self.assertEqual(
            self.manager.export_projects(),
            (json.dumps(document(), indent=2) + "\n").encode(),
        )
        self.manager.import_projects(document(repository()))
        base = len(self.manager.export_projects())
        name = self.manager.get_repo("api").name
        self.sql(
            "UPDATE repositories SET name = ?",
            ("x" * (PROJECT_FILE_LIMIT - base + len(name)),),
        )
        self.assertEqual(len(self.manager.export_projects()), PROJECT_FILE_LIMIT)
        self.sql("UPDATE repositories SET name = name || 'x'")
        with self.assertRaisesRegex(TransferValidationError, "1048576"):
            self.manager.export_projects()

    def test_legacy_and_dangling_catalog_export_errors(self):
        for entry, field in (
            (repository("../bad"), "repo_id"),
            (repository(source="unknown"), "source_type"),
            (repository(address="https://user:SECRET@host/a"), "url_or_path"),
        ):
            self.sql("DELETE FROM repositories")
            self.sql(
                "INSERT INTO repositories (repo_id,name,source_type,url_or_path,branch,status) VALUES (?,?,?,?,?,?)",
                tuple(entry.values()) + ("ready",),
            )
            with self.assertRaises(TransferValidationError) as error:
                self.manager.export_projects()
            self.assertIn(field, str(error.exception))
            self.assertNotIn("SECRET", str(error.exception))
        self.sql("DELETE FROM repositories")
        self.sql("INSERT INTO repo_group_members VALUES ('missing', 'api')")
        with self.assertRaisesRegex(TransferValidationError, "missing group"):
            self.manager.export_projects()
        self.sql("DELETE FROM repo_group_members")
        self.sql("INSERT INTO repo_groups (name) VALUES ('x')")
        self.sql("INSERT INTO repo_group_members VALUES ('x', 'api')")
        with self.assertRaises(UnknownRepositoryError):
            self.manager.export_projects()
        self.sql("DELETE FROM repo_group_members")
        self.sql("INSERT INTO repo_dependencies VALUES ('api', 'web', 0)")
        with self.assertRaises(UnknownRepositoryError):
            self.manager.export_projects()

    def test_exact_additive_preview_and_preservation(self):
        self.manager.import_projects(
            document(
                repository(),
                repository("other"),
                groups=[{"name": "platform", "members": ["api", "other"]}],
            )
        )
        before = self.manager.export_projects()
        incoming = document(
            repository(),
            repository("web", "local", "/unavailable", None),
            groups=[
                {"name": "empty", "members": []},
                {"name": "platform", "members": ["api", "web"]},
            ],
            dependencies=[{"repo_id": "web", "depends_on_repo_id": "other"}],
        )
        expected = {
            "can_import": True,
            "additions": {
                "repositories": {"count": 1, "ids": ["web"]},
                "groups": {"count": 1, "names": ["empty"]},
                "memberships": {
                    "count": 1,
                    "entries": [{"group": "platform", "repo_id": "web"}],
                },
                "dependencies": {
                    "count": 1,
                    "entries": [{"repo_id": "web", "depends_on_repo_id": "other"}],
                },
            },
            "unchanged_repositories": ["api"],
            "conflicts": [],
            "warnings": [{"repo_id": "web", "code": "local_path_machine_specific"}],
        }
        self.assertEqual(self.manager.preview_project_import(incoming), expected)
        self.assertEqual(before, self.manager.export_projects())
        self.assertEqual(self.manager.import_projects(incoming), expected)
        self.assertEqual(
            self.manager.get_group_members("platform"), ["api", "other", "web"]
        )
        self.assertIsNotNone(self.manager.get_repo("other"))
        again = self.manager.import_projects(incoming)
        self.assertEqual(again["unchanged_repositories"], ["api", "web"])
        self.assertTrue(
            all(category["count"] == 0 for category in again["additions"].values())
        )

    def test_conflicts_and_revalidation_block_all_changes(self):
        incoming = self.representative()
        self.assertTrue(self.manager.preview_project_import(incoming)["can_import"])
        self.manager.import_projects(document(repository(branch="different")))
        before = self.manager.export_projects()
        plan = self.manager.preview_project_import(incoming)
        self.assertFalse(plan["can_import"])
        self.assertEqual(plan["conflicts"], [{"repo_id": "api", "fields": ["branch"]}])
        with self.assertRaises(TransferConflictError):
            self.manager.import_projects(incoming)
        self.assertEqual(before, RepoManager(self.temp.name).export_projects())

    def test_revalidation_reports_actual_smaller_additions(self):
        incoming = self.representative()
        self.manager.preview_project_import(incoming)
        self.manager.import_projects(document(repository()))
        actual = self.manager.import_projects(incoming)
        self.assertEqual(actual["additions"]["repositories"]["ids"], ["web"])
        self.assertEqual(actual["unchanged_repositories"], ["api"])

    def test_relation_integrity_and_combined_cycles(self):
        bad = [
            (
                document(repository(), groups=[{"name": "x", "members": ["unknown"]}]),
                UnknownRepositoryError,
            ),
            (
                document(
                    repository(),
                    dependencies=[{"repo_id": "api", "depends_on_repo_id": "unknown"}],
                ),
                UnknownRepositoryError,
            ),
            (
                document(
                    repository(),
                    dependencies=[{"repo_id": "api", "depends_on_repo_id": "api"}],
                ),
                SelfDependencyError,
            ),
            (
                document(
                    repository(),
                    repository("web"),
                    dependencies=[
                        {"repo_id": "api", "depends_on_repo_id": "web"},
                        {"repo_id": "web", "depends_on_repo_id": "api"},
                    ],
                ),
                DependencyCycleError,
            ),
        ]
        for incoming, error in bad:
            with self.subTest(error=error):
                with self.assertRaises(error):
                    self.manager.import_projects(incoming)
                self.assertEqual(self.manager.list_repos(), [])
        self.manager.import_projects(
            document(
                repository(),
                repository("web"),
                dependencies=[{"repo_id": "web", "depends_on_repo_id": "api"}],
            )
        )
        before = self.manager.export_projects()
        cycle = document(dependencies=[{"repo_id": "api", "depends_on_repo_id": "web"}])
        with self.assertRaises(DependencyCycleError):
            self.manager.preview_project_import(cycle)
        with self.assertRaises(DependencyCycleError):
            self.manager.import_projects(cycle)
        self.assertEqual(self.manager.export_projects(), before)

    def test_rollback_after_repository_insert_survives_restart(self):
        self.sql("""CREATE TRIGGER fail_membership BEFORE INSERT ON repo_group_members
                    BEGIN SELECT RAISE(ABORT, 'injected persistence failure'); END""")
        before = self.manager.export_projects()
        with self.assertRaises(sqlite3.IntegrityError):
            self.manager.import_projects(self.representative())
        self.assertEqual(RepoManager(self.temp.name).export_projects(), before)

    def test_metadata_only_no_source_discovery_or_network(self):
        with contextlib.ExitStack() as stack:
            mocks = [
                stack.enter_context(patch.object(self.manager, name))
                for name in (
                    "register_repo",
                    "save_repo",
                    "create_group",
                    "add_dependency",
                    "get_repo_root_path",
                    "clone_or_pull_git_repo",
                    "scan_repository_files",
                    "get_current_git_commit",
                )
            ]
            mocks += [
                stack.enter_context(patch(target))
                for target in (
                    "os.walk",
                    "os.path.abspath",
                    "os.path.expanduser",
                    "subprocess.run",
                )
            ]
            self.manager.preview_project_import(self.representative())
            self.manager.import_projects(self.representative())
            for mock in mocks:
                mock.assert_not_called()
        for repo in RepoManager(self.temp.name).list_repos():
            self.assertEqual(
                (repo.total_files, repo.total_chunks, repo.total_symbols), (0, 0, 0)
            )
            self.assertEqual(repo.status, RepoStatus.READY)
            self.assertIsNone(repo.commit_hash)
            self.assertIsNone(repo.last_synced_at)
            self.assertIsNone(repo.error_message)

    def test_normalized_existing_definitions_and_relation_keys(self):
        self.manager.register_repo(
            " api ",
            " Repository api ",
            "git",
            " git@offline.invalid:team/api.git ",
            " main ",
        )
        self.sql("INSERT INTO repo_groups (name) VALUES (' platform ')")
        incoming = document(
            repository(), groups=[{"name": "platform", "members": ["api"]}]
        )
        self.assertEqual(
            self.manager.import_projects(incoming)["unchanged_repositories"], ["api"]
        )
        self.assertEqual(
            self.sql("SELECT group_name,repo_id FROM repo_group_members"),
            [(" platform ", " api ")],
        )
        self.assertEqual(
            json.loads(self.manager.export_projects()),
            normalize_project_document(incoming),
        )

    def test_raw_dangling_relations_are_not_hidden_by_normalization(self):
        self.manager.register_repo(" api ", "API", "git", "git@host:api.git")
        self.sql("INSERT INTO repo_groups (name) VALUES ('x')")
        self.sql("INSERT INTO repo_group_members VALUES ('x', 'api')")
        with self.assertRaises(UnknownRepositoryError):
            self.manager.export_projects()
        self.sql("DELETE FROM repo_group_members")
        self.sql("INSERT INTO repo_dependencies VALUES ('api', ' api ', 0)")
        with self.assertRaises(UnknownRepositoryError):
            self.manager.export_projects()

    def test_export_uses_one_consistent_read_transaction(self):
        initial = document(repository())
        self.manager.import_projects(initial)
        self.sql("PRAGMA journal_mode=WAL")
        connect = sqlite3.connect
        statements = []
        mutated = False

        def trace(statement):
            nonlocal mutated
            statements.append(statement)
            if statement == "SELECT name FROM repo_groups" and not mutated:
                mutated = True
                with (
                    contextlib.closing(connect(self.manager.db_path)) as writer,
                    writer,
                ):
                    writer.execute(
                        "INSERT INTO repositories (repo_id,name,source_type,url_or_path,status) "
                        "VALUES ('web','Web','local','/missing','ready')"
                    )
                    writer.execute("INSERT INTO repo_groups (name) VALUES ('later')")
                    writer.execute(
                        "INSERT INTO repo_group_members VALUES ('later','web')"
                    )
                    writer.execute(
                        "INSERT INTO repo_dependencies VALUES ('web','api',0)"
                    )

        def traced_connect(*args, **kwargs):
            conn = connect(*args, **kwargs)
            conn.set_trace_callback(trace)
            return conn

        with patch(
            "src.ingestion.repo_manager.sqlite3.connect", side_effect=traced_connect
        ) as mocked:
            exported = json.loads(self.manager.export_projects())
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(statements[0], "BEGIN")
        self.assertEqual(statements[-1], "COMMIT")
        self.assertEqual(exported, initial)
        later = json.loads(self.manager.export_projects())
        self.assertEqual(len(later["repositories"]), 2)
        self.assertEqual(later["groups"], [{"name": "later", "members": ["web"]}])
        self.assertEqual(
            later["dependencies"], [{"repo_id": "web", "depends_on_repo_id": "api"}]
        )

    def test_confirmation_uses_one_immediate_transaction(self):
        connect = sqlite3.connect
        statements = []

        def traced_connect(*args, **kwargs):
            conn = connect(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn

        with patch(
            "src.ingestion.repo_manager.sqlite3.connect", side_effect=traced_connect
        ) as mocked:
            self.manager.import_projects(self.representative())
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(statements[0], "BEGIN IMMEDIATE")
        self.assertEqual(statements[-1], "COMMIT")
        self.assertEqual(statements.count("COMMIT"), 1)


if __name__ == "__main__":
    unittest.main()
