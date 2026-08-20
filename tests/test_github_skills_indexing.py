"""
Unit and integration tests for .github/skills/* indexing.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from src.ingestion.repo_manager import RepoManager, ALLOWED_HIDDEN_DIRS
from src.parser.langchain_chunker import LangChainCodeChunker
from src.models.schema import Repository, RepoSourceType, RepoStatus, ChunkType


class TestGitHubSkillsIndexing(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.test_dir, "data")
        self.repo_dir = os.path.join(self.test_dir, "mock_repo")
        os.makedirs(self.repo_dir, exist_ok=True)
        self.repo_manager = RepoManager(data_dir=self.data_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_allowed_hidden_dirs_constant(self):
        self.assertIn(".github", ALLOWED_HIDDEN_DIRS)

    def test_is_path_ignored_rules(self):
        # Strictly allowed: .github/skills/*
        self.assertFalse(
            self.repo_manager.is_path_ignored(".github/skills/job-polling/SKILL.md", [])
        )
        self.assertFalse(
            self.repo_manager.is_path_ignored(
                ".github/skills/job-polling/references/architecture.md", []
            )
        )
        self.assertFalse(
            self.repo_manager.is_path_ignored(
                ".github/skills/job-polling/assets/usecase.template.py", []
            )
        )
        self.assertFalse(
            self.repo_manager.is_path_ignored(
                ".github/skills/job-management/SKILL.md", []
            )
        )

        # Strictly allowed: .github/workflows/*
        self.assertFalse(
            self.repo_manager.is_path_ignored(".github/workflows/unit-tests.yml", [])
        )
        self.assertFalse(
            self.repo_manager.is_path_ignored(".github/workflows/release.yaml", [])
        )
        self.assertFalse(
            self.repo_manager.is_path_ignored(
                ".github/workflows/nested/reusable-build.yml", []
            )
        )

        # Standard source and doc files
        self.assertFalse(self.repo_manager.is_path_ignored("src/main.py", []))
        self.assertFalse(self.repo_manager.is_path_ignored("docs/README.md", []))

        # Non-skills, non-workflows .github files MUST BE IGNORED
        self.assertTrue(
            self.repo_manager.is_path_ignored(".github/agents/dev.agent.md", [])
        )
        self.assertTrue(
            self.repo_manager.is_path_ignored(".github/agents/plan.agent.md", [])
        )
        self.assertTrue(
            self.repo_manager.is_path_ignored(".github/prompts/bootstrap.prompt.md", [])
        )
        self.assertTrue(
            self.repo_manager.is_path_ignored(".github/prompts/plan.prompt.md", [])
        )
        self.assertTrue(
            self.repo_manager.is_path_ignored(
                ".github/instructions/python.instructions.md", []
            )
        )
        self.assertTrue(
            self.repo_manager.is_path_ignored(".github/copilot-instructions.md", [])
        )
        self.assertTrue(
            self.repo_manager.is_path_ignored(".github/shared/memory-protocol.md", [])
        )
        self.assertTrue(self.repo_manager.is_path_ignored(".github/memory.md", []))
        self.assertTrue(self.repo_manager.is_path_ignored(".github/USAGE.md", []))
        self.assertTrue(self.repo_manager.is_path_ignored(".github/.gitattributes", []))
        self.assertTrue(
            self.repo_manager.is_path_ignored(".github/skills/.gitattributes", [])
        )
        self.assertTrue(
            self.repo_manager.is_path_ignored(".github/workflows/.gitattributes", [])
        )

        # Other hidden system and dependency dirs
        self.assertTrue(self.repo_manager.is_path_ignored(".git/config", []))
        self.assertTrue(self.repo_manager.is_path_ignored(".git/HEAD", []))
        self.assertTrue(self.repo_manager.is_path_ignored(".vscode/settings.json", []))
        self.assertTrue(self.repo_manager.is_path_ignored(".idea/workspace.xml", []))
        self.assertTrue(self.repo_manager.is_path_ignored(".venv/bin/python", []))
        self.assertTrue(
            self.repo_manager.is_path_ignored("node_modules/package.json", [])
        )

    def test_scan_repository_includes_github_skills_and_workflows(self):
        # 1. Allowed skill files
        skill_dir = Path(self.repo_dir) / ".github" / "skills" / "control-plane"
        skill_dir.mkdir(parents=True, exist_ok=True)

        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            """# Control Plane Domain Skill
