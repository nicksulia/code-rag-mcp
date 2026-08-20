"""
Unit tests for on-demand embedding model residency, single-instance ownership,
dense index provenance, and automatic reindex on embedding model change.
"""

import json
import time
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock

from src.indexer.embeddings import (
    OllamaEmbeddingEngine,
    EmbeddingFactory,
    DEFAULT_OLLAMA_EMBEDDING_MODEL,
)
from src.indexer.lifecycle import EmbeddingLifecycle, KeepAlivePolicy
from src.indexer.index_meta import IndexProvenanceStore
from src.instance_lock import InstanceLock, InstanceLockError, acquire_instance_lock
from src.service import MultiRepoRAGService, ReindexInProgressError


class FakeEngine:
    """Minimal engine double recording load/unload interactions."""

    model = "fake-embedding:0.6b"

    def __init__(self):
        self.unload_calls = 0
        self.lifecycle = None

    def attach_lifecycle(self, lifecycle):
        self.lifecycle = lifecycle

    def embed(self):
        self.lifecycle.note_loaded(0.01)

    def unload_model(self):
        self.unload_calls += 1
        return True


class TestKeepAlivePolicy(unittest.TestCase):
    def test_default_policy_is_on_demand(self):
        with patch.dict("os.environ", {}, clear=True):
            policy = KeepAlivePolicy.parse(None)
        self.assertEqual(policy.mode, "on-demand")
        self.assertEqual(policy.idle_grace_seconds, 30.0)
        self.assertFalse(policy.always_resident)

    def test_zero_means_immediate_release(self):
        policy = KeepAlivePolicy.parse("0")
        self.assertEqual(policy.mode, "immediate")
        self.assertEqual(policy.idle_grace_seconds, 0.0)

    def test_always_resident(self):
        policy = KeepAlivePolicy.parse("always")
        self.assertTrue(policy.always_resident)
        self.assertEqual(policy.keep_alive_value, -1)

    def test_duration_parsing(self):
        self.assertEqual(KeepAlivePolicy.parse("45s").idle_grace_seconds, 45.0)
        self.assertEqual(KeepAlivePolicy.parse("2m").idle_grace_seconds, 120.0)
        self.assertEqual(KeepAlivePolicy.parse("90").idle_grace_seconds, 90.0)


