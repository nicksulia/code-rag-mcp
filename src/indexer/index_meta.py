"""
Dense index embedding provenance.

Records which embedding provider, model, and vector dimension produced the
dense vectors of each repository so that a model change can be detected and
repaired by an automatic re-embedding pass.
"""

import json
import time
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("rag.provenance")

MANIFEST_FILENAME = "index_meta.json"


class IndexProvenanceStore:
    """JSON manifest of per-repository embedding provenance."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / MANIFEST_FILENAME
        self._lock = threading.RLock()

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "repos": {}}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.loads(f.read() or "{}")
            data.setdefault("version", 1)
            data.setdefault("repos", {})
            return data
        except Exception as ex:
            logger.warning(f"Could not read index provenance manifest: {ex}")
            return {"version": 1, "repos": {}}

    def _save(self, data: Dict[str, Any]):
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2))
        tmp.replace(self.path)

    def get(self, repo_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._load()["repos"].get(repo_id)

    def all(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return dict(self._load()["repos"])

    def record(
        self,
        repo_id: str,
        provider: str,
        model: Optional[str],
        dimension: Optional[int],
    ):
        with self._lock:
            data = self._load()
            data["repos"][repo_id] = {
                "provider": provider,
                "model": model,
                "dimension": dimension,
                "embedded_at": time.time(),
            }
            self._save(data)

    def delete(self, repo_id: str):
        with self._lock:
            data = self._load()
            if data["repos"].pop(repo_id, None) is not None:
                self._save(data)

    def known_dimension(self, provider: str, model: Optional[str]) -> Optional[int]:
        """Dimension previously recorded for this provider+model, if any."""
        for entry in self.all().values():
            if entry.get("provider") == provider and entry.get("model") == model:
                dim = entry.get("dimension")
                if dim:
                    return int(dim)
        return None

    def matches(
        self,
        repo_id: str,
        provider: str,
        model: Optional[str],
        dimension: Optional[int] = None,
    ) -> bool:
        entry = self.get(repo_id)
        if not entry:
            return False
        if entry.get("provider") != provider or entry.get("model") != model:
            return False
        if dimension is not None and entry.get("dimension") not in (None, dimension):
            return False
        return True

    def stale_repos(
        self,
        repo_ids: List[str],
        provider: str,
        model: Optional[str],
        dimension: Optional[int] = None,
    ) -> List[str]:
        return [
            rid for rid in repo_ids if not self.matches(rid, provider, model, dimension)
        ]
