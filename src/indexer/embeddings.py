import os
import re
import math
import json
import time
import logging
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any, Tuple

logger = logging.getLogger("rag.embeddings")

DEFAULT_OLLAMA_EMBEDDING_MODEL = "qwen3-embedding:0.6b"


class BaseEmbeddingEngine(ABC):
    lifecycle: Any = None

    def attach_lifecycle(self, lifecycle: Any):
        """Binds a residency controller. Engines without a runtime ignore it."""
        self.lifecycle = lifecycle

    @property
    @abstractmethod
    def dimension(self) -> int:
        pass

    def unload_model(self) -> bool:
        """Unload model from memory/VRAM if applicable."""
        return True

    @abstractmethod
    def encode_text(self, text: str, field_type: str = "general") -> List[float]:
        pass

    def encode_batch(
        self, texts: List[str], field_type: str = "general"
    ) -> List[List[float]]:
        return [self.encode_text(t, field_type) for t in texts]


class AdvancedSubwordNeuralEngine(BaseEmbeddingEngine):
    """
    High-dimensional (dim=384) Positional Syntax-Weighted Subword Feature Encoder.
    Applies syntactic token tagging (symbols, types, keywords, docstrings), subword splitting,
    character n-gram feature buckets, log-frequency damping, and L2 unit-sphere projection.
    """

    def __init__(self, dim: int = 384):
        self._dim = dim
        self._code_keywords = {
            "function",
            "def",
            "async",
            "class",
            "interface",
            "struct",
            "enum",
            "type",
            "import",
            "from",
            "export",
            "return",
            "const",
            "let",
            "var",
            "public",
            "private",
            "get",
            "post",
            "put",
            "delete",
            "route",
            "endpoint",
            "api",
            "jwt",
            "auth",
            "token",
        }

    @property
    def dimension(self) -> int:
        return self._dim

    def _tokenize_with_syntax_weights(
        self, text: str, field_type: str
    ) -> List[Tuple[str, float]]:
        field_mult = {
            "symbol": 3.5,
            "signature": 2.5,
            "docstring": 2.0,
            "body": 1.0,
            "general": 1.5,
        }.get(field_type, 1.5)

        raw_words = re.findall(r"[A-Za-z0-9_]+|[^\s\w]", text)
        weighted_tokens: List[Tuple[str, float]] = []

        for idx, word in enumerate(raw_words):
            word_lower = word.lower()
            pos_factor = max(0.5, 1.0 - (idx / 400.0))

            weight = field_mult * pos_factor
            if word_lower in self._code_keywords:
                weight *= 2.2
            elif word.isupper() and len(word) > 1:
                weight *= 2.0
            elif any(c.isupper() for c in word[1:]):
                weight *= 2.5

            weighted_tokens.append((word_lower, weight))

            subparts = re.findall(r"[a-z]+|[0-9]+", word_lower)
            if len(subparts) > 1:
                for sub in subparts:
                    weighted_tokens.append((sub, weight * 1.3))

            if len(word_lower) >= 3:
                for i in range(len(word_lower) - 2):
                    tri = word_lower[i : i + 3]
                    weighted_tokens.append((f"c3_{tri}", weight * 0.7))
            if len(word_lower) >= 4:
                for i in range(len(word_lower) - 3):
                    quad = word_lower[i : i + 4]
                    weighted_tokens.append((f"c4_{quad}", weight * 0.5))

        return weighted_tokens

    def encode_text(self, text: str, field_type: str = "general") -> List[float]:
        vec = [0.0] * self._dim
        if not text or not text.strip():
            return vec

        weighted_tokens = self._tokenize_with_syntax_weights(text, field_type)
        if not weighted_tokens:
            return vec

        for tok, weight in weighted_tokens:
            h1 = hash(tok) % self._dim
            h2 = hash(f"rot_{tok}") % self._dim
            vec[h1] += weight * 0.75
            vec[h2] += weight * 0.25

        norm_sq = 0.0
        for i in range(self._dim):
            if vec[i] > 0:
                vec[i] = 1.0 + math.log(vec[i])
                norm_sq += vec[i] * vec[i]

        norm = math.sqrt(norm_sq)
        if norm > 0:
            for i in range(self._dim):
                vec[i] /= norm

        return vec


