"""
Unit tests for Ollama embedding (default Qwen3-Embedding-0.6B) integration.
"""

import unittest
from unittest.mock import patch, MagicMock
import io
import json
from src.indexer.embeddings import OllamaEmbeddingEngine, EmbeddingFactory


class TestOllamaIntegration(unittest.TestCase):
    def test_ollama_embedding_engine_factory(self):
        engine = EmbeddingFactory.create(
            provider="ollama", model="qwen3-embedding:8b", host="http://localhost:11434"
        )
        self.assertIsInstance(engine, OllamaEmbeddingEngine)
        self.assertEqual(engine.model, "qwen3-embedding:8b")
        self.assertEqual(engine.host, "http://localhost:11434")

    @patch("urllib.request.urlopen")
    def test_ollama_api_embed_encoding(self, mock_urlopen):
        # Mock /api/embed response with 4096-dim vector for Qwen3-Embedding-8B
        mock_embedding = [0.01] * 4096
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"model": "qwen3-embedding:8b", "embeddings": [mock_embedding]}
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        engine = OllamaEmbeddingEngine(
            model="qwen3-embedding:8b", host="http://localhost:11434"
        )
        vec = engine.encode_text(
            "def authenticate_user(): pass", field_type="signature"
        )

        self.assertEqual(len(vec), 4096)
        self.assertEqual(engine.dimension, 4096)

    @patch("urllib.request.urlopen")
    def test_ollama_model_presence_check(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(
            {
                "models": [
                    {"name": "qwen3-embedding:8b", "size": 4500000000},
                    {"name": "qwen2.5-coder:7b", "size": 4700000000},
                ]
            }
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        engine = OllamaEmbeddingEngine(
            model="qwen3-embedding:8b", host="http://localhost:11434"
        )
        self.assertTrue(engine.is_server_online())
        self.assertTrue(engine.is_model_available("qwen3-embedding:8b"))
        self.assertFalse(engine.is_model_available("llama3.3:70b"))

        status = engine.ensure_model_ready()
        self.assertTrue(status["ok"])

    @patch("urllib.request.urlopen")
    def test_ollama_pull_model_progress(self, mock_urlopen):
        # Simulate streaming JSON chunks during pull
        mock_response = MagicMock()
        mock_response.__iter__.return_value = [
            json.dumps({"status": "pulling manifest"}).encode("utf-8") + b"\n",
            json.dumps(
                {"status": "downloading", "completed": 500, "total": 1000}
            ).encode("utf-8")
            + b"\n",
            json.dumps({"status": "success"}).encode("utf-8") + b"\n",
        ]
        mock_urlopen.return_value.__enter__.return_value = mock_response

        engine = OllamaEmbeddingEngine(
            model="qwen3-embedding:8b", host="http://localhost:11434"
        )
        progress_events = []

        def on_progress(msg, c, t):
            progress_events.append((msg, c, t))

        ok = engine.pull_model("qwen3-embedding:8b", progress_callback=on_progress)
        self.assertTrue(ok)
        self.assertEqual(len(progress_events), 3)
        self.assertEqual(progress_events[1], ("downloading", 500, 1000))

    @patch("urllib.request.urlopen")
    def test_ollama_unload_model(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response

        engine = OllamaEmbeddingEngine(
            model="qwen3-embedding:0.6b", host="http://localhost:11434"
        )
        ok = engine.unload_model()
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
