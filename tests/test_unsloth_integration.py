"""
Unit tests for Unsloth / vLLM / OpenAI-compatible embedding integration.
"""

import unittest
from unittest.mock import patch, MagicMock
import json
from src.indexer.embeddings import UnslothEmbeddingEngine, EmbeddingFactory


class TestUnslothIntegration(unittest.TestCase):
    def test_unsloth_embedding_factory(self):
        engine = EmbeddingFactory.create(
            provider="unsloth",
            model="Qwen/Qwen2.5-Coder-7B",
            host="http://localhost:8000/v1",
        )
        self.assertIsInstance(engine, UnslothEmbeddingEngine)
        self.assertEqual(engine.model, "Qwen/Qwen2.5-Coder-7B")
        self.assertEqual(engine.base_url, "http://localhost:8000/v1")

    @patch("urllib.request.urlopen")
    def test_unsloth_embeddings_call(self, mock_urlopen):
        mock_embedding = [0.05] * 4096
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"data": [{"embedding": mock_embedding, "index": 0}]}
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        engine = UnslothEmbeddingEngine(
            base_url="http://localhost:8000/v1", model="Qwen/Qwen2.5-Coder-7B"
        )
        vec = engine.encode_text("def test_function(): pass", field_type="signature")

        self.assertEqual(len(vec), 4096)
        self.assertEqual(engine.dimension, 4096)


if __name__ == "__main__":
    unittest.main()