class OllamaEmbeddingEngine(BaseEmbeddingEngine):
    """
    Local Ollama Embedding Engine supporting Qwen3-Embedding-4B, Qwen3-Embedding-8B, Qwen2.5-Coder, etc.
    Connects to Ollama REST server on http://localhost:11434 with dynamic dimension probing
    and model presence check / auto-pull capabilities.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        host: Optional[str] = None,
        fallback_dim: int = 384,
        dimension_provider: Optional[Any] = None,
    ):
        self.model = (
            model
            or os.environ.get("OLLAMA_EMBEDDING_MODEL")
            or DEFAULT_OLLAMA_EMBEDDING_MODEL
        )
        self.host = (
            host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
        ).rstrip("/")
        self._dim = fallback_dim
        self._dim_resolved = False
        self._dimension_provider = dimension_provider
        self._fallback = AdvancedSubwordNeuralEngine(dim=fallback_dim)
        self.lifecycle = None
        # NOTE: no dimension probe here - constructing the engine must never
        # pull the model into memory. Dimension is resolved lazily.

    def attach_lifecycle(self, lifecycle: Any):
        """Binds the residency controller that supplies keep_alive and load state."""
        self.lifecycle = lifecycle

    def set_dimension_provider(self, provider: Optional[Any]):
        """Sets a callable returning a persisted dimension for the active model."""
        self._dimension_provider = provider

    def _keep_alive_value(self) -> Any:
        if self.lifecycle is not None:
            return self.lifecycle.keep_alive_value
        return None

    def _with_keep_alive(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        keep_alive = self._keep_alive_value()
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        return payload

    def _note_loaded(self, elapsed_seconds: Optional[float] = None):
        if self.lifecycle is not None:
            self.lifecycle.note_loaded(elapsed_seconds)

    def is_server_online(self) -> bool:
        """Checks if Ollama REST server is responding."""
        try:
            req = urllib.request.Request(f"{self.host}/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def list_local_models(self) -> List[str]:
        """Returns list of all models currently downloaded in Ollama."""
        try:
            req = urllib.request.Request(f"{self.host}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = data.get("models", [])
                return [m.get("name", "") for m in models]
        except Exception:
            return []

    def is_model_available(self, model_name: Optional[str] = None) -> bool:
        """Checks if target model is present in Ollama."""
        target = model_name or self.model
        clean_target = target.split(":")[0].lower()
        local_models = self.list_local_models()
        for m in local_models:
            m_clean = m.split(":")[0].lower()
            if (
                target.lower() in m.lower()
                or m.lower() in target.lower()
                or clean_target == m_clean
            ):
                return True
        return False

    def pull_model(
        self, model_name: Optional[str] = None, progress_callback: Optional[Any] = None
    ) -> bool:
        """
        Pulls model from Ollama library with streaming progress reporting.
        """
        target = model_name or self.model
        url = f"{self.host}/api/pull"
        payload = {"name": target, "stream": True}
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3600) as resp:
                for line in resp:
                    if line:
                        chunk = json.loads(line.decode("utf-8"))
                        status = chunk.get("status", "")
                        completed = chunk.get("completed", 0)
                        total = chunk.get("total", 0)
                        if progress_callback:
                            progress_callback(status, completed, total)
                        if status == "success":
                            return True
            return True
        except Exception as e:
            if progress_callback:
                progress_callback(f"Error pulling model: {e}", 0, 0)
            return False

    def ensure_model_ready(
        self, auto_pull: bool = False, prompt_user: bool = True
    ) -> Dict[str, Any]:
        """
        Inspects Ollama health and model availability.
        Optionally prompts or auto-pulls if absent.
        """
        if not self.is_server_online():
            return {
                "ok": False,
                "reason": f"Ollama server is not running on {self.host}. Please start it with `ollama serve`.",
            }

        if self.is_model_available():
            # Availability is decided from /api/tags only; the model is not
            # loaded and the dimension is not probed here.
            return {
                "ok": True,
                "model": self.model,
                "dimension": self._dim if self._dim_resolved else None,
                "status": "ready",
            }

        return {
            "ok": False,
            "reason": (
                f"Model '{self.model}' is not in local Ollama library. "
                f"Run `ollama pull {self.model}` to install it."
            ),
            "pull_command": f"ollama pull {self.model}",
            "can_pull": True,
            "model": self.model,
        }

    def _probe_dimension(self):
        """Probes Ollama once to detect model dimension (loads the model)."""
        try:
            sample_vec = self._fetch_ollama_embedding("test probe")
            if sample_vec and len(sample_vec) > 0:
                self._dim = len(sample_vec)
                self._dim_resolved = True
        except Exception:
            pass

    @property
    def dimension(self) -> int:
        """
        Lazily resolved vector dimension:
        1. cached value from a previous resolution
        2. persisted index provenance for this provider+model
        3. a one-shot probe against the model runtime
        """
        if self._dim_resolved:
            return self._dim

        if self._dimension_provider is not None:
            try:
                recorded = self._dimension_provider(self.model)
            except Exception:
                recorded = None
            if recorded:
                self._dim = int(recorded)
                self._dim_resolved = True
                logger.debug(
                    f"Embedding dimension for '{self.model}' resolved from index metadata: {self._dim}"
                )
                return self._dim

        self._probe_dimension()
        return self._dim

    def unload_model(self) -> bool:
        """
        Unloads embedding model from Ollama memory (GPU VRAM / RAM) by sending keep_alive: 0.
        """
        logger.info(f"Unloading Ollama embedding model '{self.model}' from memory...")
        try:
            url = f"{self.host}/api/generate"
            payload = {"model": self.model, "keep_alive": 0}
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    logger.info(f"Successfully unloaded Ollama model '{self.model}'.")
                    if self.lifecycle is not None:
                        self.lifecycle.note_unloaded()
                    return True
        except Exception as e:
            logger.debug(f"Ollama unload request for '{self.model}' returned: {e}")
        return False

    def _fetch_ollama_embedding(self, text: str) -> Optional[List[float]]:
        # 1. Try /api/embed (Ollama v0.1.30+)
        t0 = time.time()
        try:
            url = f"{self.host}/api/embed"
            payload = self._with_keep_alive({"model": self.model, "input": text[:4000]})
            logger.debug(
                f"Calling Ollama /api/embed: model={self.model}, input_len={len(text[:4000])}"
            )
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                embeddings = data.get("embeddings")
                if embeddings and len(embeddings) > 0:
                    dur = round((time.time() - t0) * 1000, 1)
                    dim = len(embeddings[0])
                    logger.debug(
                        f"Ollama /api/embed success: dim={dim}, latency={dur}ms"
                    )
                    self._note_loaded(time.time() - t0)
                    return embeddings[0]
        except urllib.error.HTTPError as he:
            logger.debug(f"Ollama /api/embed returned HTTP {he.code}: {he.reason}")
            if he.code != 404:
                raise he
        except Exception as ex:
            logger.debug(f"Ollama /api/embed connection error: {ex}")

        # 2. Fallback to /api/embeddings (older endpoint)
        try:
            url = f"{self.host}/api/embeddings"
            payload = self._with_keep_alive(
                {"model": self.model, "prompt": text[:4000]}
            )
            logger.debug(f"Falling back to Ollama /api/embeddings: model={self.model}")
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                emb = data.get("embedding")
                if emb:
                    dur = round((time.time() - t0) * 1000, 1)
                    logger.debug(
                        f"Ollama /api/embeddings success: dim={len(emb)}, latency={dur}ms"
                    )
                    self._note_loaded(time.time() - t0)
                    return emb
        except Exception as e:
            logger.warning(f"Ollama embedding request failed: {e}")
            raise RuntimeError(
                f"Failed to generate embedding with Ollama model '{self.model}' at {self.host}. "
                f"Make sure Ollama is running and run `ollama pull {self.model}`. Error: {str(e)}"
            )

        return None

    def encode_text(self, text: str, field_type: str = "general") -> List[float]:
        try:
            vec = self._fetch_ollama_embedding(text)
            if vec:
                if len(vec) != self._dim:
                    logger.info(
                        f"Ollama detected new vector dimension: {len(vec)} (previous was {self._dim})"
                    )
                    self._dim = len(vec)
                self._dim_resolved = True
                return vec
        except Exception as ex:
            logger.debug(
                f"Ollama encode_text failed, using offline subword fallback: {ex}"
            )
            return self._fallback.encode_text(text, field_type)
        return self._fallback.encode_text(text, field_type)

    def encode_batch(
        self, texts: List[str], field_type: str = "general"
    ) -> List[List[float]]:
        if not texts:
            return []

        t0 = time.time()
        # Batch request via /api/embed if possible
        try:
            url = f"{self.host}/api/embed"
            payload = self._with_keep_alive(
                {"model": self.model, "input": [t[:4000] for t in texts]}
            )
            logger.debug(
                f"Sending batch embed request to Ollama: count={len(texts)}, model={self.model}"
            )
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                embs = data.get("embeddings")
                if embs and len(embs) == len(texts):
                    dur = round((time.time() - t0) * 1000, 1)
                    logger.debug(
                        f"Ollama batch embed returned {len(embs)} vectors in {dur}ms"
                    )
                    self._note_loaded(time.time() - t0)
                    if embs and len(embs[0]) != self._dim:
                        self._dim = len(embs[0])
                    self._dim_resolved = True
                    return embs
        except Exception as ex:
            logger.debug(
                f"Ollama batch request failed ({ex}), falling back to individual encoding"
            )

        return [self.encode_text(t, field_type) for t in texts]


class GeminiEmbeddingEngine(BaseEmbeddingEngine):
    """Google Gemini Remote Embedding Provider (text-embedding-004, dim=768)"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self._fallback = AdvancedSubwordNeuralEngine(dim=768)

    @property
    def dimension(self) -> int:
        return 768

    def encode_text(self, text: str, field_type: str = "general") -> List[float]:
        if not self.api_key:
            return self._fallback.encode_text(text, field_type)
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={self.api_key}"
            payload = {
                "model": "models/text-embedding-004",
                "content": {"parts": [{"text": text[:2048]}]},
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["embedding"]["values"]
        except Exception:
            return self._fallback.encode_text(text, field_type)


class OpenAIEmbeddingEngine(BaseEmbeddingEngine):
    """OpenAI Remote Embedding Provider (text-embedding-3-small, dim=1536)"""

    def __init__(
        self, api_key: Optional[str] = None, model: str = "text-embedding-3-small"
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model
        self._dim = 1536
        self._fallback = AdvancedSubwordNeuralEngine(dim=self._dim)

    @property
    def dimension(self) -> int:
        return self._dim

    def encode_text(self, text: str, field_type: str = "general") -> List[float]:
        if not self.api_key:
            return self._fallback.encode_text(text, field_type)
        try:
            url = "https://api.openai.com/v1/embeddings"
            payload = {"input": text[:4000], "model": self.model}
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["data"][0]["embedding"]
        except Exception:
            return self._fallback.encode_text(text, field_type)


class UnslothEmbeddingEngine(BaseEmbeddingEngine):
    """
    Unsloth / vLLM / OpenAI-compatible Local Inference Server Embedding Engine.
    Connects to http://localhost:8000/v1/embeddings (or custom base_url).
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        fallback_dim: int = 4096,
    ):
        self.base_url = (
            base_url
            or os.environ.get("UNSLOTH_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "http://localhost:8000/v1"
        ).rstrip("/")
        self.model = (
            model or os.environ.get("UNSLOTH_EMBEDDING_MODEL") or "qwen3-embedding:8b"
        )
        self.api_key = api_key or os.environ.get("UNSLOTH_API_KEY") or "EMPTY"
        self._dim = fallback_dim
        self._dim_resolved = False
        self._fallback = AdvancedSubwordNeuralEngine(dim=fallback_dim)
        # Dimension is probed lazily; construction never loads the model.

    def _probe_dimension(self):
        try:
            sample = self.encode_text("test probe")
            if sample and len(sample) > 0:
                self._dim = len(sample)
                self._dim_resolved = True
        except Exception:
            pass

    @property
    def dimension(self) -> int:
        if not self._dim_resolved:
            self._probe_dimension()
        return self._dim

    def encode_text(self, text: str, field_type: str = "general") -> List[float]:
        try:
            url = f"{self.base_url}/embeddings"
            payload = {"input": text[:4000], "model": self.model}
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                vec = data["data"][0]["embedding"]
                if len(vec) != self._dim:
                    self._dim = len(vec)
                self._dim_resolved = True
                return vec
        except Exception:
            return self._fallback.encode_text(text, field_type)

    def encode_batch(
        self, texts: List[str], field_type: str = "general"
    ) -> List[List[float]]:
        if not texts:
            return []
        try:
            url = f"{self.base_url}/embeddings"
            payload = {"input": [t[:4000] for t in texts], "model": self.model}
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data.get("data", [])
                if len(items) == len(texts):
                    return [item["embedding"] for item in items]
        except Exception:
            pass
        return [self.encode_text(t, field_type) for t in texts]


class EmbeddingFactory:
    @staticmethod
    def create(
        provider: Optional[str] = None,
        model: Optional[str] = None,
        host: Optional[str] = None,
        **kwargs,
    ) -> BaseEmbeddingEngine:
        prov = (provider or os.environ.get("EMBEDDING_PROVIDER") or "ollama").lower()
        if prov in ("unsloth", "vllm", "openai-compatible"):
            return UnslothEmbeddingEngine(base_url=host, model=model, **kwargs)
        elif prov == "ollama":
            # model=None lets the engine resolve $OLLAMA_EMBEDDING_MODEL, then the default.
            return OllamaEmbeddingEngine(model=model, host=host)
        elif prov in ("gemini", "google") and os.environ.get("GEMINI_API_KEY"):
            return GeminiEmbeddingEngine(**kwargs)
        elif prov in ("openai", "gpt") and os.environ.get("OPENAI_API_KEY"):
            return OpenAIEmbeddingEngine(**kwargs)
        else:
            dim = kwargs.get("dim", 384)
            return AdvancedSubwordNeuralEngine(dim=dim)
