"""
Embedding model residency lifecycle.

Owns when the local embedding model is resident in the model runtime (VRAM/RAM).
The model is loaded on demand by the first embedding request of an operation and
released once the last in-flight operation finishes and the idle grace elapses.
"""

import os
import time
import logging
import threading
import contextlib
from typing import Any, Dict, Optional

logger = logging.getLogger("rag.lifecycle")

DEFAULT_IDLE_GRACE_SECONDS = 30.0

MODE_ON_DEMAND = "on-demand"
MODE_IMMEDIATE = "immediate"
MODE_ALWAYS = "always-resident"


class KeepAlivePolicy:
    """
    Parsed residency policy.

    mode:
      - "immediate":     release as soon as the last operation ends (grace 0)
      - "on-demand":     release after `idle_grace_seconds` of inactivity (default)
      - "always-resident": never auto-release
    """

    def __init__(
        self,
        mode: str = MODE_ON_DEMAND,
        idle_grace_seconds: float = DEFAULT_IDLE_GRACE_SECONDS,
    ):
        self.mode = mode
        self.idle_grace_seconds = max(0.0, float(idle_grace_seconds))

    @property
    def always_resident(self) -> bool:
        return self.mode == MODE_ALWAYS

    @property
    def keep_alive_value(self) -> Any:
        """Value sent as `keep_alive` on model runtime requests."""
        if self.always_resident:
            return -1
        # Backstop: even if this process dies without releasing, the runtime
        # evicts the model shortly after the idle grace.
        return f"{int(self.idle_grace_seconds) + 5}s"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "idle_grace_seconds": self.idle_grace_seconds,
            "keep_alive": self.keep_alive_value,
        }

    @staticmethod
    def parse(value: Optional[Any] = None) -> "KeepAlivePolicy":
        """
        Parses `EMBEDDING_KEEP_ALIVE`-style values:
          None / ""            -> on-demand with the default idle grace
          "0" / "none"         -> immediate release
          "-1" / "always"      -> always resident
          "45" / "45s" / "5m"  -> on-demand with that idle grace
        """
        if isinstance(value, KeepAlivePolicy):
            return value

        raw = value if value is not None else os.environ.get("EMBEDDING_KEEP_ALIVE")
        if raw is None or str(raw).strip() == "":
            return KeepAlivePolicy(MODE_ON_DEMAND, DEFAULT_IDLE_GRACE_SECONDS)

        text = str(raw).strip().lower()
        if text in ("always", "resident", "always-resident", "-1", "forever"):
            return KeepAlivePolicy(MODE_ALWAYS, DEFAULT_IDLE_GRACE_SECONDS)
        if text in ("0", "none", "immediate", "off"):
            return KeepAlivePolicy(MODE_IMMEDIATE, 0.0)

        seconds = _parse_duration_seconds(text)
        if seconds is None:
            logger.warning(
                f"Unrecognized EMBEDDING_KEEP_ALIVE value '{raw}', using default on-demand policy."
            )
            return KeepAlivePolicy(MODE_ON_DEMAND, DEFAULT_IDLE_GRACE_SECONDS)
        if seconds <= 0:
            return KeepAlivePolicy(MODE_IMMEDIATE, 0.0)
        return KeepAlivePolicy(MODE_ON_DEMAND, seconds)


def _parse_duration_seconds(text: str) -> Optional[float]:
    units = {"s": 1.0, "m": 60.0, "h": 3600.0}
    try:
        if text[-1] in units:
            return float(text[:-1]) * units[text[-1]]
        return float(text)
    except (ValueError, IndexError):
        return None