This skill explains the control plane event flows and architecture.
""",
            encoding="utf-8",
        )

        ref_file = skill_dir / "references" / "architecture.md"
        ref_file.parent.mkdir(parents=True, exist_ok=True)
        ref_file.write_text(
            """# Architecture Reference
Details on the database partitions.
""",
            encoding="utf-8",
        )

        template_file = skill_dir / "assets" / "endpoint_template.py"
        template_file.parent.mkdir(parents=True, exist_ok=True)
        template_file.write_text(
            """def handle_request(event, context):
    return {"statusCode": 200}
""",
            encoding="utf-8",
        )

        # 2. Non-skill files inside .github that MUST be excluded
        agent_dir = Path(self.repo_dir) / ".github" / "agents"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "dev.agent.md").write_text("# Dev Agent\n", encoding="utf-8")

        wf_dir = Path(self.repo_dir) / ".github" / "workflows"
        wf_dir.mkdir(parents=True, exist_ok=True)
        (wf_dir / "ci.yml").write_text("name: CI\n", encoding="utf-8")

        prompt_dir = Path(self.repo_dir) / ".github" / "prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        (prompt_dir / "plan.prompt.md").write_text("# Plan Prompt\n", encoding="utf-8")

        (Path(self.repo_dir) / ".github" / "memory.md").write_text(
            "# Memory\n", encoding="utf-8"
        )
        (Path(self.repo_dir) / ".github" / ".gitattributes").write_text(
            "* text=auto\n", encoding="utf-8"
        )

        # 3. Standard src file
        src_file = Path(self.repo_dir) / "src" / "app.py"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text("print('hello')\n", encoding="utf-8")

        # Register and scan
        repo = self.repo_manager.register_repo(
            repo_id="test-skill-repo",
            name="Test Skill Repo",
            source_type="local",
            url_or_path=self.repo_dir,
        )

        added, modified, deleted = self.repo_manager.scan_repository_files(repo)

        # Must include
        self.assertIn(".github/skills/control-plane/SKILL.md", added)
        self.assertIn(".github/skills/control-plane/references/architecture.md", added)
        self.assertIn(".github/skills/control-plane/assets/endpoint_template.py", added)
        self.assertIn(".github/workflows/ci.yml", added)
        self.assertIn("src/app.py", added)

        # Must NOT include
        self.assertNotIn(".github/agents/dev.agent.md", added)
        self.assertNotIn(".github/prompts/plan.prompt.md", added)
        self.assertNotIn(".github/memory.md", added)
        self.assertNotIn(".github/.gitattributes", added)

    def test_chunking_workflow_yaml(self):
        chunker = LangChainCodeChunker(chunk_size=400, chunk_overlap=50)
        content = """name: CI

on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        run: pytest
"""
        chunks = chunker.chunk_file(
            repo_id="test-repo", file_path=".github/workflows/ci.yml", content=content
        )

        self.assertGreater(len(chunks), 0)
        self.assertEqual(chunks[0].language, "yaml")

    def test_chunking_skill_markdown(self):
        chunker = LangChainCodeChunker(chunk_size=400, chunk_overlap=50)
        content = """# Control Plane Skill

## Overview
Provides control plane orchestration instructions.

## Step 1: Initialization
Configure credentials and environment settings.

## Step 2: Dispatch
Dispatch the job payload to workers.
"""
        chunks = chunker.chunk_file(
            repo_id="test-repo",
            file_path=".github/skills/control-plane/SKILL.md",
            content=content,
        )

        self.assertGreater(len(chunks), 0)
        self.assertEqual(chunks[0].language, "markdown")
        self.assertEqual(chunks[0].chunk_type, ChunkType.DOC)
        self.assertIn("Control Plane Skill", chunks[0].raw_content)


if __name__ == "__main__":
    unittest.main()
