"""
Unit and integration tests for LangChainCodeChunker across multiple languages.
"""

import unittest
from src.parser.langchain_chunker import (
    LangChainCodeChunker,
    RecursiveCharacterTextSplitterCore,
    ASTChunker,
    LANGCHAIN_SEPARATORS,
    SupportedLanguage,
)
from src.models.schema import ChunkType


class TestLangChainCodeChunker(unittest.TestCase):
    def setUp(self):
        self.chunker = LangChainCodeChunker(chunk_size=500, chunk_overlap=80)

    def test_python_chunking(self):
        code = '''import os
import hashlib

class PaymentProcessor:
    """Handles credit card and crypto billing."""
    def __init__(self, key: str):
        self.key = key

    def process_charge(self, amount: float) -> bool:
        """Executes payment."""
        return amount > 0

def calculate_tax(subtotal: float) -> float:
    return subtotal * 0.08
'''
        chunks = self.chunker.chunk_file("backend-repo", "src/billing.py", code)
        self.assertTrue(len(chunks) >= 2)

        symbols = [c.symbol_name for c in chunks if c.symbol_name]
        self.assertTrue(
            any(
                s in symbols
                for s in ("PaymentProcessor", "process_charge", "calculate_tax")
            )
        )

        # Check line coordinates
        for c in chunks:
            self.assertGreaterEqual(c.start_line, 1)
            self.assertGreaterEqual(c.end_line, c.start_line)
            self.assertIn("billing.py", c.enriched_content)
            self.assertEqual(c.language, "python")
            self.assertEqual(c.repo_id, "backend-repo")
            self.assertTrue(len(c.chunk_id) > 0)

    def test_typescript_chunking(self):
        code = """import { useState } from 'react';

export interface UserDTO {
  id: string;
  name: string;
  email: string;
}

export async function fetchUser(id: string): Promise<UserDTO> {
  const res = await fetch(`/users/${id}`);
  return res.json();
}

export const formatUserName = (user: UserDTO) => {
  return `${user.name} <${user.email}>`;
};
"""
        chunks = self.chunker.chunk_file("web-repo", "src/client.ts", code)
        self.assertTrue(len(chunks) >= 2)
        symbols = [c.symbol_name for c in chunks if c.symbol_name]
        self.assertTrue(
            "UserDTO" in symbols
            or "fetchUser" in symbols
            or "formatUserName" in symbols
        )

        ts_chunk = next(c for c in chunks if "fetchUser" in c.raw_content)
        self.assertEqual(ts_chunk.language, "typescript")
        self.assertIn("// [Context] Repository: web-repo", ts_chunk.enriched_content)

    def test_go_chunking(self):
        code = """package auth

import "time"

type Session struct {
    Token string
    TTL   int64
}

type AuthProvider interface {
    Authenticate(token string) bool
}

func ValidateSession(s *Session) bool {
    return s.TTL > 0
}
"""
        chunks = self.chunker.chunk_file("auth-repo", "pkg/session.go", code)
        self.assertTrue(len(chunks) >= 2)
        symbols = [c.symbol_name for c in chunks if c.symbol_name]
        self.assertTrue(
            "Session" in symbols
            or "ValidateSession" in symbols
            or "AuthProvider" in symbols
        )

    def test_rust_chunking(self):
        code = """pub struct Config {
    pub port: u16,
    pub host: String,
}

pub trait Server {
    fn start(&self) -> Result<(), String>;
}

pub async fn run_server(config: Config) -> Result<(), String> {
    println!("Running on {}:{}", config.host, config.port);
    Ok(())
}
"""
        chunks = self.chunker.chunk_file("rust-repo", "src/server.rs", code)
        self.assertTrue(len(chunks) >= 2)
        symbols = [c.symbol_name for c in chunks if c.symbol_name]
        self.assertTrue("Config" in symbols or "run_server" in symbols)

    def test_markdown_chunking(self):
        doc = """# Architecture Overview
This is the global system architecture for the multi-repo platform.

## Authentication Microservice
The auth service issues signed JWT tokens and validates HMAC requests.

## Billing Microservice
The billing service talks to Stripe and handles invoices.

## Database Layer
PostgreSQL and Redis clusters store relational and cached entities.
"""
        chunks = self.chunker.chunk_file("docs-repo", "docs/architecture.md", doc)
        self.assertTrue(len(chunks) >= 3)
        doc_chunks = [c for c in chunks if c.chunk_type == ChunkType.DOC]
        self.assertTrue(len(doc_chunks) >= 2)

    def test_sql_chunking(self):
        sql = """CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES users(id),
    amount NUMERIC(10, 2) NOT NULL
);

SELECT u.name, SUM(o.amount)
FROM users u
JOIN orders o ON u.id = o.user_id
GROUP BY u.name;
"""
        chunks = self.chunker.chunk_file("db-repo", "migrations/001_init.sql", sql)
        self.assertTrue(len(chunks) >= 2)
        self.assertEqual(chunks[0].language, "sql")

    def test_deterministic_chunk_id(self):
        code = "def foo():\n    return 42\n"
        chunks1 = self.chunker.chunk_file("repo-a", "main.py", code)
        chunks2 = self.chunker.chunk_file("repo-a", "main.py", code)
        self.assertEqual(len(chunks1), len(chunks2))
        self.assertEqual(chunks1[0].chunk_id, chunks2[0].chunk_id)

    def test_legacy_ast_chunker_alias(self):
        chunker = ASTChunker()
        chunks = chunker.chunk_file("repo-test", "test.py", "def bar():\n    pass\n")
        self.assertTrue(len(chunks) >= 1)
        self.assertEqual(chunks[0].language, "python")

    def test_splitter_core_overlap(self):
        splitter = RecursiveCharacterTextSplitterCore(chunk_size=50, chunk_overlap=15)
        text = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua."
        splits = splitter.split_text(text)
        self.assertTrue(len(splits) > 1)
        for s in splits:
            self.assertTrue(len(s) <= 80)


if __name__ == "__main__":
    unittest.main()
