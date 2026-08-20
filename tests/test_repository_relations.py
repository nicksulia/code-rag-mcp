import unittest
import tempfile
import shutil
from pathlib import Path

from src.ingestion.repo_manager import (
    RepoManager,
    UnknownRepositoryError,
    UnknownGroupError,
    DuplicateGroupError,
    SelfDependencyError,
    DependencyCycleError,
)
from src.models.schema import RepoSourceType


class TestRepositoryRelations(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_manager = RepoManager(data_dir=self.temp_dir)
        # Register dummy repositories
        self.repo_manager.register_repo("repo-a", "Repo A", "local", "/tmp/repo-a")
        self.repo_manager.register_repo("repo-b", "Repo B", "local", "/tmp/repo-b")
        self.repo_manager.register_repo("repo-c", "Repo C", "local", "/tmp/repo-c")
        self.repo_manager.register_repo("repo-d", "Repo D", "local", "/tmp/repo-d")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_group_crud_and_idempotency(self):
        # Create group with initial members
        group = self.repo_manager.create_group("core-services", ["repo-a", "repo-b"])
        self.assertEqual(group.name, "core-services")
        self.assertIn("repo-a", group.members)
        self.assertIn("repo-b", group.members)

        # Duplicate group creation rejected
        with self.assertRaises(DuplicateGroupError):
            self.repo_manager.create_group("core-services")

        # Unknown repo in group creation rejected
        with self.assertRaises(UnknownRepositoryError):
            self.repo_manager.create_group("other-group", ["non-existent-repo"])

        # Add repos to existing group (idempotent)
        updated = self.repo_manager.add_repos_to_group(
            "core-services", ["repo-b", "repo-c"]
        )
        self.assertEqual(sorted(updated.members), ["repo-a", "repo-b", "repo-c"])

        # Add repos to unknown group rejected
        with self.assertRaises(UnknownGroupError):
            self.repo_manager.add_repos_to_group("unknown-group", ["repo-a"])

        # Add unknown repo to existing group rejected
        with self.assertRaises(UnknownRepositoryError):
            self.repo_manager.add_repos_to_group("core-services", ["unknown-repo"])

        # List groups & get members
        groups = self.repo_manager.list_groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].name, "core-services")

        members = self.repo_manager.get_group_members("core-services")
        self.assertEqual(sorted(members), ["repo-a", "repo-b", "repo-c"])

        # Remove member
        removed = self.repo_manager.remove_repo_from_group("core-services", "repo-b")
        self.assertTrue(removed)
        self.assertEqual(
            sorted(self.repo_manager.get_group_members("core-services")),
            ["repo-a", "repo-c"],
        )

        # Remove member not in group returns False
        self.assertFalse(
            self.repo_manager.remove_repo_from_group("core-services", "repo-b")
        )

        # Remove from unknown group raises UnknownGroupError
        with self.assertRaises(UnknownGroupError):
            self.repo_manager.remove_repo_from_group("unknown-group", "repo-a")

        # Delete group
        deleted = self.repo_manager.delete_group("core-services")
        self.assertTrue(deleted)
        self.assertEqual(len(self.repo_manager.list_groups()), 0)
        self.assertFalse(self.repo_manager.delete_group("core-services"))

        # Confirm repos still exist after group deletion
        self.assertIsNotNone(self.repo_manager.get_repo("repo-a"))
        self.assertIsNotNone(self.repo_manager.get_repo("repo-b"))

    def test_multi_group_membership(self):
        self.repo_manager.create_group("frontend", ["repo-a"])
        self.repo_manager.create_group("backend", ["repo-a", "repo-b"])

        relations_a = self.repo_manager.get_repo_relations("repo-a")
        self.assertEqual(sorted(relations_a["groups"]), ["backend", "frontend"])

    def test_dependency_crud_and_idempotency(self):
        # A depends on B
        dep = self.repo_manager.add_dependency("repo-a", "repo-b")
        self.assertEqual(dep.repo_id, "repo-a")
        self.assertEqual(dep.depends_on_repo_id, "repo-b")

        # Idempotent re-declaration
        dep2 = self.repo_manager.add_dependency("repo-a", "repo-b")
        self.assertEqual(dep2.repo_id, "repo-a")

        self.assertEqual(self.repo_manager.get_dependencies("repo-a"), ["repo-b"])
        self.assertEqual(self.repo_manager.get_dependents("repo-b"), ["repo-a"])

        # Remove dependency
        removed = self.repo_manager.remove_dependency("repo-a", "repo-b")
        self.assertTrue(removed)
        self.assertEqual(self.repo_manager.get_dependencies("repo-a"), [])
        self.assertEqual(self.repo_manager.get_dependents("repo-b"), [])
        self.assertFalse(self.repo_manager.remove_dependency("repo-a", "repo-b"))

    def test_dependency_integrity_errors(self):
        # Self-dependency rejected
        with self.assertRaises(SelfDependencyError):
            self.repo_manager.add_dependency("repo-a", "repo-a")

        # Unknown repo rejected
        with self.assertRaises(UnknownRepositoryError):
            self.repo_manager.add_dependency("unknown-repo", "repo-a")
        with self.assertRaises(UnknownRepositoryError):
            self.repo_manager.add_dependency("repo-a", "unknown-repo")

        # Cycle detection: A -> B -> C -> A
        self.repo_manager.add_dependency("repo-a", "repo-b")
        self.repo_manager.add_dependency("repo-b", "repo-c")

        # Adding C -> A should fail with cycle error
        with self.assertRaises(DependencyCycleError):
            self.repo_manager.add_dependency("repo-c", "repo-a")

        # Adding C -> B should fail with cycle error
        with self.assertRaises(DependencyCycleError):
            self.repo_manager.add_dependency("repo-c", "repo-b")

        # Valid non-cyclic edge: D -> A -> B -> C
        self.repo_manager.add_dependency("repo-d", "repo-a")
        self.assertEqual(self.repo_manager.get_dependencies("repo-d"), ["repo-a"])

    def test_empty_relations_for_isolated_repo(self):
        relations = self.repo_manager.get_repo_relations("repo-d")
        self.assertEqual(relations["repo_id"], "repo-d")
        self.assertEqual(relations["groups"], [])
        self.assertEqual(relations["dependencies"], [])
        self.assertEqual(relations["dependents"], [])

        # Nonexistent repo raises UnknownRepositoryError
        with self.assertRaises(UnknownRepositoryError):
            self.repo_manager.get_repo_relations("nonexistent")

    def test_persistence_across_reopened_catalog(self):
        self.repo_manager.create_group("infra", ["repo-a"])
        self.repo_manager.add_dependency("repo-b", "repo-c")

        # Reopen DB in a new RepoManager instance
        new_manager = RepoManager(data_dir=self.temp_dir)
        groups = new_manager.list_groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].name, "infra")
        self.assertEqual(groups[0].members, ["repo-a"])

        self.assertEqual(new_manager.get_dependencies("repo-b"), ["repo-c"])
        self.assertEqual(new_manager.get_dependents("repo-c"), ["repo-b"])

    def test_cascade_delete_repository(self):
        self.repo_manager.create_group("team-x", ["repo-a", "repo-b"])
        self.repo_manager.add_dependency("repo-a", "repo-c")
        self.repo_manager.add_dependency("repo-d", "repo-a")

        # Delete repo-a
        deleted = self.repo_manager.delete_repo("repo-a")
        self.assertTrue(deleted)

        # repo-a should be removed from group team-x, repo-b remains
        self.assertEqual(self.repo_manager.get_group_members("team-x"), ["repo-b"])

        # Dependencies involving repo-a should be cleaned up
        self.assertEqual(self.repo_manager.get_dependents("repo-c"), [])
        self.assertEqual(self.repo_manager.get_dependencies("repo-d"), [])


if __name__ == "__main__":
    unittest.main()
