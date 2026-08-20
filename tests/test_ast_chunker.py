"""
Unit tests for ASTChunker across multiple languages.
"""

import unittest
from src.parser.ast_chunker import ASTChunker
from src.models.schema import ChunkType


class TestASTChunker(unittest.TestCase):
    def setUp(self):
        self.chunker = ASTChunker()

    def test_python_chunking(self):
        code = '''
import os
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
        self.assertTrue(len(chunks) >= 3)

        symbols = [c.symbol_name for c in chunks if c.symbol_name]
        self.assertIn("PaymentProcessor", symbols)
        self.assertIn("process_charge", symbols)
        self.assertIn("calculate_tax", symbols)

        # Context header check
        tax_chunk = next(c for c in chunks if c.symbol_name == "calculate_tax")
        self.assertIn(
            "// [Context] Repository: backend-repo", tax_chunk.enriched_content
        )
        self.assertIn("calculate_tax", tax_chunk.enriched_content)

    def test_typescript_chunking(self):
        code = """
import { useState } from 'react';

export interface UserDTO {
  id: string;
  name: string;
}

export async function fetchUser(id: string): Promise<UserDTO> {
  const res = await fetch(`/users/${id}`);
  return res.json();
}
"""
        chunks = self.chunker.chunk_file("web-repo", "src/client.ts", code)
        self.assertTrue(len(chunks) >= 2)
        symbols = [c.symbol_name for c in chunks if c.symbol_name]
        self.assertIn("UserDTO", symbols)
        self.assertIn("fetchUser", symbols)

    def test_go_chunking(self):
        code = """
package auth

type Session struct {
    Token string
    TTL   int64
}

func ValidateSession(s *Session) bool {
    return s.TTL > 0
}
"""
        chunks = self.chunker.chunk_file("auth-repo", "pkg/session.go", code)
        self.assertTrue(len(chunks) >= 2)
        symbols = [c.symbol_name for c in chunks if c.symbol_name]
        self.assertIn("Session", symbols)
        self.assertIn("ValidateSession", symbols)

    def test_markdown_chunking(self):
        doc = """
# Architecture Overview
This is the global system architecture.

## Authentication Microservice
The auth service issues JWTs.

## Billing Microservice
The billing service talks to Stripe.
"""
        chunks = self.chunker.chunk_file("docs-repo", "docs/arch.md", doc)
        self.assertTrue(len(chunks) >= 3)


if __name__ == "__main__":
    unittest.main()
