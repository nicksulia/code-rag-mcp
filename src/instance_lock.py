"""
Single-instance enforcement for the RAG engine.

Only one engine instance per data directory may own the embedding model
lifecycle. Ownership is an exclusive `flock` on a lock file inside the data
directory, so the kernel releases it automatically if a process dies.
"""

import os
import json
import time
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None

logger = logging.getLogger("rag.instance")

LOCK_FILENAME = ".rag-instance.lock"

# flock is owned by the open file description, so a second lock attempt from the
# same process would fail. Track in-process owners and share them by data dir.
_process_locks: Dict[str, "InstanceLock"] = {}
_process_locks_guard = threading.RLock()


class InstanceLockError(RuntimeError):
    """Raised when another engine instance already owns the data directory."""


class InstanceLock:
    """Exclusive, per-data-directory ownership claim."""

    def __init__(self, data_dir: str, allow_multi_instance: bool = False):
        self.data_dir = str(Path(data_dir).resolve())
        self.allow_multi_instance = allow_multi_instance
        self.lock_path = Path(self.data_dir) / LOCK_FILENAME
        self._fh = None
        self._refs = 0
        self.acquired = False

    def _read_owner(self) -> Dict[str, Any]:
        try:
            with open(self.lock_path, "r", encoding="utf-8") as f:
                return json.loads(f.read() or "{}")
        except Exception:
            return {}

    def acquire(self) -> bool:
        if self.acquired:
            self._refs += 1
            return True

        if fcntl is None:
            logger.warning(
                "File locking is unavailable on this platform; single-instance enforcement is disabled."
            )
            self.acquired = True
            self._refs = 1
            return True

        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        fh = open(self.lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            owner = self._read_owner()
            fh.close()
            message = (
                f"Another Multi-Repo Code RAG instance (pid {owner.get('pid', 'unknown')}, "
                f"started {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(owner.get('started_at', time.time())))}) "
                f"already owns the data directory '{self.data_dir}'. "
                f"Stop it first, or pass --allow-multi-instance to bypass this check."
            )
            if self.allow_multi_instance:
                logger.warning(message)
                self.acquired = False
                return False
            raise InstanceLockError(message)

        fh.seek(0)
        fh.truncate()
        fh.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "started_at": time.time(),
                    "data_dir": self.data_dir,
                }
            )
        )
        fh.flush()
        self._fh = fh
        self.acquired = True
        self._refs = 1
        logger.debug(
            f"Acquired instance lock for '{self.data_dir}' (pid {os.getpid()})"
        )
        return True

    def release(self):
        if not self.acquired:
            return
        self._refs -= 1
        if self._refs > 0:
            return
        if self._fh is not None and fcntl is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                self._fh.close()
            except Exception:
                pass
        self._fh = None
        self.acquired = False
        with _process_locks_guard:
            if _process_locks.get(self.data_dir) is self:
                _process_locks.pop(self.data_dir, None)
        logger.debug(f"Released instance lock for '{self.data_dir}'")

    def owner_info(self) -> Dict[str, Any]:
        return self._read_owner()


def acquire_instance_lock(
    data_dir: str, allow_multi_instance: bool = False
) -> Optional[InstanceLock]:
    """
    Claims ownership of `data_dir` for this process.

    Repeated calls within the same process share one claim (the CLI and the MCP
    server both build a service against the same data directory).
    """
    resolved = str(Path(data_dir).resolve())
    with _process_locks_guard:
        existing = _process_locks.get(resolved)
        if existing is not None and existing.acquired:
            existing.acquire()
            return existing

        lock = InstanceLock(resolved, allow_multi_instance=allow_multi_instance)
        acquired = lock.acquire()
        if acquired:
            _process_locks[resolved] = lock
            return lock
        return None