class TestEmbeddingLifecycle(unittest.TestCase):
    def _lifecycle(self, policy="0"):
        engine = FakeEngine()
        return engine, EmbeddingLifecycle(engine=engine, policy=policy)

    def test_no_load_or_release_without_operations(self):
        engine, lifecycle = self._lifecycle()
        self.assertFalse(lifecycle.is_loaded)
        self.assertEqual(engine.unload_calls, 0)

    def test_single_release_after_last_overlapping_session(self):
        engine, lifecycle = self._lifecycle()
        with lifecycle.session("a"):
            engine.embed()
            with lifecycle.session("b"):
                engine.embed()
                with lifecycle.session("c"):
                    engine.embed()
                    self.assertEqual(lifecycle.active_operations, 3)
                self.assertEqual(engine.unload_calls, 0)
            self.assertEqual(engine.unload_calls, 0)
        self.assertEqual(engine.unload_calls, 1)
        self.assertFalse(lifecycle.is_loaded)

    def test_release_on_exception_path(self):
        engine, lifecycle = self._lifecycle()
        with self.assertRaises(ValueError):
            with lifecycle.session("indexing"):
                engine.embed()
                raise ValueError("index failure")
        self.assertEqual(engine.unload_calls, 1)
        self.assertEqual(lifecycle.active_operations, 0)

    def test_concurrent_sessions_load_once_release_once(self):
        engine, lifecycle = self._lifecycle()
        barrier = threading.Barrier(3)

        def work():
            with lifecycle.session("search"):
                engine.embed()
                barrier.wait(timeout=5)

        threads = [threading.Thread(target=work) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(engine.unload_calls, 1)
        self.assertEqual(lifecycle.active_operations, 0)

    def test_late_session_cancels_pending_release(self):
        engine, lifecycle = self._lifecycle(policy="1s")
        with lifecycle.session("first"):
            engine.embed()
        self.assertEqual(engine.unload_calls, 0)  # release is only armed
        with lifecycle.session("second"):
            self.assertTrue(lifecycle.is_loaded)
            self.assertEqual(engine.unload_calls, 0)
        time.sleep(1.4)
        self.assertEqual(engine.unload_calls, 1)

    def test_always_resident_never_releases(self):
        engine, lifecycle = self._lifecycle(policy="always")
        with lifecycle.session("search"):
            engine.embed()
        self.assertEqual(engine.unload_calls, 0)
        self.assertTrue(lifecycle.is_loaded)
        self.assertEqual(lifecycle.keep_alive_value, -1)

    def test_release_skipped_while_busy(self):
        engine, lifecycle = self._lifecycle(policy="always")
        lifecycle.begin("indexing")
        engine.embed()
        self.assertFalse(lifecycle.release(reason="manual"))
        self.assertEqual(engine.unload_calls, 0)
        lifecycle.end("indexing")
        self.assertTrue(lifecycle.release(reason="manual"))

    def test_state_reports_residency(self):
        engine, lifecycle = self._lifecycle()
        state = lifecycle.state()
        self.assertEqual(state["residency"], "released")
        self.assertEqual(state["active_operations"], 0)
        with lifecycle.session("search"):
            engine.embed()
            self.assertEqual(lifecycle.state()["residency"], "loaded")


class TestLazyProbing(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_construction_issues_no_request(self, mock_urlopen):
        OllamaEmbeddingEngine(model="qwen3-embedding:0.6b")
        mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_dimension_resolved_from_metadata_without_runtime(self, mock_urlopen):
        engine = OllamaEmbeddingEngine(
            model="qwen3-embedding:0.6b", dimension_provider=lambda model: 1024
        )
        self.assertEqual(engine.dimension, 1024)
        mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_dimension_probed_once_on_first_use(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"embeddings": [[0.1] * 1024]}
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        engine = OllamaEmbeddingEngine(model="qwen3-embedding:0.6b")
        self.assertEqual(engine.dimension, 1024)
        first_call_count = mock_urlopen.call_count
        self.assertEqual(engine.dimension, 1024)
        self.assertEqual(mock_urlopen.call_count, first_call_count)

    @patch("urllib.request.urlopen")
    def test_keep_alive_sent_with_embed_request(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"embeddings": [[0.1] * 1024]}
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        engine = OllamaEmbeddingEngine(model="qwen3-embedding:0.6b")
        EmbeddingLifecycle(engine=engine, policy="30s")
        engine.encode_text("def foo(): pass")

        sent = json.loads(mock_urlopen.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(sent["keep_alive"], "35s")

    @patch("urllib.request.urlopen")
    def test_model_availability_uses_tags_only(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(
            {"models": [{"name": "qwen3-embedding:0.6b"}]}
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        engine = OllamaEmbeddingEngine(model="qwen3-embedding:0.6b")
        status = engine.ensure_model_ready()

        self.assertTrue(status["ok"])
        self.assertIsNone(status["dimension"])
        for call in mock_urlopen.call_args_list:
            url = call[0][0]
            url = url.full_url if hasattr(url, "full_url") else url
            self.assertIn("/api/tags", url)

    def test_missing_model_reports_pull_command(self):
        engine = OllamaEmbeddingEngine(model="qwen3-embedding:0.6b")
        with (
            patch.object(engine, "is_server_online", return_value=True),
            patch.object(engine, "is_model_available", return_value=False),
        ):
            status = engine.ensure_model_ready()
        self.assertFalse(status["ok"])
        self.assertEqual(status["pull_command"], "ollama pull qwen3-embedding:0.6b")


class TestDefaultModel(unittest.TestCase):
    def test_default_is_0_6b(self):
        self.assertEqual(DEFAULT_OLLAMA_EMBEDDING_MODEL, "qwen3-embedding:0.6b")
        with patch.dict("os.environ", {}, clear=True):
            engine = EmbeddingFactory.create(provider="ollama")
        self.assertEqual(engine.model, "qwen3-embedding:0.6b")

    def test_explicit_model_overrides_default(self):
        with patch.dict("os.environ", {}, clear=True):
            engine = EmbeddingFactory.create(
                provider="ollama", model="qwen3-embedding:8b"
            )
        self.assertEqual(engine.model, "qwen3-embedding:8b")

    def test_env_override(self):
        with patch.dict("os.environ", {"OLLAMA_EMBEDDING_MODEL": "bge-m3"}, clear=True):
            engine = EmbeddingFactory.create(provider="ollama")
        self.assertEqual(engine.model, "bge-m3")


class TestInstanceLock(unittest.TestCase):
    def test_second_instance_rejected(self):
        data_dir = tempfile.mkdtemp()
        first = InstanceLock(data_dir)
        first.acquire()
        second = InstanceLock(data_dir)
        with self.assertRaises(InstanceLockError):
            second.acquire()
        first.release()

    def test_lock_reusable_after_release(self):
        data_dir = tempfile.mkdtemp()
        first = InstanceLock(data_dir)
        first.acquire()
        first.release()
        second = InstanceLock(data_dir)
        self.assertTrue(second.acquire())
        second.release()

    def test_stale_lock_file_from_dead_process_is_reclaimed(self):
        data_dir = tempfile.mkdtemp()
        first = InstanceLock(data_dir)
        first.acquire()
        first.release()  # file remains, lock is gone (as after an abnormal exit)
        self.assertTrue((first.lock_path).exists())
        second = InstanceLock(data_dir)
        self.assertTrue(second.acquire())
        second.release()

    def test_allow_multi_instance_downgrades_failure(self):
        data_dir = tempfile.mkdtemp()
        first = InstanceLock(data_dir)
        first.acquire()
        second = InstanceLock(data_dir, allow_multi_instance=True)
        self.assertFalse(second.acquire())
        first.release()

    def test_same_process_shares_claim(self):
        data_dir = tempfile.mkdtemp()
        a = acquire_instance_lock(data_dir)
        b = acquire_instance_lock(data_dir)
        self.assertIs(a, b)
        b.release()
        a.release()


class TestIndexProvenance(unittest.TestCase):
    def test_record_and_match(self):
        store = IndexProvenanceStore(tempfile.mkdtemp())
        store.record("repo-a", "ollama", "qwen3-embedding:0.6b", 1024)
        self.assertTrue(store.matches("repo-a", "ollama", "qwen3-embedding:0.6b", 1024))
        self.assertFalse(store.matches("repo-a", "ollama", "qwen3-embedding:4b"))
        self.assertEqual(store.known_dimension("ollama", "qwen3-embedding:0.6b"), 1024)

    def test_stale_detection(self):
        store = IndexProvenanceStore(tempfile.mkdtemp())
        store.record("repo-a", "ollama", "qwen3-embedding:4b", 2560)
        store.record("repo-b", "ollama", "qwen3-embedding:0.6b", 1024)
        stale = store.stale_repos(
            ["repo-a", "repo-b", "repo-c"], "ollama", "qwen3-embedding:0.6b"
        )
        self.assertEqual(sorted(stale), ["repo-a", "repo-c"])


class TestServiceLifecycleIntegration(unittest.TestCase):
    def _service(self, **kwargs):
        return MultiRepoRAGService(data_dir=tempfile.mkdtemp(), **kwargs)

    @patch("urllib.request.urlopen")
    def test_service_construction_makes_no_model_request(self, mock_urlopen):
        service = self._service()
        mock_urlopen.assert_not_called()
        service.shutdown()

    def test_default_service_model_is_0_6b(self):
        with patch.dict("os.environ", {}, clear=True):
            service = self._service()
        self.assertEqual(service.embedding_model_name, "qwen3-embedding:0.6b")
        service.shutdown()

    def test_runtime_state_reports_released(self):
        service = self._service(keep_alive="0")
        state = service.embedding_runtime_state()
        self.assertEqual(state["residency"], "released")
        self.assertEqual(state["active_operations"], 0)
        self.assertEqual(state["provider"], "ollama")
        service.shutdown()

    def test_unload_reports_busy_while_operation_in_flight(self):
        service = self._service(keep_alive="always")
        service.embedding_lifecycle.begin("indexing")
        try:
            res = service.unload_models()
            self.assertTrue(res["busy"])
            self.assertFalse(res["embedding_unloaded"])
        finally:
            service.embedding_lifecycle.end("indexing")
        service.shutdown()

    def test_mismatch_triggers_reindex_and_updates_provenance(self):
        service = self._service(keep_alive="0")
        service.vector_store.list_indexed_repo_ids = lambda: ["repo-a"]
        service.provenance.record("repo-a", "ollama", "qwen3-embedding:4b", 2560)

        calls = []

        def fake_reembed(repo_id, progress_callback=None, batch_size=64):
            calls.append(repo_id)
            return {"repo_id": repo_id, "chunks": 3, "dimension": 1024}

        service.vector_store.reembed_repo = fake_reembed

        self.assertEqual(service.get_stale_repo_ids(), ["repo-a"])
        result = service.reindex_stale_repos()

        self.assertEqual(calls, ["repo-a"])
        self.assertEqual(result["reindexed"], ["repo-a"])
        entry = service.provenance.get("repo-a")
        self.assertEqual(entry["model"], service.embedding_model_name)
        self.assertEqual(entry["dimension"], 1024)
        self.assertEqual(service.get_stale_repo_ids(), [])
        service.shutdown()

    def test_matching_provenance_skips_reindex(self):
        service = self._service(keep_alive="0")
        service.vector_store.list_indexed_repo_ids = lambda: ["repo-a"]
        service.provenance.record(
            "repo-a", "ollama", service.embedding_model_name, 1024
        )
        service.vector_store.reembed_repo = lambda *a, **k: self.fail(
            "reembed must not run when provenance matches"
        )
        service.ensure_dense_index_current()
        service.shutdown()

    def test_unknown_provenance_backfilled_when_dimension_known(self):
        service = self._service(keep_alive="0")
        service.vector_store.list_indexed_repo_ids = lambda: ["repo-a", "repo-b"]
        service.provenance.record(
            "repo-b", "ollama", service.embedding_model_name, 1024
        )
        self.assertEqual(service.get_stale_repo_ids(), [])
        self.assertEqual(service.provenance.get("repo-a")["dimension"], 1024)
        service.shutdown()

    def test_search_during_rebuild_is_rejected(self):
        service = self._service(keep_alive="0")
        service.vector_store.list_indexed_repo_ids = lambda: ["repo-a"]
        service.provenance.record("repo-a", "ollama", "qwen3-embedding:4b", 2560)

        started = threading.Event()
        release = threading.Event()
        observed = {}

        def slow_reembed(repo_id, progress_callback=None, batch_size=64):
            started.set()
            release.wait(timeout=5)
            return {"repo_id": repo_id, "chunks": 1, "dimension": 1024}

        service.vector_store.reembed_repo = slow_reembed

        def rebuild():
            service.reindex_stale_repos()

        worker = threading.Thread(target=rebuild)
        worker.start()
        started.wait(timeout=5)
        try:
            with self.assertRaises(ReindexInProgressError):
                service.ensure_dense_index_current()
            observed["rejected"] = True
        finally:
            release.set()
            worker.join(timeout=5)

        self.assertTrue(observed["rejected"])
        service.shutdown()


class StubVectorEngine:
    """Deterministic engine used to verify the re-embedding pass."""

    model = "stub-embedding"

    def __init__(self, dim=8, value=0.5):
        self.dim = dim
        self.value = value
        self.calls = 0
        self.lifecycle = None

    def attach_lifecycle(self, lifecycle):
        self.lifecycle = lifecycle

    @property
    def dimension(self):
        return self.dim

    def encode_text(self, text, field_type="general"):
        self.calls += 1
        return [self.value] * self.dim

    def unload_model(self):
        return True


class TestReembedPass(unittest.TestCase):
    def _chunk(self, chunk_id):
        from src.models.schema import CodeChunk, ChunkType

        return CodeChunk(
            chunk_id=chunk_id,
            repo_id="repo-a",
            file_path="src/app.py",
            language="python",
            start_line=1,
            end_line=5,
            raw_content="def handler():\n    return 1",
            enriched_content="def handler():\n    return 1",
            symbol_name="handler",
            chunk_type=ChunkType.FUNCTION,
            parent_symbol=None,
            imports=[],
            docstring=None,
        )

    def test_reembed_rewrites_vectors_and_preserves_chunks(self):
        from src.indexer.vector_store import VectorStore

        data_dir = tempfile.mkdtemp()
        old_engine = StubVectorEngine(dim=4, value=0.25)
        store = VectorStore(data_dir=data_dir, embedding_engine=old_engine)
        store.add_chunks([self._chunk("c1"), self._chunk("c2")])

        new_engine = StubVectorEngine(dim=8, value=0.75)
        store.embedding_engine = new_engine
        progress = []
        result = store.reembed_repo(
            "repo-a",
            progress_callback=lambda c, t, msg, phase: progress.append((c, t, phase)),
        )

        self.assertEqual(result["chunks"], 2)
        self.assertEqual(result["dimension"], 8)
        self.assertEqual(store.count_chunks("repo-a"), 2)
        self.assertTrue(progress)
        self.assertEqual(progress[-1][2], "reembed")

        chunk = store.get_chunk("c1")
        self.assertIsNotNone(chunk)
        self.assertEqual(chunk.symbol_name, "handler")
        self.assertEqual(store.list_indexed_repo_ids(), ["repo-a"])

    def test_reindex_is_resumable_per_repository(self):
        service = MultiRepoRAGService(data_dir=tempfile.mkdtemp(), keep_alive="0")
        service.vector_store.list_indexed_repo_ids = lambda: ["repo-a", "repo-b"]
        service.provenance.record("repo-a", "ollama", "qwen3-embedding:4b", 2560)
        service.provenance.record("repo-b", "ollama", "qwen3-embedding:4b", 2560)

        done = []

        def flaky_reembed(repo_id, progress_callback=None, batch_size=64):
            if repo_id == "repo-b" and "repo-b" not in done:
                done.append("repo-b")
                raise RuntimeError("interrupted")
            return {"repo_id": repo_id, "chunks": 1, "dimension": 1024}

        service.vector_store.reembed_repo = flaky_reembed

        with self.assertRaises(RuntimeError):
            service.reindex_stale_repos()

        # repo-a completed and is no longer stale; repo-b is retried.
        self.assertEqual(service.get_stale_repo_ids(), ["repo-b"])
        result = service.reindex_stale_repos()
        self.assertEqual(result["reindexed"], ["repo-b"])
        self.assertEqual(service.get_stale_repo_ids(), [])
        service.shutdown()


class TestConcurrentSearchResidency(unittest.TestCase):
    def test_concurrent_searches_load_once_release_once(self):
        service = MultiRepoRAGService(data_dir=tempfile.mkdtemp(), keep_alive="0")
        engine = FakeEngine()
        service.embedding_lifecycle.attach(engine)

        barrier = threading.Barrier(4)

        def fake_search(query, repo_ids=None, expanded_repos=None, top_k=8):
            engine.embed()
            barrier.wait(timeout=5)
            return []

        service.retriever.search = fake_search
        service.resolve_scope = lambda **kwargs: ResolvedScopeStub()

        threads = [
            threading.Thread(target=lambda: service.search("query")) for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(engine.unload_calls, 1)
        self.assertEqual(service.embedding_lifecycle.active_operations, 0)
        service.shutdown()


class ResolvedScopeStub:
    is_empty = False
    all_repo_ids = ["repo-a"]
    expanded = {}
    primary = ["repo-a"]


if __name__ == "__main__":
    unittest.main()