class EmbeddingLifecycle:
    """
    Reference-counted residency controller for a single embedding engine.

    Operations (indexing runs, searches, RAG queries) wrap themselves in
    `session()`. Overlapping sessions share one load and produce exactly one
    release after the last one exits.
    """

    def __init__(
        self,
        engine: Optional[Any] = None,
        policy: Optional[Any] = None,
    ):
        self.policy = KeepAlivePolicy.parse(policy)
        self._lock = threading.RLock()
        self._active = 0
        self._timer: Optional[threading.Timer] = None
        self._loaded = False
        self._last_load_seconds: Optional[float] = None
        self._last_reason: Optional[str] = None
        self._engine = None
        if engine is not None:
            self.attach(engine)

    def attach(self, engine: Any):
        """Binds an embedding engine and lets it report/consult residency state."""
        self._engine = engine
        if hasattr(engine, "attach_lifecycle"):
            engine.attach_lifecycle(self)

    @property
    def engine(self) -> Optional[Any]:
        return self._engine

    @property
    def keep_alive_value(self) -> Any:
        return self.policy.keep_alive_value

    @property
    def active_operations(self) -> int:
        with self._lock:
            return self._active

    @property
    def is_busy(self) -> bool:
        return self.active_operations > 0

    @property
    def is_loaded(self) -> bool:
        with self._lock:
            return self._loaded

    def note_loaded(self, elapsed_seconds: Optional[float] = None):
        """Called by the engine after a successful model runtime request."""
        with self._lock:
            if not self._loaded:
                self._loaded = True
                self._last_load_seconds = elapsed_seconds
                logger.info(
                    f"Embedding model loaded (trigger={self._last_reason or 'embedding'}, "
                    f"elapsed={round(elapsed_seconds, 3) if elapsed_seconds else 'n/a'}s)"
                )

    def note_unloaded(self):
        with self._lock:
            self._loaded = False

    @contextlib.contextmanager
    def session(self, reason: str = "embedding"):
        """Context manager holding the model resident for one operation."""
        self.begin(reason)
        try:
            yield self
        finally:
            self.end(reason)

    def begin(self, reason: str = "embedding"):
        with self._lock:
            self._cancel_timer()
            self._active += 1
            self._last_reason = reason
            logger.debug(
                f"Embedding session begin (reason={reason}, active={self._active})"
            )

    def end(self, reason: str = "embedding"):
        release_now = False
        with self._lock:
            self._active = max(0, self._active - 1)
            logger.debug(
                f"Embedding session end (reason={reason}, active={self._active})"
            )
            if self._active > 0 or self.policy.always_resident:
                return
            if self.policy.idle_grace_seconds <= 0:
                release_now = True
            else:
                self._arm_timer(reason)
        if release_now:
            self.release(reason=f"idle-after:{reason}")

    def _arm_timer(self, reason: str):
        self._cancel_timer()
        timer = threading.Timer(
            self.policy.idle_grace_seconds,
            self._on_idle_timeout,
            kwargs={"reason": f"idle-after:{reason}"},
        )
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _cancel_timer(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _on_idle_timeout(self, reason: str):
        with self._lock:
            self._timer = None
            if self._active > 0:
                return
        self.release(reason=reason)

    def release(self, reason: str = "manual", force: bool = False) -> bool:
        """
        Releases the model from the runtime. Returns False when an operation is
        in flight (the in-flight operation is never interrupted) unless `force`.
        """
        with self._lock:
            if self._active > 0 and not force:
                logger.debug(
                    f"Skipping embedding model release (reason={reason}): {self._active} operation(s) in flight"
                )
                return False
            self._cancel_timer()
            engine = self._engine
            was_loaded = self._loaded
            self._loaded = False

        if engine is None or not hasattr(engine, "unload_model"):
            return False

        if not was_loaded and not force:
            # Nothing was ever loaded in this window; no runtime call needed.
            logger.debug(f"Embedding model already released (reason={reason})")
            return False

        t0 = time.time()
        ok = False
        try:
            ok = bool(engine.unload_model())
        except Exception as ex:
            logger.debug(f"Embedding model release failed: {ex}")
            ok = False

        if was_loaded or ok:
            logger.info(
                f"Embedding model released (reason={reason}, elapsed={round(time.time() - t0, 3)}s, ok={ok})"
            )
        return ok

    def shutdown(self, release: bool = True):
        """Cancels pending timers and optionally releases the model."""
        with self._lock:
            self._cancel_timer()
        if release:
            self.release(reason="shutdown", force=True)

    def state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "residency": "loaded" if self._loaded else "released",
                "active_operations": self._active,
                "model": getattr(self._engine, "model", None),
                "policy": self.policy.to_dict(),
                "last_load_seconds": self._last_load_seconds,
                "release_pending": self._timer is not None,
            }
